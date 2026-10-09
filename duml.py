"""DJI DUML v1 packet framing, as used by the RC-N1 simulator tools.

Packet layout (little-endian):
    0     0x55 magic
    1-2   length (10 bits, whole packet incl. CRC16) | version << 10 (version = 1)
    3     CRC8 of bytes 0-2 (seed 0x77)
    4     sender   (0x0a = PC)
    5     receiver (0x06 = remote controller)
    6-7   sequence number
    8     command type (0x40 = request; bit 7 set = response)
    9     command set
    10    command id
    11..  payload
    -2..  CRC16 of everything before it (seed 0x3692)
"""
import struct

HEADER_LEN = 11
MIN_LEN = HEADER_LEN + 2


def _reflected_table(poly, width):
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ poly if c & 1 else c >> 1
        table.append(c & ((1 << width) - 1))
    return table


# Same tables the reference tools hard-code: CRC-8/Maxim and CRC-16/CCITT, both reflected.
_CRC8 = _reflected_table(0x8C, 8)
_CRC16 = _reflected_table(0x8408, 16)


def crc8(data, seed=0x77):
    c = seed
    for b in data:
        c = _CRC8[(b ^ c) & 0xFF]
    return c


def crc16(data, seed=0x3692):
    c = seed
    for b in data:
        c = (c >> 8) ^ _CRC16[(b ^ c) & 0xFF]
    return c


def build(sender, receiver, seq, cmd_type, cmd_set, cmd_id, payload=b""):
    length = MIN_LEN + len(payload)
    head = bytearray(b"\x55") + struct.pack("<H", length | (1 << 10))
    head.append(crc8(head))
    pkt = head + struct.pack("<BBHBBB", sender, receiver, seq & 0xFFFF, cmd_type, cmd_set, cmd_id) + payload
    return bytes(pkt + struct.pack("<H", crc16(pkt)))


class Packet:
    __slots__ = ("raw", "sender", "receiver", "seq", "cmd_type", "cmd_set", "cmd_id", "payload")

    def __init__(self, raw):
        self.raw = bytes(raw)
        self.sender, self.receiver, self.seq, self.cmd_type, self.cmd_set, self.cmd_id = \
            struct.unpack_from("<BBHBBB", raw, 4)
        self.payload = self.raw[HEADER_LEN:-2]

    @property
    def is_response(self):
        return bool(self.cmd_type & 0x80)

    def __repr__(self):
        return (f"{self.sender:02x}->{self.receiver:02x} seq={self.seq:04x} type={self.cmd_type:02x} "
                f"cmd={self.cmd_set:02x}/{self.cmd_id:02x} len={len(self.raw)} payload={self.payload.hex(' ')}")


class Parser:
    """Stream parser: feed raw bytes, get back CRC-validated packets. Skips garbage."""

    def __init__(self):
        self.buf = bytearray()
        self.skipped = 0

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            start = self.buf.find(0x55)
            if start < 0:
                self.skipped += len(self.buf)
                self.buf.clear()
                return out
            if start:
                self.skipped += start
                del self.buf[:start]
            if len(self.buf) < 4:
                return out
            if crc8(self.buf[:3]) != self.buf[3]:
                self.skipped += 1
                del self.buf[:1]
                continue
            length = struct.unpack_from("<H", self.buf, 1)[0] & 0x3FF
            if length < MIN_LEN:
                self.skipped += 1
                del self.buf[:1]
                continue
            if len(self.buf) < length:
                return out
            raw = self.buf[:length]
            if crc16(raw[:-2]) != struct.unpack_from("<H", raw, length - 2)[0]:
                self.skipped += 1
                del self.buf[:1]
                continue
            out.append(Packet(raw))
            del self.buf[:length]
