#!/usr/bin/env python3
"""glARsses glasses driver.

Reads the XREAL glasses' IMU, tracks head orientation, and drives the Glarsses.fx
effect in gamescope (Gaming Mode) so the game appears as a virtual screen that is anchored,
smoothly follows, or is head-locked.

Files:
  ~/.config/glarsses/config.json   settings (written by the Decky plugin)
  ~/.config/glarsses/control.json  one-shot commands, e.g. {"recenter": <counter>}
  $XDG_RUNTIME_DIR/glarsses-state.json   live state for the plugin
"""
import json, math, os, shutil, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fusion, imu, wl_reshade  # noqa: E402

# GLARSSES_CONFIG_DIR lets tests use their own settings without touching the real ones
CONFIG_DIR = os.environ.get("GLARSSES_CONFIG_DIR") or os.path.expanduser("~/.config/glarsses")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
CONTROL_PATH = os.path.join(CONFIG_DIR, "control.json")
AXES_PATH = os.path.join(CONFIG_DIR, "axes.json")
RUNTIME = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
STATE_PATH = os.path.join(CONFIG_DIR, "state.json") if os.environ.get("GLARSSES_CONFIG_DIR") else os.path.join(RUNTIME, "glarsses-state.json")
SHADERS = os.path.expanduser("~/.local/share/gamescope/reshade/Shaders")
EFFECT = "Glarsses.fx"

DEFAULTS = {
    "mode": "anchored",          # off | anchored | follow | head_locked
    "size": 0.85,                # screen scale relative to the glasses' view
    "distance": 1.0,             # 3DoF glasses: farther = smaller (apparent size = size / distance)
    "curved": False,
    "follow_threshold_deg": 12.0,
    "follow_speed": 3.0,         # how quickly the screen catches up (1/s)
    "follow_yaw": True,
    "follow_pitch": True,
    "look_ahead_ms": 12.0,       # pose prediction to hide display latency
    "fov_diag_deg": 46.0,        # XREAL Air 2 Pro
    "output_aspect": 16 / 9,
    "debug_outline": False,
    "roll_trim_deg": 0.0,        # + rotates the screen clockwise
}

# XREAL Air 2 Pro sensor -> head frame (learned with head_test.py)
DEFAULT_AXES = {"idx": [0, 2, 1], "sign": [-1, 1, 1]}


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_state(state):
    tmp = STATE_PATH + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_PATH)
    except OSError:
        pass


def fov_tangents(diag_deg, aspect):
    t = math.tan(math.radians(diag_deg) / 2)
    d = math.hypot(aspect, 1.0)
    return t * aspect / d, t / d


def look_rotation(yaw, pitch, roll=0.0):
    """Orientation looking at yaw (rad, +left) and pitch (rad, +up), tilted by roll (rad, +left)."""
    q = fusion.qmul(fusion.qaxis((0, 1, 0), yaw), fusion.qaxis((1, 0, 0), pitch))
    return fusion.qmul(q, fusion.qaxis((0, 0, 1), roll)) if roll else q


def head_yaw_pitch(q):
    fwd = fusion.qrot(q, (0, 0, -1))
    return math.atan2(-fwd[0], -fwd[2]), math.asin(max(-1.0, min(1.0, fwd[1])))


def angle_between(a, b):
    d = abs(sum(x * y for x, y in zip(a, b)))
    return 2 * math.degrees(math.acos(min(1.0, d)))


class BiasTracker:
    """Keeps the gyro bias (offset) accurate so the screen doesn't drift sideways.

    * Normal use: every 0.5 s window where the head is nearly still nudges the bias toward the
      measured mean (tolerates the small micro-movements of a head wearing the glasses).
    * After a recenter: the next ~1 s (user holding still) sets the bias directly.
    """
    WINDOW = 500

    def __init__(self, fusion_obj):
        self.f = fusion_obj
        self.capture_windows = 0
        self.reset()

    def reset(self):
        self.n = 0
        self.sum = [0.0, 0.0, 0.0]
        self.sq = [0.0, 0.0, 0.0]

    def request_capture(self):
        self.reset()
        self.capture_windows = 2

    def add(self, gyro):
        for i in range(3):
            v = gyro[i]
            self.sum[i] += v
            self.sq[i] += v * v
        self.n += 1
        if self.n < self.WINDOW:
            return
        n = self.n
        mean = [self.sum[i] / n for i in range(3)]
        sd = [math.sqrt(max(0.0, self.sq[i] / n - mean[i] ** 2)) for i in range(3)]
        diff = [mean[i] - self.f.bias[i] for i in range(3)]
        if self.capture_windows > 0:
            self.capture_windows -= 1
            if all(sd[i] < 1.0 and abs(diff[i]) < 4.0 for i in range(3)):
                self.f.bias = tuple(mean)
        elif all(sd[i] < 0.5 and abs(diff[i]) < 1.0 for i in range(3)):
            self.f.bias = tuple(self.f.bias[i] + 0.25 * diff[i] for i in range(3))
        self.reset()


class Driver:
    def __init__(self):
        self.config = dict(DEFAULTS)
        self.config_mtime = None
        self.recenter_seen = None
        self.dev = None
        self.rs = None
        self.fusion = None
        self.bias = None
        self.anchor = None
        self.following = False
        self.ref = None
        self.ayaw = self.apitch = 0.0
        self.effect_on = False
        self.last_sent = None
        self.last_ts = None
        self.omega = (0.0, 0.0, 0.0)
        self._acc = [0.0] * 6
        self._n = 0
        self.head = None
        self.state = {"glasses": False, "gamescope": False, "mode": self.config["mode"]}
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self.install_shader()

    # ------------------------------------------------------------ setup
    def install_shader(self):
        os.makedirs(SHADERS, exist_ok=True)
        src = os.path.join(HERE, EFFECT)
        dst = os.path.join(SHADERS, EFFECT)
        try:
            if not os.path.exists(dst) or open(src, "rb").read() != open(dst, "rb").read():
                shutil.copy(src, dst)
        except OSError as e:
            log(f"could not install shader: {e}")

    def reload_config(self):
        try:
            m = os.path.getmtime(CONFIG_PATH)
        except OSError:
            m = None
        if m == self.config_mtime:
            return False
        self.config_mtime = m
        cfg = dict(DEFAULTS)
        cfg.update({k: v for k, v in load_json(CONFIG_PATH, {}).items() if k in DEFAULTS})
        old_mode = self.config.get("mode")
        self.config = cfg
        self.state["mode"] = cfg["mode"]
        if cfg["mode"] != old_mode:
            self.anchor = None  # recenter on mode change
        self.push_static_uniforms()
        return True

    def check_control(self):
        ctl = load_json(CONTROL_PATH, {})
        counter = ctl.get("recenter")
        if self.recenter_seen is None:
            self.recenter_seen = counter
        elif counter != self.recenter_seen:
            self.recenter_seen = counter
            self.anchor = None
            if self.bias:
                self.bias.request_capture()
            log("recenter")

    # ------------------------------------------------------ connections
    def connect_glasses(self):
        if self.dev:
            return True
        path = imu.find_imu_hidraw()
        if not path:
            return False
        try:
            self.dev = imu.IMU(path)
        except OSError as e:
            log(f"cannot open {path}: {e}")
            return False
        axes = load_json(AXES_PATH, DEFAULT_AXES)
        self.fusion = fusion.Fusion(fusion.AxisMap(tuple(axes["idx"]), tuple(axes["sign"])))
        self.bias = BiasTracker(self.fusion)
        log(f"glasses connected on {path}; calibrating (keep still)")
        samples = self.still_window()
        self.fusion.calibrate(samples)
        self.last_ts = samples[-1][0]
        self.head = self.fusion.q
        self._acc = [0.0] * 6
        self._n = 0
        self.anchor = None
        self.state["glasses"] = True
        return True

    def still_window(self, n=500, max_seconds=4.0):
        """Collect samples until there is a 0.5 s window where the head is still (or time runs
        out, then use the stillest window seen). Used for the initial gyro bias / tilt."""
        best, best_sd = None, 1e9
        window = []
        t_end = time.time() + max_seconds
        while time.time() < t_end:
            window.append(self.dev.read())
            if len(window) < n:
                continue
            sd = 0.0
            for i in range(3):
                vals = [w[1][i] for w in window]
                m = sum(vals) / n
                sd = max(sd, math.sqrt(sum((v - m) ** 2 for v in vals) / n))
            if sd < best_sd:
                best, best_sd = list(window), sd
            if sd < 0.7:
                break
            window = window[n // 2:]
        log(f"initial calibration stillness: {best_sd:.2f} deg/s")
        return best

    def drop_glasses(self):
        if self.dev:
            try:
                self.dev.close()
            except OSError:
                pass
        self.dev = None
        self.state["glasses"] = False
        self.set_effect_enabled(False)

    def connect_gamescope(self):
        if self.rs:
            return True
        sock = wl_reshade.find_gamescope_socket()
        if not sock:
            return False
        try:
            self.rs = wl_reshade.Reshade(sock)
            self.effect_on = False
            log(f"connected to gamescope at {sock}")
        except (OSError, RuntimeError) as e:
            log(f"gamescope connect failed: {e}")
            self.rs = None
            return False
        self.state["gamescope"] = True
        return True

    def drop_gamescope(self):
        if self.rs:
            try:
                self.rs.close()
            except OSError:
                pass
        self.rs = None
        self.effect_on = False
        self.state["gamescope"] = False

    # ----------------------------------------------------------- effect
    def set_effect_enabled(self, on):
        if not self.rs or on == self.effect_on:
            return
        try:
            if on:
                self.install_shader()
                # don't block: gamescope compiles the effect on its next frame and keeps
                # uniforms we send after set_effect
                self.rs.set_effect(EFFECT, wait=False)
                self.rs.enable()
                self.effect_on = True
                self.last_sent = None
                self.push_static_uniforms()
            else:
                self.rs.set_floats("xri_screen", [1.0, 0.0])
                self.rs.disable()
                self.effect_on = False
        except (OSError, RuntimeError) as e:
            log(f"gamescope error: {e}")
            self.drop_gamescope()

    def push_static_uniforms(self):
        if not self.rs or not self.effect_on:
            return
        c = self.config
        th, tv = fov_tangents(c["fov_diag_deg"], c["output_aspect"])
        scale = max(0.1, float(c["size"])) / max(0.1, float(c["distance"]))
        try:
            self.rs.set_floats("xri_fov", [th, tv])
            self.rs.set_floats("xri_output", [float(c["output_aspect"]), 1.0 if c["curved"] else 0.0])
            self.rs.set_floats("xri_debug", [1.0 if c["debug_outline"] else 0.0])
            self.rs.set_floats("xri_screen", [scale, 1.0])
        except OSError as e:
            log(f"gamescope error: {e}")
            self.drop_gamescope()

    # ---------------------------------------------------------- tracking
    BATCH = 4  # run the filter at 250 Hz on averaged 1 kHz samples to save CPU

    def process_sample(self, ts, gyro, accel):
        self.bias.add(gyro)
        acc = self._acc
        for i in range(3):
            acc[i] += gyro[i]
            acc[3 + i] += accel[i]
        self._n += 1
        if self._n < self.BATCH:
            return None
        n = self._n
        g = (acc[0] / n, acc[1] / n, acc[2] / n)
        a = (acc[3] / n, acc[4] / n, acc[5] / n)
        self._acc = [0.0] * 6
        self._n = 0
        self.head = self.fusion.update(ts, g, a)
        self.omega = self.fusion.map(tuple(math.radians(g[i] - self.fusion.bias[i]) for i in range(3)))
        return self.head

    def predicted(self, q):
        la = self.config["look_ahead_ms"] / 1000.0
        w = self.omega
        wn = math.sqrt(w[0] ** 2 + w[1] ** 2 + w[2] ** 2)
        if la <= 0 or wn < 1e-3:
            return q
        return fusion.qnorm(fusion.qmul(q, fusion.qaxis(tuple(c / wn for c in w), wn * la)))

    def update_anchor(self, head, dt):
        """Anchored: the screen stays where it was at recenter.
        Follow: the screen's direction is tracked in the frame of the head pose captured at
        recenter (self.ref), so turning around the neck's natural (often slightly tilted) axis
        never tilts the screen, while leaning the head sideways doesn't rotate it either."""
        c = self.config
        if self.anchor is None:
            self.ref = head          # includes the head's tilt, so the screen looks level
            self.ayaw = self.apitch = 0.0
            self.anchor = head
            self.following = False
            return
        if c["mode"] != "follow":
            return
        # where the head looks, relative to the recenter pose
        fwd = fusion.qrot(fusion.qmul(fusion.qconj(self.ref), head), (0, 0, -1))
        yaw = math.atan2(-fwd[0], -fwd[2])
        pitch = math.asin(max(-1.0, min(1.0, fwd[1])))
        ty = yaw if c["follow_yaw"] else self.ayaw
        tp = pitch if c["follow_pitch"] else self.apitch
        dy = (ty - self.ayaw + math.pi) % (2 * math.pi) - math.pi
        dp = tp - self.apitch
        # angle between where the screen is and where it should go
        cur = fusion.qrot(look_rotation(self.ayaw, self.apitch), (0, 0, -1))
        tgt = fusion.qrot(look_rotation(ty, tp), (0, 0, -1))
        off = math.degrees(math.acos(max(-1.0, min(1.0, sum(a * b for a, b in zip(cur, tgt))))))
        if off > c["follow_threshold_deg"]:
            self.following = True
        elif off < 0.5:
            self.following = False
        if self.following:
            t = 1.0 - math.exp(-max(0.1, c["follow_speed"]) * dt)
            self.ayaw += dy * t
            self.apitch += dp * t
            self.anchor = fusion.qmul(self.ref, look_rotation(self.ayaw, self.apitch))

    def send_pose(self, head):
        if not self.rs or not self.effect_on:
            return
        mode = self.config["mode"]
        trim = fusion.qaxis((0, 0, 1), -math.radians(float(self.config["roll_trim_deg"])))
        if mode == "head_locked":
            rel = fusion.qconj(trim)
        else:
            rel = fusion.qmul(fusion.qconj(fusion.qmul(self.anchor, trim)), head)
        if self.last_sent is not None and angle_between(rel, self.last_sent) < 0.01:
            return
        self.rs.set_floats("xri_pose", list(rel))
        self.rs.pump()
        self.last_sent = rel

    # -------------------------------------------------------------- main
    def run(self):
        log("glARsses driver started")
        next_frame = 0.0
        next_housekeeping = 0.0
        last_frame_time = time.monotonic()
        while True:
            now = time.monotonic()
            if now >= next_housekeeping:
                next_housekeeping = now + 0.25
                self.reload_config()
                self.check_control()
                active = self.config["mode"] != "off"
                if active and not self.connect_gamescope():
                    self.drop_glasses()
                if active and self.rs and not self.connect_glasses():
                    self.set_effect_enabled(False)
                if not active:
                    self.set_effect_enabled(False)
                    if self.dev:
                        self.drop_glasses()
                self.state.update({"heartbeat": time.time(), "following": self.following,
                                   "effect": self.effect_on})
                write_state(self.state)
                if not (active and self.dev and self.rs):
                    time.sleep(0.5)
                    continue
                self.set_effect_enabled(True)
                if not self.rs:
                    continue  # gamescope went away while enabling; retry on next housekeeping

            time.sleep(0.004)  # the kernel queues samples; batching keeps wakeups (and CPU) low
            try:
                samples = self.dev.read_available()
            except OSError:
                log("glasses disconnected")
                self.drop_glasses()
                continue
            for ts, gyro, accel in samples:
                self.process_sample(ts, gyro, accel)
            head = self.head
            now = time.monotonic()

            if not self.rs:
                next_housekeeping = 0.0
                continue
            if head is not None and now >= next_frame:
                dt = now - last_frame_time
                last_frame_time = now
                next_frame = now + 1 / 120
                try:
                    p = self.predicted(head)
                    self.update_anchor(p, dt)
                    self.send_pose(p)
                except (OSError, RuntimeError) as e:
                    log(f"gamescope error: {e}")
                    self.drop_gamescope()


if __name__ == "__main__":
    try:
        Driver().run()
    except KeyboardInterrupt:
        pass
