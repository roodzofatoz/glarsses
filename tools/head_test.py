"""Step 1 live test: prints how far the glasses rotated around each sensor axis while you move."""
import os, subprocess, sys, time

def say(msg):
    print(msg, flush=True)
    subprocess.run(["notify-send", "-t", "3500", "XR head test", msg], stderr=subprocess.DEVNULL)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "driver"))
import imu

d = imu.IMU()
say("Look straight ahead and keep still...")
bias = [0.0, 0.0, 0.0]
n = 0
end = time.time() + 2
while time.time() < end:
    _, g, _ = d.read()
    for i in range(3):
        bias[i] += g[i]
    n += 1
bias = [b / n for b in bias]
print("Calibrated gyro bias (deg/s):", [round(b, 2) for b in bias])

steps = [
    ("TURN your head LEFT about 90 degrees and hold", 4),
    ("LOOK UP and hold", 4),
    ("TILT your head toward your RIGHT shoulder and hold", 4),
]
results = []
for k, (text, secs) in enumerate(steps):
    if k:
        say("Face straight ahead again")
        t_back = time.time() + 3
        while time.time() < t_back:
            d.read()
    say(text)
    ang = [0.0, 0.0, 0.0]
    last_ts = None
    t_end = time.time() + secs
    next_print = time.time()
    while time.time() < t_end:
        ts, g, _ = d.read()
        if last_ts is not None:
            dt = (ts - last_ts) / 1e9
            for i in range(3):
                ang[i] += (g[i] - bias[i]) * dt
        last_ts = ts
        if time.time() >= next_print:
            print("   axis x/y/z degrees: %7.1f %7.1f %7.1f" % tuple(ang))
            next_print += 1
    results.append(ang)

print("\nRESULT")
for (text, _), ang in zip(steps, results):
    print(f"  {text[:40]:40s} -> x={ang[0]:.0f} y={ang[1]:.0f} z={ang[2]:.0f}")
d.close()

# Learn mapping sensor -> head frame (x right, y up, z backward):
#   look up  = +rotation about head x, turn left = +rotation about head y, tilt right = -rotation about head z
import json
yaw, pitch, roll = results
def dom(v):
    i = max(range(3), key=lambda k: abs(v[k]))
    return i, (1 if v[i] > 0 else -1), abs(v[i])
(px, ps, pm), (yx, ys, ym), (rx, rs, rm) = dom(pitch), dom(yaw), dom(roll)
if len({px, yx, rx}) == 3 and min(pm, ym, rm) > 20:
    cfg_dir = os.path.expanduser("~/.config/glarsses"); os.makedirs(cfg_dir, exist_ok=True)
    json.dump({"idx": [px, yx, rx], "sign": [ps, ys, -rs]}, open(os.path.join(cfg_dir, "axes.json"), "w"))
    print("\nOK: learned axis mapping -> ~/.config/glarsses/axes.json (restart the glarsses service to use it)", {"idx": [px, yx, rx], "sign": [ps, ys, -rs]})
else:
    print("\nCould not learn a clean mapping (moves too small or mixed). Please run the test again.")
