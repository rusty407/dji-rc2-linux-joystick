#!/usr/bin/env bash
# Run once with sudo so rc2_joystick.py works as your normal user.
# Installs the udev rule and makes uinput load at boot.
set -euo pipefail
cd "$(dirname "$0")"
if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo: sudo $0" >&2
    exit 1
fi

install -m 0644 70-rc2-joystick.rules /etc/udev/rules.d/70-rc2-joystick.rules
echo uinput > /etc/modules-load.d/rc2-joystick-uinput.conf

udevadm control --reload-rules
modprobe uinput
udevadm trigger --action=change --subsystem-match=misc --sysname-match=uinput
udevadm trigger --action=change --subsystem-match=usb --attr-match=idVendor=2ca3
udevadm settle

echo "Installed. Current access:"
getfacl -p /dev/uinput 2>/dev/null | grep -E '^(# file|user)'
for d in /sys/bus/usb/devices/*; do
    if [[ "$(cat "$d/idVendor" 2>/dev/null)" == 2ca3 ]]; then
        node=$(printf '/dev/bus/usb/%03d/%03d' "$(cat "$d/busnum")" "$(cat "$d/devnum")")
        getfacl -p "$node" 2>/dev/null | grep -E '^(# file|user)'
    fi
done
