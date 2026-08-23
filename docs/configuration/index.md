---
title: Configuration
---

# Configuration

DrowsyGuard has four places where behaviour is configured. Nothing else in the
tree holds a tunable number.

| Layer | Lives in | Applied |
| --- | --- | --- |
| [Training](#training-configuration) | `configs/*.yaml` | at `train` / `evaluate` / `export-onnx` |
| [Firmware pins](#firmware-pins) | `firmware/esp32s3/main/board_*.h` | at compile time |
| [Firmware behaviour](#firmware-behaviour) | `main.cpp`, `risk_filter.h` | at compile time |
| [Build](#build-configuration) | `sdkconfig.defaults` | at `idf.py set-target` |

Plus the [environment variables](#environment-variables) `plxy.sh` reads, and the
handful of [runtime settings](#runtime-settings) the device accepts over HTTP.

## Training configuration

`configs/train.yaml` is the default; `configs/train_ddd.yaml` is the DDD variant.

```yaml
seed: 42
image_size: 64
batch_size: 64
epochs: 20
learning_rate: 0.001
weight_decay: 0.0001
num_workers: 0
train_dir: data/processed/train
val_dir: data/processed/val
test_dir: data/processed/test
checkpoint: models/best.pt
onnx_output: models/drowsyguard.onnx
subject_split:
  train: 0.70
  val: 0.15
  test: 0.15
class_names: [alert, drowsy]
```

| Field | Notes |
| --- | --- |
| `image_size` | side of the square grayscale input to the whole-face model |
| `num_workers` | `0` is right for small sets; DDD uses `8` because PNG decoding dominates on CPU |
| `subject_split` | fractions of **subjects**, not of images — that is the whole point |
| `class_names` | order defines the label indices; `alert` is class 0 |

The DDD config differs only in `batch_size: 128`, `epochs: 15`, `num_workers: 8`
and the output paths (`models/ddd_best.pt`, `models/drowsyguard_ddd.onnx`).

## Firmware pins

Pin assignments live in exactly two headers, and nowhere else in the firmware.

### Camera — `main/board_camera.h`

| Signal | GPIO | | Signal | GPIO |
| --- | --- | --- | --- | --- |
| XCLK | 15 | | D7 (Y9) | 16 |
| SIOD (SCCB SDA) | 4 | | D6 (Y8) | 17 |
| SIOC (SCCB SCL) | 5 | | D5 (Y7) | 18 |
| VSYNC | 6 | | D4 (Y6) | 12 |
| HREF | 7 | | D3 (Y5) | 10 |
| PCLK | 13 | | D2 (Y4) | 8 |
| PWDN | not routed | | D1 (Y3) | 9 |
| RESET | not routed (reset via SCCB) | | D0 (Y2) | 11 |

Frame geometry and orientation live in the same header:

| Define | Default | Meaning |
| --- | --- | --- |
| `CAM_FRAME_W` / `CAM_FRAME_H` | `240` × `240` | capture size |
| `CAM_RGB565_BYTE_SWAP` | `1` | the sensor's byte order vs. the DL stack's |
| `CAM_ROTATE_180` | `1` | how the module is mounted |
| `CAM_SELFIE_MIRROR` | `1` | mirror the preview only |

### Audio — `main/board_audio.h`

| Amplifier pin | GPIO |
| --- | --- |
| BCLK (bit clock) | 41 |
| LRC (word select) | 42 |
| DIN (data, S3 → amp) | 2 |

| Define | Default |
| --- | --- |
| `AUDIO_SAMPLE_RATE_HZ` | `16000` |
| `AUDIO_TONE_AMPLITUDE` | `0.35` |

Wiring these is [step 6 of the hardware tutorial](../tutorials/hardware-setup/README.md#6-wiring).

## Firmware behaviour

### Risk and timing — `main/main.cpp`

```cpp
static constexpr float PERCLOS_WINDOW_S = 3.0f;
static constexpr float RISK_REQUIRED_S  = 0.55f;
static constexpr float RISK_COOLDOWN_S  = 4.0f;
static constexpr float RISK_TRIGGER     = 0.55f;
```

These are **durations in seconds**, deliberately. `RiskFilter` counts frames, and
frame counts silently couple the alarm's sensitivity to the frame rate: 8 frames
is half a second at 15 fps and a third of a second at 25, so making the capture
loop faster made the alarm twitchier without anyone editing a threshold.
`retune_for_fps()` converts these durations to frame counts once a second from
the measured rate, so the intended half second stays half a second.

`RISK_TRIGGER` is a level of the **fused behaviour score**, not a raw model
probability. Tune it in the [live dashboard](../guide/live-dashboard.md) and
paste it here.

### The filter itself — `main/risk_filter.h`

```cpp
RiskFilter(float trigger=0.72f, int required=8, int cooldown=60)
```

!!! warning "Load-bearing text"
    `tests/test_firmware_parity.py` **parses this constructor signature** and
    compares it with `src/drowsyguard/risk.py`. The defaults above are not just
    defaults — changing one without the other fails the test suite. Run
    `./plxy.sh test` after any edit.

`MODEL_SELFTEST` in `main.cpp` (default `0`) runs the detector over the fixed
frames in `test_frames.h` at boot, which separates "the ESP-DL binding is wrong"
from "nobody was in front of the camera". Leave it off: `/api/snapshot` returns
the actual frame the detector was handed, which is strictly better.

### Wi-Fi — `main/board_wifi.h` { #wi-fi }

| Define | Default | Meaning |
| --- | --- | --- |
| `WIFI_AP_SSID_PREFIX` | `"DrowsyGuard"` | the MAC suffix is appended |
| `WIFI_AP_PASSWORD` | `"drowsyguard"` | **change this before any road test** |
| `WIFI_AP_CHANNEL` | `6` | |
| `WIFI_AP_MAX_CLIENTS` | `4` | |
| `WIFI_STA_SSID` | `""` | set to join an existing network instead |
| `WIFI_STA_PASSWORD` | `""` | |

`./plxy.sh wifi` reads these straight out of the header, so it always prints what
the firmware will actually do. See [Security](../security.md#the-access-point-is-open-by-default).

## Build configuration

`firmware/esp32s3/sdkconfig.defaults` seeds `sdkconfig` at
`idf.py set-target esp32s3`. Delete `sdkconfig` — not this file — to start over,
or run `./plxy.sh fullclean`.

The settings that are not optional:

| Setting | Why |
| --- | --- |
| `CONFIG_SPIRAM=y`, `CONFIG_SPIRAM_MODE_OCT=y` | camera framebuffers (`CAMERA_FB_IN_PSRAM`) and the ESP-DL models both live in PSRAM. Get `MODE_OCT` wrong and the board boots but finds 0 bytes of PSRAM, which surfaces later as an unexplained `esp_camera_init()` failure |
| `CONFIG_ESPTOOLPY_FLASHSIZE_16MB` | the N16R8 module |
| `CONFIG_PARTITION_TABLE_CUSTOM` | `partitions.csv` |
| `CONFIG_OV5640_SUPPORT` + `CONFIG_OV3660_SUPPORT` | the board is sold with an OV3660 but ships an OV5640; both are pinned so a driver change cannot look like a dead camera |

The `ov3660: Mismatch PID=0x5640` line on the way to a working camera is
expected, not a fault.

## Environment variables

Read by `plxy.sh`:

| Variable | Default | Effect |
| --- | --- | --- |
| `PLXY_PORT` | auto-detected | force a serial port, e.g. `COM9` |
| `PLXY_HOST` | `192.168.4.1` | talk to the board over station mode |
| `PLXY_DOCS_PYTHON` | discovered | interpreter used for the docs commands |
| `PLXY_DOCS_VENV` | `.venv-docs` | where the docs toolchain is installed |
| `PLXY_DOCS_PORT` | `8001` | `docs-preview` port — not 8000, which the live dashboard uses |
| `PLXY_DOCS_NO_VENV` | unset | set to `1` to use the ambient interpreter and never create a venv (this is what CI does) |
| `PLXY_DOCS_SITE_URL` | the published Pages URL | base URL the site is built for; `docs-preview` sets it to the local address so the preview serves at `/` |

## Runtime settings

The only things settable on a running device, over HTTP:

| Setting | Range | Endpoint |
| --- | --- | --- |
| JPEG quality | 10–95, higher is better | `POST /api/settings?quality=N` |
| Stream rate | 1–20 fps | `POST /api/settings?fps=N` |
| Mute | `0` / `1` | `POST /api/settings?muted=N` |
| Alert language | `en` or `km` | `POST /api/settings?lang=xx` |

Risk thresholds are **not** runtime-settable — they are compiled in, by design:
a threshold that can drift at runtime cannot be reported in a thesis. Full
parameter detail in the [Device HTTP API](../reference/device-api.md#post-apisettings).
