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

## Flashing from the browser

### The board does not appear in the browser's port picker

In order of how often it is the cause:

1. **A charge-only USB cable.** No data pins, so nothing enumerates anywhere — check
   whether the board shows up in Device Manager or `ls /dev/tty*` at all before
   blaming the page.
2. **The wrong USB socket.** These boards have two. Use the one wired to the
   ESP32-S3's native USB, not the UART bridge, if the picker stays empty.
3. **Not in download mode.** Hold **BOOT**, tap **RESET**, release **BOOT**, then click
   Install again. On this board a normal reset can leave the chip in the ROM loader
   anyway, which is harmless here — the loader is what the installer wants.
4. **Linux permissions.** `sudo usermod -aG dialout $USER`, then log out and back in.

### The Install button never appears

The page fetched no manifest. Two possibilities, and the status line above the button
says which:

- *No published build* — the release workflow has not produced one yet. Use
  [flash it yourself](getting-started/install-esp32.md#flash-it-yourself-instead).
- *Web Serial unsupported* — you are on Firefox or Safari. There is no extension or
  flag that adds it; use Chrome or Edge, or flash manually.

### The install fails part way through

Try a shorter or better-quality cable first, then a different USB port, and prefer a
port on the machine over one on a hub. The merged image is 3.4 MB and a marginal
cable that survives enumeration can still drop a bulk transfer.

If it consistently fails at the same point, the board's flash may be smaller than the
16 MB the partition table assumes. `python -m esptool --chip esp32s3 flash_id` prints
the real size.

### It flashed, but the board does nothing

Expect a three-note rising chime within a couple of seconds of the reboot. If there
is no chime and no `DrowsyGuard-` SSID, connect a serial monitor and read the boot
log — `./plxy.sh monitor`, or any terminal at 115200 baud. A board that flashed
successfully and boots into nothing usually means the image landed at the wrong
offset, which the browser installer cannot do (it writes one merged image at 0) but a
hand-typed `esptool` command can.

## The device

### The page says "no driver" while somebody is sitting there

The gate refused every candidate. The **driver** pill and the `face.reject` field name
which check failed, and each one has a different fix:

| Reject | What it means | What to do |
| --- | --- | --- |
| `score-too-low` | The detector's own confidence is under 0.55. | Almost always light. Check the `light` pill: below about 40 the frame is too dark for the detector, whatever the geometry looks like. |
| `face-too-small` | The face box is under 10% of the frame's short side. | The driver is too far from the camera for the eye crop to contain an eye. Move the board closer or reduce the field of view. |
| `face-too-large` | Over 95%. | Something is up against the lens. |
| `box-not-head-shaped` | The box aspect ratio is outside 0.55–1.80. | Usually a hand or a forearm. If it happens to a real face, it is at the frame edge and half cropped. |
| `nose-outside-eye-pair`, `mouth-too-narrow`, `mouth-too-wide` | The five landmarks do not describe a face. | Expected on hands, headrests and phones. On a real face it means the landmarks are badly fitted — check the overlay on the preview. |
| `roll-too-steep` | Head tilt past 45°. | A genuinely extreme pose, or the mirror bug returning; see `behavior_orient_landmarks()`. |
| `moved-too-far` | Plausible, but not where the tracked face was. | Correct behaviour when a passenger leans in. If it fires on the driver, the frame rate has collapsed far enough that a head moves more than a box width between detections. |

If every candidate reads `ok` and the driver still is not present, the track has not
confirmed yet — presence needs two consecutive agreeing detections, which is about
0.4 s at 15 fps.

### "No driver detected" fires while someone is driving

The driver is present but not *confirmed* for at least three seconds at a stretch.
Check the **driver** pill: if it flickers between `driver present` and `empty Ns`, the
detector is dropping the face repeatedly rather than the gate rejecting it. Raise the
threshold, or turn the alert off, from the dashboard's `/config` endpoint
(`no_driver_after`, `no_driver_alert`) — see
[Configuration](configuration/index.md#the-no-driver-alert).

### "No driver detected" never fires on an empty seat

Three things suppress it deliberately, and the status page says which:

- `presence.health` is not `ok` — the camera or the models are down, and a fault is
  never reported as an absence.
- `presence.state` is `warmup` — the first five healthy seconds after boot or after a
  fault clears are not trusted.
- Something in the frame is being confirmed as a driver. Check `face.reject`; a
  headrest that passes the gate is a real bug and worth reporting with a snapshot.

### The sneeze filter fires on a yawn (or never fires)

A sneeze is distinguished from a yawn by *when* the mouth opened relative to the eyes
closing, not by how wide it opened. If a yawn is being reclassified, the mouth is
opening within `SNEEZE_MOUTH_LEAD_S` (0.5 s) of the closure, which means the eye model
is late — check `eyes.closed` against the preview. If real sneezes are missed, the
opening index is not reaching `SNEEZE_JAW_DELTA`; watch **opening index** on the page
during one and compare.

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

### `./plxy.sh test` skips the firmware tests

`no host C++ compiler`. Several tests compile `firmware/esp32s3/main/*.cpp` on the
host and drive it against the Python implementations; without a compiler they skip,
which for a parity check is the same as not having one. `pip install -e ".[dev]"`
installs `ziglang`, a C++ compiler shipped as a wheel, which is enough.

`esptool is not installed` skips the web-installer merge tests for the same reason and
the same fix.

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
