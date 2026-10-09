# DJI RC 2 → Linux joystick

Use the **DJI RC 2**, the screen remote that ships with the DJI Mini 4 Pro, as
a joystick on Linux for FPV simulators like **FPV FreeRider**.

A small Python bridge reads the sticks over USB and exposes them as a virtual
joystick through `uinput`. It shows up as both `/dev/input/event*` and the
legacy `/dev/input/js*`, so it works with SDL, Unity and older joystick-API
games alike.

The community simulator bridges I found are for the RC-N1 (the remote
without a screen) on Windows. As far as I know, this is the first tool that
supports the RC 2.

- 4 stick axes plus both dials, full range −32768 … 32767; throttle stick bottom = min
- The dials double as 4 buttons (each end of each dial)
- About 95 Hz polling, calibration, deadzones, per-axis invert
- Auto-reconnect: if the RC 2 is unplugged, the virtual joystick stays up with sticks centred and throttle at minimum, and the bridge reconnects when the RC 2 comes back
- `--debug` live view of raw and output values
- Read-only: it only ever asks the remote for stick positions (see [Safety](#safety))

## Requirements

- Linux with the `uinput` and `joydev` kernel modules (standard on desktop distros)
- Python 3.9+ and libusb 1.0
- A USB **data** cable. With a charge-only cable the RC 2 doesn't show up at all.
- Optional: `evtest` and `jstest` (from `joyutils` / `joystick`) for checking the device

Tested on Arch Linux (kernel 7.1, Python 3.14) with an RC 2 from a Mini 4 Pro kit.

## Setup

```sh
git clone https://github.com/rusty407/dji-rc2-linux-joystick.git
cd dji-rc2-linux-joystick
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
sudo ./install-udev.sh
```

`install-udev.sh` installs `70-rc2-joystick.rules`. That rule gives the
logged-in desktop user access to the RC 2 and to `/dev/uinput`; Steam ships
the same `uinput` rule. The script also loads `uinput` now and at every boot.
After that, nothing needs root.

Then:

1. **Connect the RC 2.** Power it on and plug it into the computer. While
   booting it shows up as `18d1:4ee7`, then about 15 s later as `2ca3:1021`;
   only the second one works (`lsusb | grep 2ca3`). Keep the drone switched
   off.
2. **Run the bridge:**

   ```sh
   .venv/bin/python rc2_joystick.py --debug
   ```

   It prints the device nodes it created (for example `/dev/input/js0`) and a
   live line per axis showing `raw>output`. Leave it running while you play.
3. **Check it:**

   ```sh
   jstest /dev/input/js0
   ```

   Push the left stick up and axis 2 should go to +32767. Right stick right
   should send axis 0 positive.
4. **In the game.** Start the bridge *before* the game; Unity games usually
   only look for joysticks at launch. Pick **"DJI RC 2 Joystick"**, run the
   game's calibration, and assign the axes from the table below.

## Mapping

| RC 2 control | Function (Mode 2) | Virtual axis | jstest axis |
|---|---|---|---|
| right stick left/right | roll | `ABS_X` | 0 |
| right stick up/down | pitch | `ABS_Y` | 1 |
| left stick up/down | throttle | `ABS_Z` | 2 |
| left stick left/right | yaw | `ABS_RX` | 3 |
| left dial (gimbal) | — | `ABS_RY` | 4 |
| right dial (camera) | — | `ABS_RZ` | 5 |

Up and right = positive. The order follows RC transmitters' AETR order,
which FPV simulators are used to.

The RC 2 doesn't report its physical buttons over this interface, so the
dials act as buttons. Turn one past 80 % of its travel to press, and back
below 60 % to release:

| Button | jstest | Trigger |
|---|---|---|
| `BTN_TRIGGER` | 0 | left dial, one end |
| `BTN_THUMB` | 1 | left dial, other end |
| `BTN_THUMB2` | 2 | right dial, one end |
| `BTN_TOP` | 3 | right dial, other end |

## Options

| Flag | Effect |
|---|---|
| `--debug` | live raw and output values plus the poll rate |
| `--rate HZ` | stick polling rate (default 100) |
| `--throttle-half` | the RC 2 throttle stick springs back to centre, so by default releasing it gives 50 % throttle. This maps only the upper half of the stick, so a released stick = zero throttle (at half the resolution). |
| `--calibrate` | measure centre/min/max and save to the config file |
| `--config FILE` | calibration file (default `~/.config/rc2-joystick/calibration.json`) |

The defaults already match the RC 2 exactly (364 / 1024 / 1684), so
calibration is optional. `--calibrate` asks you to leave the sticks alone for
a few seconds, then to move everything to its limits. In the saved JSON you
can also tune each axis's `deadzone` (0 … 1) and `invert` (true/false).
Default deadzones are 1 % on roll, pitch and yaw, none on throttle, and 2 % on
the dials.

To keep it running in the background, start it from your desktop's
autostart, or with any process manager you like. It waits for the RC 2 and
reconnects by itself.

## Safety

This tool never writes to the remote's settings or firmware.
`rc2link.py` refuses to send anything except two allowlisted packets:

- `06/01`: request stick positions. This is the only thing the bridge sends.
- `06/24 01`: enable simulator mode. The RC-N1 tools need it; the RC 2
  doesn't, and only the reverse-engineering tool can send it, with
  `rc2_probe.py --sim`.

It never resets or reconfigures the USB device or detaches kernel drivers. It
only claims the RC 2's vendor bulk interface; the MTP and ADB interfaces are
left alone.

**Disclaimer:** this is an unofficial project, not affiliated with or endorsed
by DJI. Use it at your own risk, and never with a drone that is powered on.

## How it works / protocol notes

Unlike the RC-N1, the RC 2 has **no serial port**. Once booted it enumerates
as `2ca3:1021` with three USB interfaces:

| # | Class | Name | Endpoints |
|---|---|---|---|
| 0 | vendor `ff/43/01` | BULK Interface | `0x01` OUT, `0x81` IN (bulk, 512 bytes) |
| 1 | `06/01/01` | MTP | — |
| 2 | vendor `ff/42/01` | ADB Interface | — (shows as `offline` in `adb devices`) |

Interface 0 carries DJI **DUML v1** packets, the same framing the RC-N1 uses
over its serial port: `0x55`, 10-bit length plus version, CRC8 header
checksum (seed `0x77`), sender, receiver, sequence number, command type /
set / id, payload, and a CRC16 (seed `0x3692`). The bridge sends
`0a → 06, type 0x40, cmd 06/01` with no payload. The RC 2 answers with a
32-byte reply (the RC-N1's is 38) that echoes the sequence number. Its payload
holds six little-endian `u16` channel values at packet offsets 12, 15, 18, 21,
24 and 27, each followed by a channel index byte:

```
payload: 00 | 00 04 01 | 00 04 02 | 00 04 03 | 00 04 04 | 00 04 05 | 00 04 06
              ch1=1024   ch2        ch3        ch4        ch5        ch6
```

Values range from 364 to 1684 with 1024 at centre. They update live without
simulator mode, and the RC 2 answers more than 100 requests per second.
Unprompted, it also sends `0d → 2a cmd 00/81` and `00/82` (payload starting
with `rc331`, the RC 2's model number) and `0e → 0a cmd 09/43`, about once per
second each.

`rc2_probe.py` is the tool used to work this out:

```sh
.venv/bin/python rc2_probe.py listen --seconds 3          # passive: sends nothing
.venv/bin/python rc2_probe.py poll --seconds 3            # request sticks, hexdump replies
.venv/bin/python rc2_probe.py capture --seconds 60 --out sweep.log
.venv/bin/python rc2_probe.py analyze sweep.log           # find which bytes move
```

`capture_nosim.log` is a real capture of each stick and dial being moved in
turn, which you can feed to `analyze`.

## Troubleshooting

- **`waiting for the RC 2`**: run `lsusb | grep 2ca3`. Nothing at all
  usually means a charge-only cable. `18d1:4ee7` means it's still booting.
- **`Permission denied on /dev/uinput`**: run `sudo ./install-udev.sh`, and
  run the bridge from your logged-in desktop session (the rule follows the
  active seat).
- **No `/dev/input/js*`**: `sudo modprobe joydev`.
- **The game doesn't see the joystick**: start the bridge first, then the game.

## Files

| File | Purpose |
|---|---|
| `rc2_joystick.py` | the bridge: virtual joystick, calibration, reconnect, debug view |
| `rc2link.py` | USB link to the RC 2: allowlisted send, reply parsing, channel offsets |
| `duml.py` | DJI DUML packet building/parsing with CRC8/CRC16 |
| `rc2_probe.py` | reverse-engineering tool: listen / poll / capture / analyze |
| `70-rc2-joystick.rules`, `install-udev.sh` | permissions so it runs without root |
| `capture_nosim.log` | sample stick capture |

## Credits

- [ricardoneves93/DJIRemoteToFPVSimulator](https://github.com/ricardoneves93/DJIRemoteToFPVSimulator)
  and [IvanYaky/DJI_RC-N1_SIMULATOR_FLY_DCL](https://github.com/IvanYaky/DJI_RC-N1_SIMULATOR_FLY_DCL)
  worked out the RC-N1 stick request this project builds on.
- The DUML framing and checksums come from the
  [o-gs/dji-firmware-tools](https://github.com/o-gs/dji-firmware-tools) community.

## License

[MIT](LICENSE)
