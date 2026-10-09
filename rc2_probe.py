#!/usr/bin/env python3
"""Poke at the RC 2's USB bulk interface. Used to figure out the stick data.

Only sends the stick request (06/01), plus simulator mode (06/24 01) with --sim.

    rc2_probe.py listen  [--seconds N]           # just listen, sends nothing
    rc2_probe.py poll    [--seconds N] [--sim]   # ask for sticks, hexdump replies
    rc2_probe.py capture [--seconds N] [--sim] --out FILE
    rc2_probe.py analyze FILE
"""
import argparse
import statistics
import struct
import sys
import time

import duml
import rc2link
from rc2link import EP_IN, SIM_MODE_ON, STICK_REQUEST, is_stick_reply


def cmd_listen(rc, args):
    print(f"Listening on EP {EP_IN:#04x} for {args.seconds}s, sending nothing...")
    end = time.monotonic() + args.seconds
    total = 0
    while time.monotonic() < end:
        raw = rc.read(250)
        if not raw:
            continue
        total += len(raw)
        print(f"raw[{len(raw)}]: {raw[:96].hex(' ')}{' ...' if len(raw) > 96 else ''}")
        for p in rc.parser.feed(raw):
            print("  DUML", p)
    print(f"Done: {total} bytes received, {rc.parser.skipped} non-DUML bytes skipped.")


def poll_loop(rc, args, on_packet):
    if args.sim:
        print("Sending simulator-mode enable:", rc.send(SIM_MODE_ON).raw.hex(" "))
        for p in rc.parser.feed(rc.read(200)):
            on_packet(p)
    end = time.monotonic() + args.seconds
    sent = 0
    while time.monotonic() < end:
        rc.send(STICK_REQUEST)
        sent += 1
        deadline = time.monotonic() + args.interval
        while (left := deadline - time.monotonic()) > 0:
            raw = rc.read(max(1, int(left * 1000)))
            for p in rc.parser.feed(raw):
                on_packet(p)
    return sent


def cmd_poll(rc, args):
    first = None
    stick = other = 0

    def on_packet(p):
        nonlocal first, stick, other
        if not is_stick_reply(p):
            other += 1
            print("other", p)
            return
        stick += 1
        if first is None:
            first = p.payload
            print(f"first stick response ({len(p.raw)} bytes):", p)
            return
        # Mark bytes that differ from the first response.
        marks = "".join("^^ " if i < len(first) and b != first[i] else "   " for i, b in enumerate(p.payload))
        print(f"{p.payload.hex(' ')}\n{marks.rstrip()}" if marks.strip() else p.payload.hex(" "))

    sent = poll_loop(rc, args, on_packet)
    print(f"Done: {sent} requests sent, {stick} stick responses, {other} other packets, "
          f"{rc.parser.skipped} non-DUML bytes skipped.")


def cmd_capture(rc, args):
    t0 = time.monotonic()
    stick = 0
    last_print = 0
    with open(args.out, "w") as f:
        def on_packet(p):
            nonlocal stick, last_print
            t = time.monotonic() - t0
            if is_stick_reply(p):
                stick += 1
                f.write(f"{t:.3f} {p.payload.hex()}\n")
            else:
                f.write(f"# {t:.3f} other {p!r}\n")
            if t - last_print >= 1:
                last_print = t
                print(f"\r{t:5.1f}s  {stick} stick responses", end="", flush=True)

        sent = poll_loop(rc, args, on_packet)
    print(f"\nDone: {sent} requests, {stick} stick responses -> {args.out}")


def cmd_analyze(path):
    rows = []
    for line in open(path):
        if not line.startswith("#"):
            t, hexpayload = line.split()
            rows.append((float(t), bytes.fromhex(hexpayload)))
    if not rows:
        sys.exit("no stick responses in capture")
    n = min(len(p) for _, p in rows)
    print(f"{len(rows)} responses over {rows[-1][0] - rows[0][0]:.1f}s, payload length {n}"
          f"{'' if n == max(len(p) for _, p in rows) else ' (varies!)'}")
    changing = [i for i in range(n) if len({p[i] for _, p in rows}) > 1]
    print("changing byte offsets (payload-relative):", changing or "none")
    seen = set()
    for i in changing:
        if i in seen:
            continue
        # Treat a changing byte plus its neighbour as a 16-bit little-endian value.
        lo = i if i + 1 < n and (i + 1 in changing or i - 1 not in changing) else i - 1
        if lo < 0 or lo + 1 >= n:
            continue
        seen.update((lo, lo + 1))
        vals = [struct.unpack_from("<H", p, lo)[0] for _, p in rows]
        base = statistics.median(vals[:20])
        active = [t for (t, _), v in zip(rows, vals) if abs(v - base) > 30]
        span = f"active {active[0]:.1f}s-{active[-1]:.1f}s" if active else "never far from rest"
        print(f"  u16 @ payload[{lo}:{lo + 2}] (packet offset {lo + duml.HEADER_LEN}): "
              f"min {min(vals)}  rest {base:.0f}  max {max(vals)}  {span}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["listen", "poll", "capture", "analyze"])
    ap.add_argument("file", nargs="?", help="capture file for analyze")
    ap.add_argument("--seconds", type=float, default=3)
    ap.add_argument("--interval", type=float, default=0.05, help="seconds between stick requests")
    ap.add_argument("--sim", action="store_true", help="send simulator-mode enable (06/24 01) first")
    ap.add_argument("--out", default="capture.log")
    args = ap.parse_args()

    if args.mode == "analyze":
        return cmd_analyze(args.file or args.out)

    try:
        rc = rc2link.RC2()
    except rc2link.NotFound as exc:
        sys.exit(str(exc))
    try:
        {"listen": cmd_listen, "poll": cmd_poll, "capture": cmd_capture}[args.mode](rc, args)
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        rc.close()


if __name__ == "__main__":
    main()
