"""Minimal pure-Python Wayland client for gamescope's gamescope_reshade protocol (v1).

Requests: destroy=0, set_effect(path)=1, enable_effect=2, set_uniform_variable(key, array)=3, disable_effect=4
Event:    effect_ready(path)=0
"""
import glob, os, socket, struct

WL_DISPLAY = 1


def _pad(b):
    return b + b"\0" * ((4 - len(b) % 4) % 4)


def _str(s):
    b = s.encode() + b"\0"
    return struct.pack("<I", len(b)) + _pad(b)


def _arr(b):
    return struct.pack("<I", len(b)) + _pad(b)


def find_gamescope_socket():
    rt = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    socks = [p for p in glob.glob(os.path.join(rt, "gamescope-[0-9]*")) if not p.endswith((".lock", "-ei"))]
    live = []
    for p in socks:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(p); live.append(p)
        except OSError:
            pass
        finally:
            s.close()
    return max(live, key=os.path.getmtime) if live else None


class Reshade:
    def __init__(self, path=None):
        self.path = path or find_gamescope_socket()
        if not self.path:
            raise RuntimeError("no running gamescope wayland socket found")
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.path)
        self.next_id = 2
        self.ready_effect = None
        self.buf = b""
        self.registry = self._new_id()
        self._send(WL_DISPLAY, 1, struct.pack("<I", self.registry))  # get_registry
        globals_ = self._roundtrip()
        name = next((n for n, (iface, _) in globals_.items() if iface == "gamescope_reshade"), None)
        if name is None:
            raise RuntimeError("gamescope does not expose gamescope_reshade")
        self.obj = self._new_id()
        self._send(self.registry, 0, struct.pack("<I", name) + _str("gamescope_reshade") + struct.pack("<II", 1, self.obj))

    def _new_id(self):
        i = self.next_id; self.next_id += 1; return i

    def _send(self, obj, opcode, payload=b""):
        size = 8 + len(payload)
        self.sock.sendall(struct.pack("<II", obj, (size << 16) | opcode) + payload)

    def _read_events(self, block=True):
        self.sock.setblocking(block)
        try:
            data = self.sock.recv(65536)
            if not data:
                raise RuntimeError("gamescope closed the connection")
            self.buf += data
        except BlockingIOError:
            pass
        finally:
            self.sock.setblocking(True)
        events = []
        while len(self.buf) >= 8:
            obj, so = struct.unpack_from("<II", self.buf)
            size, op = so >> 16, so & 0xFFFF
            if len(self.buf) < size:
                break
            events.append((obj, op, self.buf[8:size]))
            self.buf = self.buf[size:]
        return events

    def _roundtrip(self):
        cb = self._new_id()
        self._send(WL_DISPLAY, 0, struct.pack("<I", cb))  # sync
        globals_ = {}
        while True:
            for obj, op, body in self._read_events():
                if obj == WL_DISPLAY and op == 0:
                    _, code = struct.unpack_from("<II", body)
                    msg = body[12:12 + struct.unpack_from("<I", body, 8)[0] - 1].decode(errors="replace")
                    raise RuntimeError(f"wayland error {code}: {msg}")
                if obj == self.registry and op == 0:
                    name, ln = struct.unpack_from("<II", body)
                    iface = body[8:8 + ln - 1].decode()
                    ver = struct.unpack_from("<I", body, 8 + ((ln + 3) & ~3))[0]
                    globals_[name] = (iface, ver)
                if obj == cb:
                    return globals_

    def set_effect(self, path, wait=True, timeout=10.0):
        self._send(self.obj, 1, _str(path))
        if not wait:
            return True
        import time
        end = time.time() + timeout
        while time.time() < end:
            time.sleep(0.02)
            for obj, op, body in self._read_events(block=False):
                if obj == self.obj and op == 0:
                    return True
                if obj == WL_DISPLAY and op == 0:
                    raise RuntimeError("wayland protocol error while loading effect")
        return False

    def enable(self):
        self._send(self.obj, 2)

    def disable(self):
        self._send(self.obj, 4)

    def set_floats(self, key, values):
        self._send(self.obj, 3, _str(key) + _arr(struct.pack(f"<{len(values)}f", *values)))

    def pump(self):
        """Drain pending events so the socket buffer never fills. Records effect_ready."""
        for obj, op, body in self._read_events(block=False):
            if obj == self.obj and op == 0:
                n = struct.unpack_from("<I", body)[0]
                self.ready_effect = body[4:4 + n - 1].decode(errors="replace")

    def close(self):
        try:
            self.disable()
        except OSError:
            pass
        self.sock.close()
