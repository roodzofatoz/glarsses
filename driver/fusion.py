"""Head orientation from gyro + accelerometer (Mahony-style complementary filter).

Head frame (right-handed): x = right, y = up, z = backward (you look along -z).
Quaternions are (w, x, y, z) and map head-frame vectors into the world frame.
"""
import json, math, os

AXES_FILE = os.path.expanduser("~/.config/glarsses/axes.json")


def qmul(a, b):
    aw, ax, ay, az = a; bw, bx, by, bz = b
    return (aw*bw - ax*bx - ay*by - az*bz,
            aw*bx + ax*bw + ay*bz - az*by,
            aw*by - ax*bz + ay*bw + az*bx,
            aw*bz + ax*by - ay*bx + az*bw)


def qconj(q):
    return (q[0], -q[1], -q[2], -q[3])


def qnorm(q):
    n = math.sqrt(sum(c*c for c in q)) or 1.0
    return tuple(c / n for c in q)


def qrot(q, v):
    return qmul(qmul(q, (0.0, *v)), qconj(q))[1:]


def qaxis(axis, angle):
    s = math.sin(angle / 2)
    return (math.cos(angle / 2), axis[0]*s, axis[1]*s, axis[2]*s)


def qslerp(a, b, t):
    d = sum(x*y for x, y in zip(a, b))
    if d < 0:
        b = tuple(-c for c in b); d = -d
    if d > 0.9995:
        return qnorm(tuple(x + (y - x)*t for x, y in zip(a, b)))
    th = math.acos(d); s = math.sin(th)
    return tuple((math.sin((1-t)*th)*x + math.sin(t*th)*y) / s for x, y in zip(a, b))


def yaw_pitch_roll(q):
    """Degrees: yaw (+left), pitch (+up), roll (+left tilt) of a head->world quaternion."""
    fwd = qrot(q, (0, 0, -1)); up = qrot(q, (0, 1, 0))
    yaw = math.degrees(math.atan2(-fwd[0], -fwd[2]))
    pitch = math.degrees(math.asin(max(-1, min(1, fwd[1]))))
    right = qrot(q, (1, 0, 0))
    roll = math.degrees(math.atan2(right[1], up[1]))
    return yaw, pitch, roll


def yaw_only(q):
    fwd = qrot(q, (0, 0, -1))
    return qaxis((0, 1, 0), math.atan2(-fwd[0], -fwd[2]))


class AxisMap:
    """Maps sensor x/y/z into head-frame x/y/z. Learned by head_test.py (axes.json)."""
    def __init__(self, idx=(0, 1, 2), sign=(1, 1, 1)):
        self.idx, self.sign = idx, sign

    @classmethod
    def load(cls):
        try:
            d = json.load(open(AXES_FILE))
            return cls(tuple(d["idx"]), tuple(d["sign"]))
        except (OSError, KeyError, ValueError):
            return cls((0, 2, 1), (-1, 1, 1))  # XREAL Air 2 Pro

    def __call__(self, v):
        return tuple(self.sign[i] * v[self.idx[i]] for i in range(3))


class Fusion:
    def __init__(self, axis_map, kp=1.0):
        self.map = axis_map
        self.kp = kp
        self.bias = (0.0, 0.0, 0.0)
        self.q = (1.0, 0.0, 0.0, 0.0)
        self.last_ts = None

    def calibrate(self, samples):
        """samples: list of (ts, gyro, accel) taken while still. Sets bias and initial tilt."""
        n = len(samples)
        self.bias = tuple(sum(s[1][i] for s in samples) / n for i in range(3))
        a = self.map(tuple(sum(s[2][i] for s in samples) / n for i in range(3)))
        # rotate so measured "up" in head frame maps to world +y, yaw = 0
        an = math.sqrt(sum(c*c for c in a)) or 1
        a = tuple(c / an for c in a)
        axis = (-a[2], 0.0, a[0])  # cross(a, y_up)
        s = math.sqrt(axis[0]**2 + axis[2]**2)
        if s > 1e-6:
            ang = math.atan2(s, a[1])
            self.q = qaxis(tuple(c / s for c in axis), ang)
        self.q = qnorm(qmul(qconj(yaw_only(self.q)), self.q))
        self.last_ts = samples[-1][0]

    def update(self, ts, gyro, accel):
        if self.last_ts is None:
            self.last_ts = ts
            return self.q
        dt = (ts - self.last_ts) / 1e9
        self.last_ts = ts
        if dt <= 0 or dt > 0.1:
            return self.q
        w = self.map(tuple(math.radians(gyro[i] - self.bias[i]) for i in range(3)))
        a = self.map(accel)
        an = math.sqrt(sum(c*c for c in a))
        if 0.8 < an < 1.2:  # only trust accel when not accelerating hard
            a = tuple(c / an for c in a)
            v = qrot(qconj(self.q), (0, 1, 0))  # expected up in head frame
            e = (a[1]*v[2] - a[2]*v[1], a[2]*v[0] - a[0]*v[2], a[0]*v[1] - a[1]*v[0])
            w = tuple(w[i] + self.kp * e[i] for i in range(3))
        wn = math.sqrt(sum(c*c for c in w))
        if wn > 0:
            self.q = qnorm(qmul(self.q, qaxis(tuple(c / wn for c in w), wn * dt)))
        return self.q
