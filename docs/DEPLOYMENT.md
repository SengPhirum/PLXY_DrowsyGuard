# ESP32-S3 Deployment Notes

Espressif currently recommends ESP32-S3/ESP32-P4-class boards for ESP-DL and supports camera sensors through `esp32-camera`. ESP-DL deploys quantized `.espdl` models produced with ESP-PPQ.

## Version policy
Intended versions, chosen 2026-08-11 when the hardware was bought. Replace each with
the version actually resolved by `idf.py reconfigure` (see
`firmware/esp32s3/dependencies.lock`) once the first build succeeds - the resolved
numbers are what the thesis reports.

- ESP-IDF: v5.4.x intended (>=5.3 required by esp-dl 3.x) — **confirm after first build**
- esp32-camera: ^2.1.7 — **confirm**
- esp_lcd_st7735 (waveshare): ^1.0.1 — **confirm**
- ESP-DL: ^3.1.2 — **confirm at stage 3**
- ESP-PPQ: TBD (pin before running `scripts/quantize_espdl.py`)
- Board: ESP32-S3-WROOM-1 **N16R8** CAM dev board (16 MB flash, 8 MB octal PSRAM)
- Camera sensor: **OV3660**, DVP, pin map identical to ESP32-S3-EYE
- Display: 1.8" **128x160 ST7735S** SPI panel

Do not leave these floating for thesis experiments. Setup and flashing procedure:
`docs/HARDWARE_SETUP.md`.

## Firmware pipeline
Camera (240x240 RGB565) -> ESP-DL face detect + 5 landmarks, every 3rd frame with the
box held in between -> two 32x32 eye crops per frame -> eye-closed probability ->
PERCLOS + long blinks + yawn + nod fused into a risk score, sneeze-suppressed ->
temporal risk filter -> reason-specific voice alert (buzzer fallback) + on-panel banner.

This replaced the earlier whole-face 64x64 grayscale classifier, which learned driver
identity rather than eyelid state; see `PROJECT_STATE.md`.

## Hardware acceptance tests
- Camera initializes 100 consecutive boots.
- No heap exhaustion after 1 hour.
- Inference latency recorded over >=1000 frames.
- Peak RAM and flash usage recorded.
- Alert output verified physically.
- Quantized predictions compared with Python on a shared test-image set.
