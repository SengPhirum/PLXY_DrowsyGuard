# ESP32-S3 Firmware

ESP-IDF project for the deployment phase.

**Board: ESP32-S3-WROOM-1 N16R8 CAM + OV3660** (16 MB flash, 8 MB octal PSRAM), with a
2.8" 240x320 ILI9341 SPI panel. Its DVP pin map is identical to the ESP32-S3-EYE map,
so ESP-DL's vision examples apply unchanged. Full wiring, toolchain and flashing
instructions: [`../../docs/HARDWARE_SETUP.md`](../../docs/HARDWARE_SETUP.md).

Target is **ESP32-S3**. ESP32-S2 will not work: it has no AI vector instructions and
ESP-DL's face detection models support only S3/P4. See `../../docs/FIRMWARE_PIPELINE.md`
for the frame budget.

## Commands
```powershell
idf.py set-target esp32s3     # seeds sdkconfig from sdkconfig.defaults
idf.py reconfigure            # fetch managed components
idf.py build
idf.py -p COM5 flash monitor
```

## Layout
| file | role |
| --- | --- |
| `behavior.h/.cpp` | eye/yawn/nod/sneeze logic + PERCLOS, mirrors `drowsyguard/behavior.py` |
| `risk_filter.h/.cpp` | sustained-risk trigger + cooldown |
| `display_ui.h/.cpp` | driver-facing screen (RGB565, no LVGL), panel-agnostic |
| `board_camera.h` | verified OV3660 DVP pin map + sensor tuning |
| `board_display.h/.cpp` | ILI9341 SPI bring-up and blit |
| `voice_alert.h/.cpp` | reason-specific spoken alerts + buzzer fallback |
| `model_adapter.*` | ESP-DL binding, version-specific |

`behavior.h` constants MUST match the Python module; `tests/test_firmware_parity.py`
enforces that, because the thresholds are tuned on the desktop dashboard.

## Build stages

The firmware is written to be brought up in stages rather than all at once, because a
camera fault and a model fault look identical from the outside.

1. **Preview-only** (current). `model_init()` returns false, the capture loop runs, and
   the panel shows the live feed with `NO MODEL - PREVIEW`. This validates the ribbon,
   pin map, PSRAM, SPI panel, byte order and power supply on their own.
2. **Behaviour path.** Already compiled in and fed zeros; use it to measure frame rate
   and heap.
3. **Models.** Uncomment the ESP-DL dependencies in `main/idf_component.yml` and
   implement `model_detect_face()` / `model_eye_closed_prob()` in `model_adapter.cpp`.
   See stage 3 of `docs/HARDWARE_SETUP.md`.

Nothing here has been compiled or flashed - no board existed in the environment this
was written in. The behaviour logic is a tested port; treat the ESP-DL call sites, the
LCD driver and the I2S path as unverified.
