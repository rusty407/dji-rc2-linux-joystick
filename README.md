# dji-rc2-linux-joystick

Fly FPV sims on Linux with the DJI RC 2, the controller with the screen that
comes with the Mini 4 Pro.

I wanted to practice in FPV FreeRider on Linux with the RC 2 I already had.
There are a couple of tools for the older RC-N1 controller, but they're
Windows-only and none of them handle the RC 2. So I poked at it over USB until
the sticks came out, and this is the result.

It's a small Python script that reads the sticks over USB and creates a
virtual joystick. Games see a normal joystick (both `/dev/input/event*` and
the old `/dev/input/js*` interface), so FreeRider, other Unity games and SDL
games should all pick it up.

What you get:

- both sticks and both dials as axes, full range, around 90-95 updates a second
- the dials also work as 4 buttons, handy for reset/restart
- it survives unplugging: the sticks go to center, throttle drops to zero, and it reconnects by itself
- calibration and deadzones if you want them
- a `--debug` mode that shows the live values

It only ever asks the controller where the sticks are. It doesn't change any
settings on the RC 2 and never goes anywhere near the firmware.

> Not affiliated with DJI in any way. Use at your own risk, and keep the drone
> switched off while you're doing this.

## Setup

You'll need Linux with `uinput` and `joydev` (pretty much any desktop distro
has them), Python 3.9+, and a USB cable that actually carries data. A
charge-only cable is the number one reason the RC 2 won't show up. I've tested
it on Arch with an RC 2 from a Mini 4 Pro kit.

```sh
git clone https://github.com/rusty407/dji-rc2-linux-joystick.git
cd dji-rc2-linux-joystick
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
sudo ./install-udev.sh
```

The install script is the only part that needs root. It adds a udev rule that
lets your user talk to the RC 2 and create the virtual joystick (Steam uses
the same `/dev/uinput` rule), and makes the `uinput` module load at boot.

## Flying

1. Turn the RC 2 on and plug it in. Give it a few seconds: it shows up as
   `18d1:4ee7` while it boots and then as `2ca3:1021` once it's ready.
   `lsusb | grep 2ca3` will tell you which.

2. Start the bridge and leave it running:

   ```sh
   .venv/bin/python rc2_joystick.py --debug
   ```

   It prints which devices it made (like `/dev/input/js0`) and a live line
   with all the stick values.

3. Optional sanity check: run `jstest /dev/input/js0` and push the left stick
   up. Axis 2 should go to +32767.

4. Start the game *after* the bridge, since Unity games tend to only look for
   controllers at launch. Pick "DJI RC 2 Joystick", run the game's
   calibration and map the axes:

| Stick | Does | Axis |
|---|---|---|
| right, left/right | roll | 0 (`ABS_X`) |
| right, up/down | pitch | 1 (`ABS_Y`) |
| left, up/down | throttle | 2 (`ABS_Z`) |
| left, left/right | yaw | 3 (`ABS_RX`) |
| left dial | | 4 (`ABS_RY`) |
| right dial | | 5 (`ABS_RZ`) |

Up and right are positive, and throttle all the way down is the minimum. Turn
a dial most of the way to either end and it presses a button. The left dial is
buttons 0 and 1 and the right dial is 2 and 3 (jstest numbering).

### About the throttle

The RC 2's left stick springs back to the middle, so letting go means 50%
throttle. That's the hardware and there's not much software can do about it.
If it drives you nuts, run with `--throttle-half`. The middle becomes zero
throttle and pushing all the way up is full throttle. You lose half the
resolution, but it's a lot more forgiving.

## Options

```
--debug           live values + update rate
--rate HZ         how often to poll the sticks (default 100)
--throttle-half   resting throttle = 0 (see above)
--calibrate       measure your sticks and save the result
--config FILE     where calibration lives (default ~/.config/rc2-joystick/calibration.json)
```

You don't have to calibrate, because the defaults already match the RC 2
(364 / 1024 / 1684). If you do run `--calibrate`, keep your hands off for the
first few seconds, then swirl both sticks around and flick both dials to both
ends. The saved JSON also has a `deadzone` (0 to 1) and an `invert` setting per
axis if you want to tweak things.

## When it doesn't work

- **Stuck on "waiting for the RC 2"**: run `lsusb | grep 2ca3`. If nothing
  shows up, try another cable. If you see `18d1:4ee7`, it's still booting, so
  give it a moment.
- **"Permission denied on /dev/uinput"**: run `sudo ./install-udev.sh`, and
  start the bridge from your normal desktop session, not over SSH.
- **No `/dev/input/js0`**: `sudo modprobe joydev`.
- **The game doesn't see it**: start the bridge first, then the game.
- **`adb devices` shows the RC 2 as offline**: ignore it, that's a separate
  interface.

## How it works

The RC-N1 tools talk to the controller over a serial port, but the RC 2
doesn't have one. Plugged in, it shows three USB interfaces: MTP, ADB, and a
vendor "BULK Interface" (interface 0, endpoints `0x01` out and `0x81` in).
That bulk interface turns out to speak DJI's DUML protocol, the same thing the
RC-N1 sends over serial, so the RC-N1 stick request works as-is:

```
55 0d 04 33 0a 06 eb 34 40 06 01 74 24
```

That's command set `06`, command `01`, sent from the PC (`0a`) to the remote
(`06`). The reply is 32 bytes (the RC-N1's is 38). After a status byte there
are six little-endian 16-bit values, each followed by its channel number:

```
00 | 00 04 01 | 00 04 02 | 00 04 03 | 00 04 04 | 00 04 05 | 00 04 06
     ch1        ch2        ch3        ch4        ch5        ch6
```

In order, those are roll, pitch, throttle, yaw, left dial and right dial, at
packet offsets 12, 15, 18, 21, 24 and 27. The range is 364 to 1684 with 1024
in the middle. Unlike the RC-N1, you don't need to turn on simulator mode
first: the values are live straight away, and the RC 2 will happily answer
100+ requests a second. The physical buttons don't show up in this data,
which is why the dials double as buttons.

The controller also sends a few packets of its own about once a second:
`00/81` and `00/82`, whose payload contains "rc331" (the RC 2's model
number), and `09/43`. The bridge just ignores them.

If you want to dig further, `rc2_probe.py` is the tool I used to figure all
this out:

```sh
.venv/bin/python rc2_probe.py listen --seconds 3       # just listen, sends nothing
.venv/bin/python rc2_probe.py poll --seconds 3         # ask for sticks, hexdump the replies
.venv/bin/python rc2_probe.py capture --seconds 60 --out sweep.log
.venv/bin/python rc2_probe.py analyze sweep.log        # which bytes moved, and when
```

`capture_nosim.log` is my capture of moving each stick and dial one at a time,
in case you want to try `analyze` without a controller.

### Is it safe for the controller?

All USB traffic goes through `rc2link.py`, which refuses to send anything
except two packets. One is the stick request above. The other is the RC-N1's
"simulator mode on" (`06/24 01`); only `rc2_probe.py --sim` can send it, and
the RC 2 doesn't need it anyway. It never resets or reconfigures the USB
device, and it leaves the MTP and ADB interfaces alone.

## Files

```
rc2_joystick.py         the bridge
rc2link.py              USB link + the send allowlist
duml.py                 DUML packet building/parsing
rc2_probe.py            the poking-around tool
70-rc2-joystick.rules   udev rule (installed by install-udev.sh)
capture_nosim.log       sample capture
```

## Thanks

- [ricardoneves93/DJIRemoteToFPVSimulator](https://github.com/ricardoneves93/DJIRemoteToFPVSimulator)
  and [IvanYaky/DJI_RC-N1_SIMULATOR_FLY_DCL](https://github.com/IvanYaky/DJI_RC-N1_SIMULATOR_FLY_DCL)
  for working out the RC-N1 stick request
- the [o-gs/dji-firmware-tools](https://github.com/o-gs/dji-firmware-tools) people for documenting DUML

## License

MIT, see [LICENSE](LICENSE).
