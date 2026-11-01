# glARsses

A [Decky Loader](https://decky.xyz) plugin for Steam Deck that turns AR glasses into a virtual screen in **Gaming Mode**, with its own **free, independent head-tracking driver**.

- **Smooth follow:** the screen stays put until you look away past a threshold, then glides after you.
- **Anchored:** the screen stays fixed in place in the room.
- **Head-locked:** the screen moves rigidly with your head.
- Display **size** and **distance**, **curved** screen, and **tilt correction**.
- **Ambient light:** an optional glow around the screen, coloured by the picture (Ambilight-style).
- **Recenter**, which also re-measures the gyro offset to stop drift.
- **Presets** and automatic **per-game profiles**.
- Optional support for the XR Gaming plugin's driver, if you have it installed.

Tested with the **XREAL Air 2 Pro**. Other XREAL Air models will probably work but are untested.

## Install

You need Decky Loader installed. In **Desktop Mode**, open Konsole and run:

```bash
curl -fsSL https://raw.githubusercontent.com/roodzofatoz/glarsses/main/install.sh | bash
```

It asks for your sudo password. If you haven't set one yet, run `passwd` first. Then switch to Gaming Mode, press **⋯**, and open **glARsses**.

To update, run the same command again.

### Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/roodzofatoz/glarsses/main/install.sh | bash -s -- --uninstall
```

Add `--purge` to also delete your settings, presets and profiles.

## Using it

1. Plug in your glasses and switch to Gaming Mode.
2. In the glARsses panel, leave **Driver** on **glARsses (free)**.
3. Pick a **Display mode**. Sit the way you normally play, look straight ahead and press **Recenter**, then hold still for a second.
4. Adjust **Display size**, **Display distance** and **Tilt correction** to taste.

**Tips:**
- Steam Deck games usually render at 16:10, while the glasses are 16:9. Setting a game's resolution to 1920×1080 in its Steam properties lets the screen use the glasses' full width.
- If the screen slowly drifts sideways, press **Recenter** while holding your head still.

## How it works

- **Reading the glasses:** `driver/glarsses_driver.py` is a small user service (`glarsses.service`). It reads the glasses' gyroscope and accelerometer directly over USB HID at 1 kHz.
- **Head tracking:** it estimates head orientation with a complementary (Mahony) filter and keeps re-estimating the gyro's offset while you're still.
- **Drawing the screen:** it streams the head pose to its own gamescope ReShade effect (`driver/Glarsses.fx`), using gamescope's `gamescope_reshade` Wayland protocol. The effect redraws the game as a flat or curved screen placed in space.
- **The plugin:** the Decky plugin (`main.py`, `src/`) installs and controls the driver and provides the Quick Access panel.

**Limitations:**
- These glasses track rotation only (3DoF), so "distance" shows up as a change in scale.
- The virtual screen can only be drawn inside the area the game occupies. A 16:10 game is cut off slightly at the sides when you turn your head.

### Troubleshooting

```bash
systemctl --user status glarsses       # is the driver running?
journalctl --user -u glarsses -f       # driver log
python3 tools/head_test.py             # re-learn sensor axes for other glasses models
```

## Building from source

```bash
npm install
npm run build                  # builds dist/index.js
scripts/dev-install.sh         # copies this checkout into Decky (sudo)
scripts/build-release.sh       # creates release/decky-glarsses.zip
```

## License

GPL-3.0. See [LICENSE](LICENSE).

glARsses is an independent project. It is not affiliated with XREAL, Valve, or the XR Gaming / Breezy Desktop projects.
