---
title: Troubleshooting
---

# Troubleshooting

Start here, then follow the link. The exhaustive symptom table for the hardware
build lives in [Toolchain and firmware install §9](HARDWARE_SETUP.md#9-troubleshooting),
and the tutorial has its own at
[Hardware setup §13](tutorials/hardware-setup/README.md#13-troubleshooting).

```bash
./plxy.sh doctor      # toolchain, serial port, built binary, and the live device
```

`doctor` answers most "which half is broken" questions in one pass.

## Toolchain

### `idf.py` is not found, or works in one terminal but not another

`export.ps1` is per-session, not permanent. Re-run it in each new terminal — and
do not add it to your PowerShell profile. Better: use `./plxy.sh`, which sets up
the environment itself.

### "ESP-IDF Python virtual environment not found"

`export.ps1` derives the virtualenv name from whichever `python` is first on
`PATH`. This project ships a `.venv` on Python 3.13, so export computes
`idf5.5_py3.13_env` — an environment that was never installed — and reports a
missing virtualenv rather than a shadowing problem.

`./plxy.sh` works around this by scrubbing the project tree off `PATH` and asking
`idf_tools.py` for the real environment. Use it instead of calling `idf.py`
directly.

### "MSys/Mingw is not supported"

ESP-IDF refuses to run under Git Bash, and the guard is correct: MSYS rewrites
path-shaped arguments as they cross into native processes. `./plxy.sh` keeps the
bash front end and hands the build to PowerShell.

## Flashing and boot

### `Failed to connect ... Wrong boot mode detected (0x28)`

This board's UART bridge drives EN but not GPIO0, so no reset sequence can put
the chip into download mode on its own. `./plxy.sh flash` drives the lines
directly via `scripts/board_reset.py`. If that still fails: hold **BOOT**, tap
**RESET**, release **BOOT**, and reflash.

### The monitor shows nothing but `waiting for download`

You are on the **native USB-Serial/JTAG** interface, which re-enters the ROM
loader after every reset on this board — `rst:0x15 (USB_UART_CHIP_RESET)`,
`boot:0x22 (DOWNLOAD(USB/UART0))`.

Two traps compound it:

- pyserial raises DTR and RTS on `open()` under Windows. DTR drives GPIO0 and RTS
  drives EN, so simply opening the port reboots the chip into the loader. The
  port must be opened with `dtr=False`/`rts=False` set **before** `open()` —
  which `idf.py monitor` does not do.
- A raw `setRTS()` is not enough on `usbser.sys`: the set-control-line-state
  request only goes out when DTR is written too, so hand-rolled reset pulses
  silently do nothing.

`./plxy.sh port` says which interface the cable is in. Prefer the CH343 bridge.

### `ov3660: Mismatch PID=0x5640` in the boot log

**Expected, not a fault.** The board is sold with an OV3660 but ships an OV5640;
that line is the OV3660 driver declining the part, immediately followed by
`camera: Detected OV5640 camera`. Both drivers are pinned in
`sdkconfig.defaults` so a component default change cannot turn this into what
looks like a dead sensor.

### `esp_camera_init failed`

| Code | Cause |
| --- | --- |
| `0x105` `ESP_ERR_NOT_FOUND` | ribbon half-seated, PSRAM off, or the wrong pin map |
| `0x101` `ESP_ERR_NO_MEM` | PSRAM not enabled — needs `CONFIG_SPIRAM=y` **and** `CONFIG_SPIRAM_MODE_OCT=y` |

After a `fullclean`, also check the three `CONFIG_OV*_SUPPORT` entries: a
sensor-support regression reads exactly like a bad ribbon.

## The device

### No `DrowsyGuard-` SSID anywhere

The radio never started. Look for `wifi: SoftAP ... up` in the log — if PSRAM
failed first, fix that, because the Wi-Fi stack allocates from it.

### The SSID is visible but the phone refuses to join

`WIFI_AP_PASSWORD` is shorter than 8 characters. WPA2 requires 8+.

### Joined, but the page times out

The address is `http://192.168.4.1/` — not `https`, and not a `.local` name.

### The preview is stuck on "opening live stream"

Another viewer holds the single stream slot. Close the other tab, or switch the
page to **Use still photos**.

### The board is silent

Work down in this order:

1. `./plxy.sh alert drowsy` — read the JSON it returns. `"played": false` means
   the firmware never started playback; `"source"` tells you whether it used an
   SD-card clip or the embedded English fallback.
2. `./plxy.sh status` and check `alert.muted`.
3. If the log says `output=buzzer`, the I²S channel failed to initialise — check
   `AUDIO_PIN_*` against the reserved pins.
4. If the log says `output=I2S/MAX98357A`, it is wiring: check GND and VIN, then
   measure the amplifier's `SD` pin — below 0.16 V is shutdown.
5. A loud hiss instead of a tone means there is no common ground between the
   board and the amplifier. Very quiet output usually means `VIN` is on 3V3
   rather than 5V.

Full detail: [Voice alert hardware](VOICE_ALERT_HARDWARE.md).

### `fps` is far below 15

Check `CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ_240`, and lower the stream rate on the
page — re-encoding frames the camera has not replaced only burns CPU.

### The alarm is twitchier (or lazier) than it was

Frame rate changed. `required` and `cooldown` are counted in frames but were
chosen as durations, so `main.cpp` re-derives them from the measured rate once a
second. If you bypassed `retune_for_fps()`, that coupling is back. See
[Configuration](configuration/index.md#risk-and-timing-mainmaincpp).

## The desktop toolkit

### The webcam runs at 1 fps

On Windows, the installed `drowsyguard` console-script launcher can throttle
capture. Use the module form instead:

```bash
python -m drowsyguard.cli live
drowsyguard camera-test --index 0 --frames 20   # benchmark the backends
```

The dashboard also warns when capture is abnormally slow.

### `prepare` refuses to run

Splitting into a non-empty output would leave the previous split's files in place
and put one subject in two splits. Pass `--overwrite` if that is what you mean.

### Accuracy looks impossibly good

You probably split over DDD's flat class folders. That puts the same face — and
adjacent frames of one video — in both train and test. Use `import-ddd`, then
`prepare`. See [Datasets](guide/datasets.md#driver-drowsiness-dataset-ddd).

### `esp_ppq: OPTIONAL/MISSING`

Expected until you need `.espdl` quantization. Nothing else requires it.

### `./plxy.sh test` fails in `test_firmware_parity.py`

The `RiskFilter` defaults in `firmware/esp32s3/main/risk_filter.h` and
`src/drowsyguard/risk.py` disagree. That test parses the C++ constructor
signature on purpose — change both, or neither.

## The documentation build

### `docs-check` fails on a link

The strict build treats an unresolvable link, a missing anchor and a missing
image as errors. The message names the source file and the target. Links to
files **outside** `docs/` — source files, `PROJECT_STATE.md` — must be absolute
GitHub URLs, because they do not exist in the published site.

### `docs-preview` will not start

Port 8001 is already in use. Pass another: `./plxy.sh docs-preview 8010`.

### The docs toolchain will not install

The docs commands build their own environment in `.venv-docs` from
`requirements-docs.txt`, which is deliberately independent of the project
`.venv`. Delete `.venv-docs` and re-run, or point `PLXY_DOCS_PYTHON` at a working
interpreter. Full detail: [Documentation pipeline](operations/documentation.md).
