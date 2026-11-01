"""Find which USB HID reports the XREAL glasses send when their buttons are pressed.

Listens on every non-IMU hidraw interface of the glasses while on-screen notifications ask
you to press each button, then prints the reports that only appeared during a press.
Results are also saved to ~/.config/glarsses/button_test.txt.
"""
import glob, os, select, subprocess, time

VID = "00003318"
PHASES = [
    ("Don't press anything for a few seconds", 5),
    ("Press brightness UP once", 6),
    ("Press brightness DOWN once", 6),
    ("Press the other (top / dimming) button once", 6),
    ("Press brightness UP twice quickly", 6),
]


def say(msg):
    print(msg, flush=True)
    subprocess.run(["notify-send", "-t", "4000", "glARsses button test", msg], stderr=subprocess.DEVNULL)


def interfaces():
    out = []
    for d in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            ue = open(d + "/device/uevent").read()
        except OSError:
            continue
        if VID in ue.upper() and "/input3" not in ue:  # input3 is the IMU stream
            phys = [l for l in ue.splitlines() if l.startswith("HID_PHYS=")][0].split("/")[-1]
            out.append(("/dev/" + os.path.basename(d), phys))
    return out


def signature(b):
    # ignore bytes that change every packet (checksum/timestamps) by keeping a short prefix
    # plus any message id area; full hex is kept for the report
    return b[:2].hex() + ":" + b[7:9].hex() + ":" + b[15:17].hex()


fds = {}
for path, phys in interfaces():
    try:
        fds[os.open(path, os.O_RDONLY | os.O_NONBLOCK)] = phys
    except OSError as e:
        print("cannot open", path, e)
if not fds:
    raise SystemExit("glasses not found")

seen = {}  # phase index -> {(iface, sig): example bytes}
for k, (text, secs) in enumerate(PHASES):
    say(text)
    seen[k] = {}
    end = time.time() + secs
    while time.time() < end:
        r, _, _ = select.select(list(fds), [], [], 0.2)
        for fd in r:
            try:
                b = os.read(fd, 64)
            except BlockingIOError:
                continue
            if not b or b[0] != 0xFD:  # only MCU messages; other channels stream constantly
                continue
            key = (fds[fd], signature(b))
            seen[k].setdefault(key, []).append(b)

lines = []
base = set(seen[0])
for k, (text, _) in enumerate(PHASES):
    if k == 0:
        continue
    new = {key: v for key, v in seen[k].items() if key not in base}
    lines.append(f"== {text}: {len(new)} new report type(s)")
    for (iface, sig), examples in new.items():
        lines.append(f"   {iface} x{len(examples)}  sig {sig}  first: {examples[0][:32].hex()}")
lines.append("== background report types: " + ", ".join(f"{i}:{s}" for i, s in sorted(base)))
report = "\n".join(lines)
print("\n" + report)
os.makedirs(os.path.expanduser("~/.config/glarsses"), exist_ok=True)
open(os.path.expanduser("~/.config/glarsses/button_test.txt"), "w").write(report + "\n")
say("Button test finished")
