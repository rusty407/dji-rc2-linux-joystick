"""Talks to the DJI RC 2 over USB.

No serial port on this one. Once it's booted it shows up as 2ca3:1021 and
interface 0 ("BULK Interface", EP 0x01/0x81) speaks DUML.

send() only lets the commands in ALLOWED through.
"""
import struct

import usb.core
import usb.util

import duml

VID, PID = 0x2CA3, 0x1021
IFACE, EP_OUT, EP_IN = 0, 0x01, 0x81
PC, RC = 0x0A, 0x06

STICK_REQUEST = (0x06, 0x01, b"")
SIM_MODE_ON = (0x06, 0x24, b"\x01")
ALLOWED = {STICK_REQUEST, SIM_MODE_ON}

# The 06/01 reply carries six u16 LE values (RC channels 1-6) at these payload offsets.
CHANNELS = ("roll", "pitch", "throttle", "yaw", "left_dial", "right_dial")
_VALUE_OFFSETS = (1, 4, 7, 10, 13, 16)
RAW_MIN, RAW_CENTER, RAW_MAX = 364, 1024, 1684


class NotFound(Exception):
    pass


class RC2:
    def __init__(self):
        self.dev = usb.core.find(idVendor=VID, idProduct=PID)
        if self.dev is None:
            raise NotFound(f"RC 2 ({VID:04x}:{PID:04x}) not found; is it powered on and fully booted?")
        if self.dev.is_kernel_driver_active(IFACE):
            raise RuntimeError(f"a kernel driver owns interface {IFACE}; refusing to detach it")
        usb.util.claim_interface(self.dev, IFACE)
        self.seq = 0x34EB
        self.parser = duml.Parser()

    def close(self):
        try:
            usb.util.release_interface(self.dev, IFACE)
        except usb.core.USBError:
            pass  # already unplugged
        usb.util.dispose_resources(self.dev)

    def send(self, cmd):
        """Send an allowlisted command; returns the sent packet (its .seq is echoed in the reply)."""
        if cmd not in ALLOWED:
            raise RuntimeError(f"refusing to send non-allowlisted command {cmd!r}")
        cmd_set, cmd_id, payload = cmd
        pkt = duml.build(PC, RC, self.seq, 0x40, cmd_set, cmd_id, payload)
        self.seq = (self.seq + 1) & 0xFFFF
        self.dev.write(EP_OUT, pkt, timeout=200)
        return duml.Packet(pkt)

    def read(self, timeout_ms):
        """One bulk read; returns raw bytes (empty on timeout)."""
        try:
            return bytes(self.dev.read(EP_IN, 16384, timeout=timeout_ms))
        except usb.core.USBTimeoutError:
            return b""

    def packets(self, timeout_ms):
        return self.parser.feed(self.read(timeout_ms))


def is_stick_reply(p):
    return p.is_response and (p.cmd_set, p.cmd_id) == STICK_REQUEST[:2]


def stick_values(p):
    """Raw channel values (364..1684, in CHANNELS order) from a 06/01 reply, or None if malformed."""
    if len(p.payload) < _VALUE_OFFSETS[-1] + 2:
        return None
    return tuple(struct.unpack_from("<H", p.payload, o)[0] for o in _VALUE_OFFSETS)
