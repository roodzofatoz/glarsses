"""XREAL Air 2 Pro IMU reader (independent implementation, protocol per open community docs)."""
import os, select, struct, glob, time, zlib

def find_imu_hidraw(vid=0x3318, iface="input3"):
    for d in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            ue = open(d + "/device/uevent").read()
        except OSError:
            continue
        if f"{vid:08X}" in ue.upper() and ue.strip().endswith(iface) or (f"{vid:08X}" in ue.upper() and f"/{iface}" in ue):
            return "/dev/" + os.path.basename(d)
    return None

def _s24(b, o):
    return int.from_bytes(b[o:o+3], "little", signed=True)

def parse(b):
    """Return (timestamp_ns, gyro_dps(x,y,z), accel_g(x,y,z)) or None."""
    if len(b) < 42 or b[0] != 0x01 or b[1] != 0x02:
        return None
    ts = struct.unpack_from("<Q", b, 4)[0]
    gm = struct.unpack_from("<H", b, 12)[0]; gd = struct.unpack_from("<i", b, 14)[0]
    am = struct.unpack_from("<H", b, 27)[0]; ad = struct.unpack_from("<i", b, 29)[0]
    if gd == 0 or ad == 0:
        return None
    g = tuple(_s24(b, 18 + 3*i) * gm / gd for i in range(3))
    a = tuple(_s24(b, 33 + 3*i) * am / ad for i in range(3))
    return ts, g, a

class IMU:
    def __init__(self, path=None):
        self.path = path or find_imu_hidraw()
        if not self.path:
            raise RuntimeError("XREAL IMU hidraw device not found")
        try:
            self.fd = os.open(self.path, os.O_RDWR)
            self.writable = True
        except PermissionError:
            self.fd = os.open(self.path, os.O_RDONLY)
            self.writable = False

    def send_command(self, msg_id, payload=b""):
        """MCU command packet: 0xAA, crc32(len..), len = 3 + payload, msg id, payload (64 bytes)."""
        if not self.writable:
            return False
        length = 3 + len(payload)
        body = struct.pack("<HB", length, msg_id) + payload
        pkt = b"\xAA" + struct.pack("<I", zlib.crc32(body[:length]) & 0xFFFFFFFF) + body
        os.write(self.fd, pkt.ljust(64, b"\0"))
        return True

    def start_stream(self):
        return self.send_command(0x19, b"\x01")

    def read_available(self, silent_timeout=1.0):
        """All samples queued since the last call (non-blocking). Handles a silent stream
        by sending start-stream once; raises OSError if it stays silent."""
        if not getattr(self, "_nonblock", False):
            os.set_blocking(self.fd, False)
            self._nonblock = True
            self._last_data = time.monotonic()
            self._retried = False
        out = []
        while True:
            try:
                b = os.read(self.fd, 64)
            except BlockingIOError:
                break
            r = parse(b)
            if r:
                out.append(r)
        now = time.monotonic()
        if out:
            self._last_data = now
            self._retried = False
        elif now - self._last_data > silent_timeout:
            if self._retried:
                raise OSError("no IMU data from glasses")
            self.start_stream()
            self._retried = True
            self._last_data = now
        return out

    def read(self, timeout=1.0):
        """Next IMU sample. Sends the start-stream command if the glasses are silent;
        raises OSError if they stay silent (unplugged / not streaming)."""
        retried = False
        while True:
            if getattr(self, "_nonblock", False):
                os.set_blocking(self.fd, True)
                self._nonblock = False
            r, _, _ = select.select([self.fd], [], [], timeout)
            if not r:
                if retried:
                    raise OSError("no IMU data from glasses")
                self.start_stream()
                retried = True
                continue
            r = parse(os.read(self.fd, 64))
            if r: return r
    def close(self):
        os.close(self.fd)


# ---------------------------------------------------------------- buttons
# The glasses' MCU interface (USB HID interface 4) reports button presses on its own:
# packet 0xFD | crc32(4) | length(2) | timestamp(8) | msg id(2, LE) | reserved(5) | data...
# msg 0x6C05 = button pressed, data[0..3] (LE) = which button.
MSG_BUTTON_PRESSED = 0x6C05
BUTTONS = {1: "top", 2: "brightness_up", 3: "brightness_down"}


def parse_button(b):
    """Return "top" / "brightness_up" / "brightness_down" for a button packet, else None."""
    if len(b) < 26 or b[0] != 0xFD:
        return None
    if b[15] | (b[16] << 8) != MSG_BUTTON_PRESSED:
        return None
    return BUTTONS.get(int.from_bytes(b[22:26], "little"))


class Buttons:
    def __init__(self, path=None):
        self.path = path or find_imu_hidraw(iface="input4")
        if not self.path:
            raise OSError("XREAL MCU hidraw device not found")
        self.fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)

    def presses(self):
        """Button presses since the last call (non-blocking)."""
        out = []
        while True:
            try:
                b = os.read(self.fd, 64)
            except BlockingIOError:
                return out
            name = parse_button(b)
            if name:
                out.append(name)

    def close(self):
        os.close(self.fd)
