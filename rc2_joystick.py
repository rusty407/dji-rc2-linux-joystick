#!/usr/bin/env python3
"""Use a DJI RC 2 as a Linux joystick (uinput), e.g. for FPV FreeRider.

Axes, -32768..32767, up/right = max, throttle all the way down = min:
    ABS_X roll    ABS_Y pitch    ABS_Z throttle    ABS_RX yaw
    ABS_RY left dial    ABS_RZ right dial
Turning a dial to either end also presses a button (4 in total).

    rc2_joystick.py [--debug] [--rate HZ] [--throttle-half] [--config FILE]
    rc2_joystick.py --calibrate
"""
import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

import usb.core
from evdev import AbsInfo, UInput
from evdev import ecodes as e

import rc2link
from rc2link import CHANNELS, RAW_CENTER, RAW_MAX, RAW_MIN

AXIS_CODES = {"roll": e.ABS_X, "pitch": e.ABS_Y, "throttle": e.ABS_Z, "yaw": e.ABS_RX,
              "left_dial": e.ABS_RY, "right_dial": e.ABS_RZ}
DIAL_BUTTONS = {"left_dial": (e.BTN_TRIGGER, e.BTN_THUMB), "right_dial": (e.BTN_THUMB2, e.BTN_TOP)}
DIAL_PRESS, DIAL_RELEASE = 0.8, 0.6  # fraction of dial travel, with hysteresis
OUT_MIN, OUT_MAX = -32768, 32767

DEADZONES = {"roll": 0.01, "pitch": 0.01, "throttle": 0.0, "yaw": 0.01, "left_dial": 0.02, "right_dial": 0.02}
DEFAULT_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "rc2-joystick" / "calibration.json"

REPLY_TIMEOUT = 0.1  # s to wait for the reply to one request
LINK_TIMEOUT = 1.0   # s without any reply before we treat the RC 2 as gone


class LinkLost(Exception):
    pass


def log(msg):
    print(f"\n[{time.strftime('%H:%M:%S')}] {msg}" if sys.stdout.isatty() else f"[{time.strftime('%H:%M:%S')}] {msg}",
          flush=True)


def load_calibration(path):
    cal = {ch: {"min": RAW_MIN, "center": RAW_CENTER, "max": RAW_MAX, "deadzone": DEADZONES[ch], "invert": False}
           for ch in CHANNELS}
    if path.exists():
        saved = json.loads(path.read_text())
        for ch in CHANNELS:
            c = dict(cal[ch])
            c.update({k: v for k, v in saved.get(ch, {}).items() if k in c})
            if c["min"] < c["center"] < c["max"] and 0 <= c["deadzone"] < 1:
                cal[ch] = c
            else:
                log(f"ignoring invalid calibration for {ch} in {path}: {saved.get(ch)}")
    return cal


def normalize(raw, c):
    """Raw RC value -> -1..1 using centre/min/max, inversion and deadzone."""
    if raw >= c["center"]:
        x = (raw - c["center"]) / (c["max"] - c["center"])
    else:
        x = (raw - c["center"]) / (c["center"] - c["min"])
    x = max(-1.0, min(1.0, x))
    if c["invert"]:
        x = -x
    dz = c["deadzone"]
    if abs(x) <= dz:
        return 0.0
    return (abs(x) - dz) / (1 - dz) * (1 if x > 0 else -1)


def to_output(x):
    return round((x + 1) / 2 * (OUT_MAX - OUT_MIN)) + OUT_MIN


def poll_sticks(rc, rate):
    """Yield raw channel tuples at up to `rate` Hz; raise LinkLost if the RC stops answering."""
    period = 1 / rate
    last_reply = time.monotonic()
    while True:
        t0 = time.monotonic()
        want = rc.send(rc2link.STICK_REQUEST).seq
        vals = None
        while vals is None and time.monotonic() - t0 < REPLY_TIMEOUT:
            for p in rc.packets(timeout_ms=20):
                if p.seq == want and rc2link.is_stick_reply(p):
                    vals = rc2link.stick_values(p)
        now = time.monotonic()
        if vals is not None:
            last_reply = now
            yield vals
        elif now - last_reply > LINK_TIMEOUT:
            raise LinkLost(f"no stick replies for {LINK_TIMEOUT:g} s")
        time.sleep(max(0.0, period - (time.monotonic() - t0)))


class VirtualJoystick:
    def __init__(self, cal, throttle_half):
        self.cal = cal
        self.throttle_half = throttle_half
        axes = [(AXIS_CODES[ch], AbsInfo(value=OUT_MIN if ch == "throttle" else 0, min=OUT_MIN, max=OUT_MAX,
                                         fuzz=0, flat=0, resolution=0)) for ch in CHANNELS]
        buttons = [code for pair in DIAL_BUTTONS.values() for code in pair]
        try:
            self.ui = UInput({e.EV_ABS: axes, e.EV_KEY: buttons}, name="DJI RC 2 Joystick",
                             vendor=rc2link.VID, product=rc2link.PID, version=1, bustype=e.BUS_USB,
                             phys="rc2_joystick", max_effects=0)  # no force feedback: rumble requests would stall
        except PermissionError:
            sys.exit("Permission denied on /dev/uinput. Install the udev rule (see README.md), "
                     "or run 'sudo modprobe uinput' if the module isn't loaded yet.")
        self.pressed = dict.fromkeys(buttons, False)
        self.axes = {}
        self.failsafe()

    def nodes(self):
        """/dev/input paths of the virtual device (event node plus js node, if joydev picked it up)."""
        ev = self.ui.device.path if self.ui.device else None
        if not ev:
            return []
        sysdir = Path("/sys/class/input") / Path(ev).name / "device"
        return [ev] + [f"/dev/input/{p.name}" for p in sorted(sysdir.glob("js*"))]

    def failsafe(self):
        """Sticks centred, throttle at minimum, buttons released."""
        for ch in CHANNELS:
            self._axis(ch, -1.0 if ch == "throttle" else 0.0)
        for code in self.pressed:
            self._button(code, False)
        self.ui.syn()

    def update(self, raw):
        for ch, r in zip(CHANNELS, raw):
            x = normalize(r, self.cal[ch])
            if ch == "throttle" and self.throttle_half:
                x = max(x, 0.0) * 2 - 1  # spring-centred stick resting = zero throttle
            self._axis(ch, x)
        for ch, (low, high) in DIAL_BUTTONS.items():
            x = self.axes[ch]
            self._button(low, x <= -(DIAL_RELEASE if self.pressed[low] else DIAL_PRESS))
            self._button(high, x >= (DIAL_RELEASE if self.pressed[high] else DIAL_PRESS))
        self.ui.syn()

    def _axis(self, ch, x):
        self.axes[ch] = x
        self.ui.write(e.EV_ABS, AXIS_CODES[ch], to_output(x))

    def _button(self, code, down):
        if self.pressed[code] != down:
            self.pressed[code] = down
            self.ui.write(e.EV_KEY, code, int(down))

    def close(self):
        self.ui.close()


class DebugView:
    LABELS = {"roll": "roll", "pitch": "pitch", "throttle": "thr", "yaw": "yaw", "left_dial": "dialL", "right_dial": "dialR"}

    def __init__(self):
        self.count = 0
        self.rate = 0.0
        self.t_rate = self.t_print = time.monotonic()
        self.end = "\r" if sys.stdout.isatty() else "\n"

    def show(self, raw, js):
        self.count += 1
        now = time.monotonic()
        if now - self.t_rate >= 1:
            self.rate, self.count, self.t_rate = self.count / (now - self.t_rate), 0, now
        if now - self.t_print < 0.1:
            return
        self.t_print = now
        cols = "  ".join(f"{self.LABELS[ch]} {r:4d}>{to_output(js.axes[ch]):+6d}" for ch, r in zip(CHANNELS, raw))
        btns = "".join("X" if v else "." for v in js.pressed.values())
        print(f"{self.rate:4.0f}Hz  {cols}  btn {btns}", end=self.end, flush=True)


def run_bridge(args):
    cal = load_calibration(args.config)
    js = VirtualJoystick(cal, args.throttle_half)
    log(f"virtual joystick 'DJI RC 2 Joystick' at {', '.join(js.nodes()) or '(device node not readable)'}")
    if not args.config.exists():
        log(f"no calibration file at {args.config}; using defaults {RAW_MIN}/{RAW_CENTER}/{RAW_MAX}")
    debug = DebugView() if args.debug else None
    waiting = False
    try:
        while True:
            try:
                rc = rc2link.RC2()
            except rc2link.NotFound:
                if not waiting:
                    log("waiting for the RC 2 (2ca3:1021); plug it in and power it on")
                    waiting = True
                time.sleep(1)
                continue
            except usb.core.USBError as exc:
                log(f"RC 2 found but not usable yet ({exc}); retrying")
                time.sleep(2)
                continue
            waiting = False
            log("RC 2 connected")
            try:
                for raw in poll_sticks(rc, args.rate):
                    js.update(raw)
                    if debug:
                        debug.show(raw, js)
            except (usb.core.USBError, LinkLost) as exc:
                log(f"RC 2 link lost ({exc}); sticks centred, throttle at minimum")
            finally:
                js.failsafe()
                rc.close()
            time.sleep(1)
    except KeyboardInterrupt:
        log("stopping")
    finally:
        js.close()


def run_calibration(args):
    cal = load_calibration(args.config)
    try:
        rc = rc2link.RC2()
    except rc2link.NotFound as exc:
        sys.exit(str(exc))
    try:
        print("Let go of both sticks and leave both dials alone. Measuring centre in 3 s...", flush=True)
        time.sleep(3)
        centre = sample(rc, 1.5)
        print("Now move BOTH sticks around their full circles and turn BOTH dials to both ends (10 s)...",
              flush=True)
        travel = sample(rc, 10, live=True)
    finally:
        rc.close()

    for i, ch in enumerate(CHANNELS):
        mid = round(statistics.median(v[i] for v in centre))
        lo, hi = min(v[i] for v in travel), max(v[i] for v in travel)
        if mid - lo < 300 or hi - mid < 300:
            print(f"  {ch:10s} not moved far enough (min {lo}, centre {mid}, max {hi}); keeping previous values")
            continue
        cal[ch].update(min=lo, center=mid, max=hi)
        print(f"  {ch:10s} min {lo}  centre {mid}  max {hi}")
    args.config.parent.mkdir(parents=True, exist_ok=True)
    args.config.write_text(json.dumps(cal, indent=2) + "\n")
    print(f"Saved {args.config} (edit 'deadzone' 0..1 and 'invert' there if needed)")


def sample(rc, seconds, live=False):
    out = []
    end = time.monotonic() + seconds
    for raw in poll_sticks(rc, 100):
        out.append(raw)
        if live and len(out) % 10 == 0:
            print(f"\r  {end - time.monotonic():4.1f}s  " + "  ".join(f"{v:4d}" for v in raw), end="", flush=True)
        if time.monotonic() >= end:
            break
    if live:
        print()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--debug", action="store_true", help="print live raw and output axis values")
    ap.add_argument("--rate", type=float, default=100, help="stick polling rate in Hz (default 100)")
    ap.add_argument("--throttle-half", action="store_true",
                    help="map only the upper half of the throttle stick (rest = zero throttle)")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help=f"calibration file (default {DEFAULT_CONFIG})")
    ap.add_argument("--calibrate", action="store_true", help="measure centre/min/max and save to --config")
    args = ap.parse_args()
    if args.calibrate:
        run_calibration(args)
    else:
        run_bridge(args)


if __name__ == "__main__":
    main()
