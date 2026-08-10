# ESP32-S3 Firmware

This directory is an ESP-IDF scaffold for the deployment phase.

Because ESP32-S3 camera pin mappings vary by board and ESP-DL's model wrapper is version-specific, the code isolates both behind explicit TODO adapters instead of pretending one untested pin map/API works everywhere.

## Intended commands
```bash
idf.py set-target esp32s3
idf.py menuconfig
idf.py build
idf.py flash monitor -p /dev/ttyUSB0
```

Target is **ESP32-S3**. ESP32-S2 will not work: it has no AI vector instructions and
ESP-DL's face detection models support only S3/P4. See `../../docs/FIRMWARE_PIPELINE.md`
for the frame budget and board recommendation (ESP32-S3-EYE).

## Layout
| file | role |
| --- | --- |
| `behavior.h/.cpp` | eye/yawn/nod/sneeze logic + PERCLOS, mirrors `drowsyguard/behavior.py` |
| `risk_filter.h/.cpp` | sustained-risk trigger + cooldown |
| `display_ui.h/.cpp` | driver-facing screen (RGB565, no LVGL) |
| `voice_alert.h/.cpp` | reason-specific spoken alerts + buzzer fallback |
| `model_adapter.*` | ESP-DL binding, version-specific |

`behavior.h` constants MUST match the Python module; `tests/test_firmware_parity.py`
enforces that, because the thresholds are tuned on the desktop dashboard.

## Before first build
1. Select exact board and camera sensor (recommended: ESP32-S3-EYE).
2. Add its verified pin map in `main/board_camera.h`.
3. Pin ESP-DL dependency in `main/idf_component.yml`.
4. Implement `model_adapter.cpp` against that pinned ESP-DL release, and un-comment the
   capture loop in `main.cpp`.
5. Place/export the quantized `.espdl` eye model as configured.
6. Implement `lcd_blit()` in `main.cpp` for the board's LCD controller.
