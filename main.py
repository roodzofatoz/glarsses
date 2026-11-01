"""
glARsses - Decky Loader backend.

Talks to the XRLinuxDriver (installed by the XR Gaming plugin / Breezy Vulkan) through:
  * ~/.config/xr_driver/config.ini   (persistent settings, the driver watches this file)
  * /dev/shm/xr_driver_control       (one-shot control flags: recenter, sbs, ...)
  * /dev/shm/xr_driver_state         (read-only state written by the driver)

The config file is updated with a read-modify-write that preserves every key we
don't touch, so it coexists with the XR Gaming plugin.
"""
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time

import decky

CONTROL_FLAGS_PATH = "/dev/shm/xr_driver_control"
STATE_PATH = "/dev/shm/xr_driver_state"
CONFIG_PATH = os.path.join(decky.DECKY_USER_HOME, ".config", "xr_driver", "config.ini")
BREEZY_MANIFEST = os.path.join(decky.DECKY_USER_HOME, ".local", "share", "breezy_vulkan", "manifest")
STORE_PATH = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "glarsses.json")
# settings saved before the plugin was renamed from "AR Glasses" (decky-ar-glasses)
OLD_STORE_PATH = os.path.join(os.path.dirname(decky.DECKY_PLUGIN_SETTINGS_DIR), "decky-ar-glasses", "ar_glasses.json")

ALLOWED_CONTROL_FLAGS = {
    "recenter_screen", "recalibrate", "sbs_mode", "refresh_device_license",
    "enable_breezy_desktop_smooth_follow", "toggle_breezy_desktop_smooth_follow",
    "breezy_desktop_display_distance", "breezy_desktop_follow_threshold",
    "request_features",
}

# key -> (type, default). Only these keys may be written by the frontend.
CONFIG_SCHEMA = {
    "disabled": (bool, True),
    "output_mode": (str, "mouse"),
    "external_mode": (str, "none"),
    "display_size": (float, 1.0),
    "display_distance": (float, 1.0),
    "sideview_position": (str, "middle_center"),
    "virtual_display_smooth_follow_enabled": (bool, False),
    "sideview_smooth_follow_enabled": (bool, False),
    "sideview_follow_threshold": (float, 0.5),
    "curved_display": (bool, False),
    "multi_tap_enabled": (bool, False),
    "smooth_follow_track_roll": (bool, False),
    "smooth_follow_track_pitch": (bool, True),
    "smooth_follow_track_yaw": (bool, True),
    "neck_saver_horizontal_multiplier": (float, 1.0),
    "neck_saver_vertical_multiplier": (float, 1.0),
    "dead_zone_threshold_deg": (float, 0.0),
    "look_ahead": (int, 0),
    "sbs_content": (bool, False),
    "sbs_mode_stretched": (bool, True),
}

STATE_SCHEMA = {
    "heartbeat": (int, 0),
    "connected_device_brand": (str, None),
    "connected_device_model": (str, None),
    "connected_device_full_distance_cm": (float, 0.0),
    "connected_device_full_size_cm": (float, 0.0),
    "connected_device_pose_has_position": (bool, False),
    "calibration_state": (str, None),
    "sbs_mode_supported": (bool, False),
    "sbs_mode_enabled": (bool, False),
    "breezy_desktop_smooth_follow_enabled": (bool, False),
    "is_gamescope_reshade_ipc_connected": (bool, False),
    "firmware_update_recommended": (bool, False),
}


def _parse(kind, raw, default):
    try:
        if kind is bool:
            return raw.strip().lower() == "true" if raw.strip() else default
        if kind is int:
            return int(float(raw))
        if kind is float:
            return float(raw)
        return raw if raw != "" else default
    except (ValueError, TypeError):
        return default


def _format(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".") or "0"
    return str(value)


def _read_kv(path):
    """Return an ordered list of (key, raw_value) pairs; lines without '=' are kept as (line, None)."""
    entries = []
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.rstrip("\n")
                if "=" in line:
                    key, value = line.split("=", 1)
                    entries.append((key.strip(), value))
                elif line.strip():
                    entries.append((line, None))
    except FileNotFoundError:
        pass
    return entries


def _coerce(key, value):
    kind, _ = CONFIG_SCHEMA[key]
    if kind is bool:
        return bool(value)
    if kind is int:
        return int(round(float(value)))
    if kind is float:
        return float(value)
    return str(value)


# ----------------------------------------------------- independent driver
INDEP_SERVICE = "glarsses"
INDEP_SRC_DIR = os.path.join(decky.DECKY_PLUGIN_DIR, "driver")
INDEP_INSTALL_DIR = os.path.join(decky.DECKY_USER_HOME, ".local", "share", "glarsses")
INDEP_UNIT = os.path.join(decky.DECKY_USER_HOME, ".config", "systemd", "user", INDEP_SERVICE + ".service")
INDEP_CONFIG_DIR = os.path.join(decky.DECKY_USER_HOME, ".config", "glarsses")
INDEP_CONFIG = os.path.join(INDEP_CONFIG_DIR, "config.json")
INDEP_CONTROL = os.path.join(INDEP_CONFIG_DIR, "control.json")
INDEP_FILES = ["glarsses_driver.py", "imu.py", "fusion.py", "wl_reshade.py", "Glarsses.fx"]
INDEP_MODES = ("off", "anchored", "follow", "head_locked")
INDEP_DEFAULTS = {
    "mode": "anchored",
    "size": 0.85,
    "distance": 1.0,
    "curved": False,
    "follow_threshold_deg": 12.0,
    "follow_speed": 3.0,
    "follow_yaw": True,
    "follow_pitch": True,
    "look_ahead_ms": 12.0,
    "debug_outline": False,
    "roll_trim_deg": 0.0,
    "gentle_centering": True,
    "ambient_light": False,
    "ambient_intensity": 0.6,
    "ambient_size": 0.35,
}
INDEP_UNIT_TEXT = """[Unit]
Description=glARsses glasses driver (glARsses Decky plugin)

[Service]
Type=simple
ExecStart=/usr/bin/python3 -I %h/.local/share/glarsses/glarsses_driver.py
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
"""


def _user_ids():
    import pwd
    pw = pwd.getpwnam(decky.DECKY_USER)
    return pw.pw_uid, pw.pw_gid


def _indep_state_path():
    return f"/run/user/{_user_ids()[0]}/glarsses-state.json"


def _write_user_file(path, content, mode=0o644):
    """Write a file in the user's home as root, owned by the user (dirs created as needed)."""
    uid, gid = _user_ids()
    d = os.path.dirname(path)
    home = decky.DECKY_USER_HOME
    to_create = []
    while d and d != home and not os.path.isdir(d):
        to_create.append(d)
        d = os.path.dirname(d)
    for d in reversed(to_create):
        os.mkdir(d)
        os.chown(d, uid, gid)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.chown(tmp, uid, gid)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _install_independent():
    """Copy the bundled driver into the user's home and write the systemd unit.
    Returns True if anything changed."""
    changed = False
    for name in INDEP_FILES:
        src = os.path.join(INDEP_SRC_DIR, name)
        dst = os.path.join(INDEP_INSTALL_DIR, name)
        with open(src) as f:
            content = f.read()
        try:
            with open(dst) as f:
                if f.read() == content:
                    continue
        except OSError:
            pass
        _write_user_file(dst, content)
        changed = True
    try:
        with open(INDEP_UNIT) as f:
            unit_same = f.read() == INDEP_UNIT_TEXT
    except OSError:
        unit_same = False
    if not unit_same:
        _write_user_file(INDEP_UNIT, INDEP_UNIT_TEXT)
        changed = True
    return changed


def _xr_gaming_installed():
    return os.path.isfile(os.path.join(decky.DECKY_USER_HOME, ".local", "bin", "xrDriver"))


def _migrate_old_driver():
    """Clean up the driver from before the rename (service "xr-independent"), keeping settings."""
    home = decky.DECKY_USER_HOME
    old_unit = os.path.join(home, ".config", "systemd", "user", "xr-independent.service")
    old_cfg = os.path.join(home, ".config", "xr-independent")
    old_data = os.path.join(home, ".local", "share", "xr-independent")
    old_fx = os.path.join(home, ".local", "share", "gamescope", "reshade", "Shaders", "XRIndependent.fx")
    if os.path.exists(old_unit):
        _user_systemctl("disable", "--now", "xr-independent")
        os.remove(old_unit)
        _user_systemctl("daemon-reload")
        decky.logger.info("removed old xr-independent service")
    if os.path.isdir(old_cfg) and not os.path.exists(INDEP_CONFIG_DIR):
        os.rename(old_cfg, INDEP_CONFIG_DIR)
        decky.logger.info("moved driver settings to ~/.config/glarsses")
    if os.path.isdir(old_data):
        shutil.rmtree(old_data, ignore_errors=True)
    if os.path.exists(old_fx):
        os.remove(old_fx)


def _user_systemctl(*args):
    uid, _ = _user_ids()
    cmd = f"XDG_RUNTIME_DIR=/run/user/{uid} systemctl --user " + " ".join(args)
    result = subprocess.run(["su", "-l", "-c", cmd, decky.DECKY_USER], capture_output=True, text=True)
    if result.returncode != 0 and "disable" not in args:
        decky.logger.error(f"systemctl --user {' '.join(args)}: {result.stdout} {result.stderr}")
    return result.returncode == 0


def _load_store():
    if not os.path.exists(STORE_PATH) and os.path.exists(OLD_STORE_PATH):
        try:
            with open(OLD_STORE_PATH) as f:
                _save_store(json.load(f))
            decky.logger.info("migrated settings from AR Glasses")
        except (OSError, ValueError) as e:
            decky.logger.error(f"settings migration failed: {e}")
    try:
        with open(STORE_PATH, "r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}


def _save_store(data):
    os.makedirs(os.path.dirname(STORE_PATH), exist_ok=True)
    tmp = STORE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, STORE_PATH)


class Plugin:
    # ---------------------------------------------------------------- status
    async def get_status(self):
        state = {k: d for k, (_, d) in STATE_SCHEMA.items()}
        for key, raw in _read_kv(STATE_PATH):
            if raw is not None and key in STATE_SCHEMA:
                kind, default = STATE_SCHEMA[key]
                state[key] = _parse(kind, raw, default)

        # license info is JSON in the state file; extract just the feature status we care about
        features = {}
        for key, raw in _read_kv(STATE_PATH):
            if key == "device_license" and raw:
                try:
                    lic = json.loads(raw)
                    now = time.time()
                    for name, info in (lic.get("features") or {}).items():
                        status = info.get("status", "off")
                        end = info.get("endDate")
                        enabled = status != "off" and (end is None or end > now)
                        features[name] = {
                            "enabled": enabled,
                            "trial": status == "trial",
                            "days_left": int((end - now) / 86400) if (end and enabled) else None,
                        }
                except (json.JSONDecodeError, AttributeError, TypeError):
                    pass

        state["features"] = features
        state["driver_running"] = state["heartbeat"] != 0 and (time.time() - state["heartbeat"]) < 5
        state["breezy_installed"] = os.path.isfile(BREEZY_MANIFEST)
        state["config_exists"] = os.path.isfile(CONFIG_PATH)
        return state

    # ---------------------------------------------------------------- config
    async def get_config(self):
        config = {k: d for k, (_, d) in CONFIG_SCHEMA.items()}
        for key, raw in _read_kv(CONFIG_PATH):
            if raw is not None and key in CONFIG_SCHEMA:
                kind, default = CONFIG_SCHEMA[key]
                config[key] = _parse(kind, raw, default)
        # external_mode may be a comma list; the driver currently uses only one
        config["external_mode"] = (config["external_mode"] or "none").split(",")[0]
        return config

    async def set_config(self, changes):
        """Merge `changes` into config.ini, preserving unknown keys and line order."""
        try:
            clean = {k: _coerce(k, v) for k, v in (changes or {}).items() if k in CONFIG_SCHEMA}
            if not clean:
                return await self.get_config()

            entries = _read_kv(CONFIG_PATH)
            seen = set()
            lines = []
            for key, raw in entries:
                if raw is None:
                    lines.append(key)
                    continue
                if key in clean:
                    lines.append(f"{key}={_format(clean[key])}")
                    seen.add(key)
                else:
                    lines.append(f"{key}={raw}")
            for key, value in clean.items():
                if key not in seen:
                    lines.append(f"{key}={_format(value)}")

            config_dir = os.path.dirname(CONFIG_PATH)
            os.makedirs(config_dir, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".config.", dir=config_dir)
            with os.fdopen(fd, "w") as f:
                f.write("\n".join(lines) + "\n")
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH | stat.S_IWOTH)
            os.replace(tmp, CONFIG_PATH)
            return await self.get_config()
        except Exception as e:
            decky.logger.error(f"set_config failed: {e}")
            return None

    # --------------------------------------------------------- control flags
    async def send_control(self, flags):
        try:
            out = []
            for key, value in (flags or {}).items():
                if key not in ALLOWED_CONTROL_FLAGS:
                    continue
                if key == "sbs_mode" and value not in ("enable", "disable", "unset"):
                    continue
                if isinstance(value, list):
                    value = ",".join(str(v) for v in value)
                elif isinstance(value, bool):
                    value = "true" if value else "false"
                out.append(f"{key}={value}")
            if not out:
                return False
            with open(CONTROL_FLAGS_PATH, "w") as f:
                f.write("\n".join(out) + "\n")
            os.chmod(CONTROL_FLAGS_PATH, 0o777)
            return True
        except Exception as e:
            decky.logger.error(f"send_control failed: {e}")
            return False

    async def restart_driver(self):
        try:
            uid = subprocess.check_output(["id", "-u", decky.DECKY_USER]).decode().strip()
            subprocess.check_output(
                ["su", "-l", "-c", f"XDG_RUNTIME_DIR=/run/user/{uid} systemctl --user restart xr-driver", decky.DECKY_USER],
                stderr=subprocess.STDOUT,
            )
            return True
        except subprocess.CalledProcessError as e:
            decky.logger.error(f"restart_driver failed: {e.output}")
            return False

    # --------------------------------------------------- presets & profiles
    async def get_store(self):
        store = _load_store()
        store.setdefault("presets", {})
        store.setdefault("game_profiles", {})
        store.setdefault("per_game_enabled", False)
        return store

    async def save_preset(self, name, values):
        store = await self.get_store()
        store["presets"][str(name)] = values
        _save_store(store)
        return store

    async def delete_preset(self, name):
        store = await self.get_store()
        store["presets"].pop(str(name), None)
        _save_store(store)
        return store

    async def save_game_profile(self, appid, name, values):
        store = await self.get_store()
        store["game_profiles"][str(appid)] = {"name": name, "values": values}
        _save_store(store)
        return store

    async def delete_game_profile(self, appid):
        store = await self.get_store()
        store["game_profiles"].pop(str(appid), None)
        _save_store(store)
        return store

    async def set_per_game_enabled(self, enabled):
        store = await self.get_store()
        store["per_game_enabled"] = bool(enabled)
        _save_store(store)
        return store

    # ------------------------------------------------- independent driver
    async def get_driver_choice(self):
        store = await self.get_store()
        if "driver" not in store:
            # fresh install: use XR Gaming only if its driver is present, otherwise glARsses
            return "xr_gaming" if _xr_gaming_installed() else "independent"
        return store["driver"]

    async def set_driver_choice(self, choice):
        if choice not in ("xr_gaming", "independent"):
            return await self.get_driver_choice()
        store = await self.get_store()
        current = store.get("driver", "xr_gaming")
        try:
            if choice == "independent":
                _migrate_old_driver()
                if current != "independent":
                    xr = await self.get_config()
                    store["xr_disabled_before"] = xr.get("disabled", True)
                _install_independent()
                await self.set_config({"disabled": True})  # make sure Breezy never draws on top
                _user_systemctl("disable", "--now", "xr-driver")
                _user_systemctl("daemon-reload")
                _user_systemctl("enable", "--now", INDEP_SERVICE)
            else:
                if not _xr_gaming_installed():
                    decky.logger.error("XR Gaming's driver is not installed; staying on the glARsses driver")
                    return current
                _user_systemctl("disable", "--now", INDEP_SERVICE)
                await self.set_config({"disabled": bool(store.get("xr_disabled_before", True))})
                _user_systemctl("enable", "--now", "xr-driver")
            store["driver"] = choice
            _save_store(store)
        except Exception as e:
            decky.logger.error(f"switching driver to {choice} failed: {e}")
        return store.get("driver", "xr_gaming")

    async def get_indep_config(self):
        cfg = dict(INDEP_DEFAULTS)
        try:
            with open(INDEP_CONFIG) as f:
                cfg.update({k: v for k, v in json.load(f).items() if k in INDEP_DEFAULTS})
        except (OSError, ValueError):
            pass
        return cfg

    async def set_indep_config(self, changes):
        cfg = await self.get_indep_config()
        for k, v in (changes or {}).items():
            if k not in INDEP_DEFAULTS:
                continue
            default = INDEP_DEFAULTS[k]
            try:
                if isinstance(default, bool):
                    cfg[k] = bool(v)
                elif isinstance(default, (int, float)):
                    cfg[k] = float(v)
                elif k == "mode" and v in INDEP_MODES:
                    cfg[k] = v
            except (TypeError, ValueError):
                pass
        _write_user_file(INDEP_CONFIG, json.dumps(cfg, indent=2))
        return cfg

    async def indep_recenter(self):
        try:
            with open(INDEP_CONTROL) as f:
                ctl = json.load(f)
        except (OSError, ValueError):
            ctl = {}
        ctl["recenter"] = int(ctl.get("recenter", 0)) + 1
        _write_user_file(INDEP_CONTROL, json.dumps(ctl))
        return True

    async def get_indep_status(self):
        state = {}
        try:
            with open(_indep_state_path()) as f:
                state = json.load(f)
        except (OSError, ValueError):
            pass
        state["running"] = bool(state.get("heartbeat")) and time.time() - state.get("heartbeat", 0) < 3
        state["installed"] = os.path.isfile(os.path.join(INDEP_INSTALL_DIR, "glarsses_driver.py"))
        return state

    # ------------------------------------------------------------ lifecycle
    async def _main(self):
        decky.logger.info("glARsses plugin loaded")
        try:
            _migrate_old_driver()
            if (await self.get_driver_choice()) == "independent":
                # keep the installed driver in sync with this plugin version
                changed = _install_independent()
                if changed:
                    _user_systemctl("daemon-reload")
                _user_systemctl("enable", INDEP_SERVICE)
                _user_systemctl("restart" if changed else "start", INDEP_SERVICE)
        except Exception as e:
            decky.logger.error(f"independent driver sync failed: {e}")

    async def _unload(self):
        pass

    async def _uninstall(self):
        pass
