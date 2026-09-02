---
title: Firmware dev loop
---

# Firmware dev loop

`plxy.sh` is the one entry point for the board. The usual command is:

```bash
./plxy.sh dev        # build, flash and open the monitor
./plxy.sh help       # every command
```

## Why the wrapper exists

It is not a convenience alias. Three things on this hardware and this toolchain
do not work the way their documentation assumes:

1. **ESP-IDF cannot run under Git Bash.** `idf.py` and `idf_tools.py` both abort
   with *"MSys/Mingw is not supported"* the moment they see `MSYSTEM` — and that
   guard is correct, because MSYS rewrites anything path-shaped as it crosses
   into a native process, corrupting compiler command lines. `plxy.sh` keeps the
   bash front end and hands the actual build to PowerShell.
2. **`export.ps1` derives its virtualenv name from whichever `python` is first
   on `PATH`.** This project ships a `.venv` on Python 3.13, so export computes
   `idf5.5_py3.13_env` — an environment that was never installed — and dies with
   *"ESP-IDF Python virtual environment not found"*, which reads like a broken
   IDF install rather than a `PATH`-shadowing problem. The wrapper scrubs the
   project tree off `PATH` and asks `idf_tools.py` for the real environment.
3. **Neither USB interface behaves as expected.** On the CH343 UART bridge the
   reset line reaches EN but not GPIO0, so nothing can drive the chip into
   download mode and flashing fails with *"Wrong boot mode detected (0x28)"*.
   On the native USB-Serial/JTAG interface the opposite holds: it sits in the ROM
   loader after every reset, so the monitor shows nothing but
   *"waiting for download"*.

## Commands

### Firmware

| Command | Does |
| --- | --- |
| `dev` | build, flash and monitor — the usual one |
| `build` | compile only |
| `flash` | flash without opening the monitor |
| `monitor` | open the serial console (<kbd>Ctrl</kbd>+<kbd>]</kbd> to exit) |
| `reconfigure` | re-resolve managed components |
| `menuconfig` | the ESP-IDF config UI |
| `size` | binary size breakdown |
| `clean` / `fullclean` | drop build artefacts; `fullclean` also drops `sdkconfig` |
| `erase` | erase the whole flash — **wipes NVS and Wi-Fi calibration** |

### Device

| Command | Does |
| --- | --- |
| `port` | list serial ports and say which one will be used |
| `wifi` | how to reach the live preview from a phone |
| `open` | open the preview in your browser |
| `status` | pretty-print `GET /api/status` |
| `watch` | poll the risk/PERCLOS line once a second |
| `snapshot [file]` | save one JPEG frame (default `snapshot.jpg`) |
| `alert [reason]` | play a warning: `drowsy`, `microsleep`, `yawn`, `nod` |
| `mute` / `unmute` | silence or restore the speaker |
| `mqtt [sub]` | broker state, a test publish, or the fleet topic — `status`, `test`, `topic`, `on`, `off` |

### Project

| Command | Does |
| --- | --- |
| `test` | the Python test suite |
| `diagrams` | regenerate the tutorial figures and the wiring poster |
| `doctor` | check toolchain, port and device in one pass |
| `docs-build` | build the documentation site only — see [Documentation pipeline](../operations/documentation.md) |
| `docs-preview` | serve the docs locally with hot reload |
| `docs-check` | validate the docs without publishing |
| `docs-deploy` | publish the docs to GitHub Pages |

### Environment

| Variable | Effect |
| --- | --- |
| `PLXY_PORT=COM9` | force a serial port instead of auto-detecting |
| `PLXY_HOST=10.0.0.5` | talk to the board over station mode instead of its own AP |

The docs commands read a few more; they are listed in
[Configuration](../configuration/index.md#environment-variables).

## Flashing

`flash` builds first, then handles download mode for you:

```bash
./plxy.sh flash
```

If it reports *Wrong boot mode detected (0x28)*, the chip is not in the ROM
loader and cannot be put there electrically on this bridge. Hold **BOOT**, tap
**RESET**, release **BOOT**, and let the retry through. `./plxy.sh port` tells
you which of the two USB interfaces the cable is currently in, which decides
whether you will see this at all.

!!! danger "`erase` wipes more than the app"
    `./plxy.sh erase` clears the entire flash, including the NVS partition and
    the factory Wi-Fi calibration data. Use `fullclean` when you only want a
    clean build.

## Checking the toolchain

```bash
./plxy.sh doctor
```

Prints, in order: the detected ESP-IDF version, the serial port table, the size
and timestamp of the built binary, and — if the board is reachable — the live
`fps`, `camera`, `models`, `eye_model`, `ssid` and `ip` fields from
[`/api/status`](../reference/device-api.md#get-apistatus).

## Running the tests

```bash
./plxy.sh test
```

This runs `pytest tests/` against the project `.venv`. It includes
`tests/test_firmware_parity.py`, which parses the `RiskFilter` constructor
signature in `firmware/esp32s3/main/risk_filter.h` and checks it against
`src/drowsyguard/risk.py` — so the defaults in that header are load-bearing
text, not just a default.
