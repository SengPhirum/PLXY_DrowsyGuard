# ESP32-S3 Firmware

ESP-IDF project for the deployment phase.

**Board: ESP32-S3-WROOM-1 N16R8 CAM + OV5640** (16 MB flash, 8 MB octal PSRAM), a
MAX98357A I2S amplifier and a 4 Ω speaker. **No display.** The listing says OV3660;
the unit here is an OV5640 and `esp32-camera` says so on the way past
(`ov3660: Mismatch PID=0x5640`) before binding the right driver — see `board_camera.h`. Its DVP pin map is
identical to the ESP32-S3-EYE map, so ESP-DL's vision examples apply unchanged. Full
wiring, toolchain and flashing instructions:
[`../../docs/HARDWARE_SETUP.md`](../../docs/HARDWARE_SETUP.md).

Target is **ESP32-S3**. ESP32-S2 will not work: it has no AI vector instructions and
ESP-DL's face detection models support only S3/P4. See `../../docs/FIRMWARE_PIPELINE.md`
for the frame budget.

## Seeing what it sees

The board is headless. It comes up as its own Wi-Fi access point and serves the live
preview, all the detection telemetry, and a couple of controls to any browser:

| | |
| --- | --- |
| **SSID** | `DrowsyGuard-XXXXXX` (last three bytes of the AP MAC) |
| **Password** | `drowsyguard` |
| **Address** | `http://192.168.4.1/` |

| Endpoint | Port | What it serves |
| --- | --- | --- |
| `/` | 80 | the page, linked into the binary from `main/web/index.html` |
| `/stream` | 81 | MJPEG live preview — **one viewer at a time** |
| `/api/snapshot` | 80 | one JPEG; the page's fallback for extra viewers |
| `/api/status` | 80 | risk, PERCLOS, face box, event rates, heap, uptime, as JSON |
| `/api/settings` | 80 | `?quality=`, `?fps=`, `?muted=` |
| `/api/alert-test` | 80 | `?reason=0..3` — plays one warning through the speaker |

Two HTTP servers, not one, because `esp_http_server` serves one request at a time per
instance and an MJPEG stream never ends. The stream therefore gets its own instance on
port 81 and the control API stays responsive on port 80.

To join an existing network as well as serving its own, set `WIFI_STA_SSID` in
`main/board_wifi.h`; the device then runs AP+STA and is reachable from a development
machine without leaving the lab Wi-Fi.

## Commands

Use [`../../plxy.sh`](../../plxy.sh) from the repo root - it finds the port, sets
up the IDF environment, and knows this board's download-mode quirk:

```bash
./plxy.sh dev            # build + flash + monitor
./plxy.sh build
./plxy.sh flash
./plxy.sh monitor
./plxy.sh doctor         # toolchain, port, and whether the device answers
```

Raw ESP-IDF still works, from an **ESP-IDF PowerShell** (not Git Bash - idf.py
refuses to run under MSYS):

```powershell
idf.py set-target esp32s3     # seeds sdkconfig from sdkconfig.defaults
idf.py reconfigure            # fetch managed components
idf.py build
idf.py -p COM9 flash monitor
```

> **Flashing needs the BOOT button on this board.** Its CH343 UART bridge drives
> EN but not GPIO0, so esptool cannot reach download mode and reports
> `Wrong boot mode detected (0x28)`. Hold BOOT, tap RESET, release BOOT - the
> chip then waits in the ROM loader indefinitely, so there is no window to hit -
> then run `./plxy.sh flash`, which detects the loader and flashes with the reset
> lines left alone.

`curl` is often faster than a browser for checking a change:

```powershell
curl http://192.168.4.1/api/status
curl -X POST "http://192.168.4.1/api/alert-test?reason=1"
curl -o frame.jpg http://192.168.4.1/api/snapshot
```

## Layout
| file | role |
| --- | --- |
| `behavior.h/.cpp` | eye/yawn/nod/sneeze logic + PERCLOS, mirrors `drowsyguard/behavior.py` |
| `risk_filter.h/.cpp` | sustained-risk trigger + cooldown |
| `board_camera.h` | verified DVP pin map (OV3660/OV5640) + sensor tuning |
| `board_audio.h/.cpp` | I2S bring-up for the MAX98357A, tone + PCM playback |
| `board_wifi.h/.cpp` | SoftAP (optionally AP+STA) bring-up |
| `web_server.h/.cpp` | the two HTTP servers, MJPEG encoding, telemetry, controls |
| `web/index.html` | the page itself; embedded as flash rodata |
| `voice_alert.h/.cpp` | reason-specific spoken alerts + buzzer fallback |
| `model_adapter.*` | ESP-DL binding, version-specific |
| `test_frames.h` | captured RGB565 frames for the optional `MODEL_SELFTEST` in `main.cpp` |

`behavior.h` constants MUST match the Python module; `tests/test_firmware_parity.py`
enforces that, because the thresholds are tuned on the desktop dashboard.

## Why there is no panel

Earlier revisions drove a 1.8" ST7735S and then a 2.8" ILI9341 SPI panel. It cost five
GPIOs, a 150 KB PSRAM framebuffer, a per-frame software blit and a managed component
per panel variant, and in exchange it showed 240×320 pixels of 8-pixel-tall text to one
person sitting directly in front of it.

The browser preview replaced all of that and is strictly better as a research
instrument: it shows the frame *and* the risk score, the trigger, the PERCLOS window,
the per-eye closure probability, blink/yawn/nod rates, head geometry, an event log and
frame timing, on a screen large enough to read, from the passenger seat, on any device,
with `curl`-able JSON behind it for scripted acceptance tests.

The preview is a diagnostic, never a dependency:

* `web_server_publish_frame()` copies one frame and returns — it does no encoding, and
  it returns immediately without copying at all when no browser is connected.
* JPEG encoding runs in the stream task, pinned to core 1, on a second buffer the
  capture loop is no longer writing to.
* The alert path does not touch the network. If Wi-Fi fails to come up, the firmware
  logs it and keeps detecting and warning.

## Build stages

The firmware is written to be brought up in stages rather than all at once, because a
camera fault and a model fault look identical from the outside.

1. **Preview-only.** `model_init()` returns false, the capture loop runs, and the page
   shows the live feed with `no face model`. This validates the ribbon, pin map, PSRAM,
   radio, byte order and power supply on their own.
2. **Behaviour path.** Compiled in and fed zeros; use it to measure frame rate and heap.
3. **Models.** The face detector is bound (`espressif/human_face_detect`); the eye
   model is not, because it needs an `.espdl` this repo cannot yet produce — see
   `model_adapter.cpp`. Alerting is gated on the eye model on purpose: with PERCLOS
   pinned at zero the fused score can only under-report, and for a drowsiness alarm
   silence is the dangerous way to be wrong.

Set `MODEL_SELFTEST` to 1 in `main.cpp` to run the detector over the captured frames in
`test_frames.h` at boot — the same bytes every time, which separates "the ESP-DL
binding is wrong" from "nobody was in front of the camera". For live debugging,
`http://192.168.4.1/api/snapshot` returns the actual frame the detector was handed,
which is usually the faster answer.

## First hardware bring-up, 2026-08-23

Flashed and run on the board with MAC `80:b5:4e:c5:e0:18`. What the boot log showed:

| | |
| --- | --- |
| PSRAM | `Found 8MB PSRAM device`, octal mode, 80 MHz |
| Sensor | `Detected OV5640 camera` (after the harmless OV3660 PID mismatch) |
| Camera | `240x240 RGB565, PSRAM free 7755392 B` |
| Audio | `I2S up: BCLK=39 LRCLK=38 DIN=40`, boot chime played |
| Wi-Fi | `SoftAP "DrowsyGuard-C5E019" up on channel 6, WPA2`, DHCP on 192.168.4.1 |
| Web | `preview at http://192.168.4.1/  (stream on port 81)` |
| Face model | `human_face_detect msrmnp_s8_v1 loaded` (internal 99015 → 83743 B) |
| **Frame rate** | **19.7 fps** with no viewer — above the 15 fps target |
| Heap | 83.4–83.7 KB internal, 7.34 MB PSRAM, flat across samples |

Still unverified: the preview under a real browser (frame rate with a viewer
attached, the single-stream fallback, sustained heap), the alert path end to end,
and everything downstream of the eye model, which is still unbound.

`fps 19.7` with `viewers 0` is the baseline for the acceptance test in
`../../docs/DEPLOYMENT.md` that compares frame rate with and without a viewer.
