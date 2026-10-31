"""Step 2 live test: anchored virtual screen driven by our own head tracking.

Desktop Mode: launches a nested gamescope window running vkcube (fullscreen on the glasses).
Gaming Mode:  attaches to the running gamescope.
Press Ctrl+C to stop; the effect is switched off on exit.
"""
import os, shutil, signal, subprocess, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "driver"))
import imu, fusion, wl_reshade

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "driver")
SHADERS = os.path.expanduser("~/.local/share/gamescope/reshade/Shaders")
SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 40

os.makedirs(SHADERS, exist_ok=True)
shutil.copy(os.path.join(HERE, "Glarsses.fx"), SHADERS)

nested = None
sock = wl_reshade.find_gamescope_socket()
if not sock or os.environ.get("XDG_SESSION_DESKTOP") == "KDE" or os.environ.get("WAYLAND_DISPLAY") == "wayland-0":
    before = set(os.listdir(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")))
    nested = subprocess.Popen(["gamescope", "-W", "1920", "-H", "1080", "-w", "1920", "-h", "1080", "-f", "--", "glxgears", "-geometry", "1920x1080"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    rt = os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")
    for _ in range(100):
        time.sleep(0.1)
        import re
        new = [f for f in set(os.listdir(rt)) - before if re.fullmatch(r"gamescope-\d+", f)]
        if new:
            sock = os.path.join(rt, new[0]); break
    time.sleep(1.0)
print("gamescope socket:", sock)

rs = None
try:
    rs = wl_reshade.Reshade(sock)
    print("effect loaded:", rs.set_effect("Glarsses.fx"))
    rs.enable()
    rs.set_floats("xri_fov", [0.370, 0.208])
    rs.set_floats("xri_screen", [0.7, 1.0])
    rs.set_floats("xri_debug", [1.0])

    d = imu.IMU()
    f = fusion.Fusion(fusion.AxisMap.load())
    print("Keep your head still for 1 second...")
    samples = [d.read() for _ in range(1000)]
    f.calibrate(samples)
    anchor = fusion.yaw_only(f.q)
    print(f"Running for {SECONDS:.0f} s. Look around - the screen should stay put in the room.")
    end = time.time() + SECONDS
    next_send = 0.0
    next_print = time.time()
    while time.time() < end:
        ts, g, a = d.read()
        q = f.update(ts, g, a)
        now = time.time()
        if now >= next_send:
            rel = fusion.qmul(fusion.qconj(anchor), q)
            rs.set_floats("xri_pose", list(rel))
            rs.pump()
            next_send = now + 1 / 120
        if now >= next_print:
            y, p, r = fusion.yaw_pitch_roll(fusion.qmul(fusion.qconj(anchor), q))
            print(f"  yaw {y:6.1f}  pitch {p:6.1f}  roll {r:6.1f}")
            next_print = now + 1
except KeyboardInterrupt:
    pass
finally:
    if rs:
        rs.close()
    if nested:
        os.killpg(nested.pid, signal.SIGTERM)
    print("Stopped; effect disabled.")
