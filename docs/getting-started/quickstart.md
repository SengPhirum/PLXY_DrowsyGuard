---
title: Quickstart
---

# Quickstart

Two paths. The first needs nothing but a laptop; the second assumes a board that
is already [wired up](../tutorials/hardware-setup/README.md).

## 1. On a laptop, with a webcam

```bash
pip install -e ".[live]"
drowsyguard fetch-models
python -m drowsyguard.cli live      # open http://127.0.0.1:8000
```

The page shows the camera feed, both eye crops with P(closed) per eye, the
PERCLOS bar, and the streak/cooldown state of the **same** decision logic the
firmware runs — `drowsyguard.risk.RiskFilter` mirrors `risk_filter.cpp`, and
`tests/test_firmware_parity.py` keeps them honest.

The trigger/required/cooldown sliders tune that logic live, and **Copy as C++**
emits the constructor line to paste into
`firmware/esp32s3/main/risk_filter.h`. That is the whole point of the dashboard:
thresholds tuned here transfer to the device unchanged.

To replay a recording at its native frame rate instead of a webcam, which makes
threshold comparisons repeatable:

```bash
python -m drowsyguard.cli live --source path/to/clip.mp4
```

Full detail: [Live dashboard](../guide/live-dashboard.md).

## 2. On the board

`plxy.sh` is the single entry point for the firmware loop. It exists because two
things bite on this hardware: ESP-IDF refuses to run under Git Bash at all (it
aborts on `MSYSTEM`), and this board's UART bridge cannot drive the chip into
download mode by itself.

```bash
./plxy.sh doctor     # toolchain, serial port and device check
./plxy.sh dev        # build, flash, monitor
```

When `flash` reports `Wrong boot mode detected (0x28)`, hold **BOOT**, tap
**RESET**, release **BOOT**, and let it retry — the bridge drives EN but not
GPIO0, so no reset sequence can reach download mode on its own.

Then join the board:

```bash
./plxy.sh wifi       # prints the SSID, password and URL
./plxy.sh open       # opens the preview in your browser
./plxy.sh watch      # live risk / PERCLOS / fps, once a second
```

The board raises an access point named `DrowsyGuard-XXXXXX` (the suffix is from
its MAC) with the password `drowsyguard`, and serves everything at
`http://192.168.4.1/`.

Full detail: [Firmware dev loop](../guide/dev-loop.md) and
[Using the device](../guide/device.md).

## 3. Check it speaks

With the amplifier wired and a client joined:

```bash
./plxy.sh alert microsleep     # play one warning
./plxy.sh mute                 # silence the speaker
./plxy.sh unmute
```

If the board answers but nothing is audible, work through
[Troubleshooting](../troubleshooting.md#the-board-is-silent).

## Where next

| You want to | Go to |
| --- | --- |
| Understand every `plxy.sh` command | [Firmware dev loop](../guide/dev-loop.md) |
| Train your own model | [Datasets](../guide/datasets.md) then [Training](../guide/training.md) |
| Change a pin, a threshold or the Wi-Fi password | [Configuration](../configuration/index.md) |
| Script against the board | [Device HTTP API](../reference/device-api.md) |
| Know what the numbers mean | [On-device pipeline](../FIRMWARE_PIPELINE.md) |
