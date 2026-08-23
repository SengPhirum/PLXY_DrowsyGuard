# ESP32-S3 Deployment Notes

Espressif currently recommends ESP32-S3/ESP32-P4-class boards for ESP-DL and supports camera sensors through `esp32-camera`. ESP-DL deploys quantized `.espdl` models produced with ESP-PPQ.

## Version policy
Intended versions, chosen 2026-08-11 when the hardware was bought. Replace each with
the version actually resolved by `idf.py reconfigure` (see
`firmware/esp32s3/dependencies.lock`) once the first build succeeds - the resolved
numbers are what the thesis reports.

- ESP-IDF: v5.4.x intended (>=5.3 required by esp-dl 3.x) — **confirm after first build**
- esp32-camera: ^2.1.7 — **confirm** (also supplies the JPEG encoder the web
  preview uses; no separate dependency)
- ESP-DL: ^3.1.2 — **confirm at stage 3**
- ESP-PPQ: TBD (pin before running `scripts/quantize_espdl.py`)
- Board: ESP32-S3-WROOM-1 **N16R8** CAM dev board (16 MB flash, 8 MB octal PSRAM)
- Camera sensor: **OV3660**, DVP, pin map identical to ESP32-S3-EYE
- Display: **none.** The preview is served over the board's own Wi-Fi access
  point (SoftAP, `esp_http_server`, MJPEG on port 81). Both ship with ESP-IDF,
  so there is no version to pin here — record the ESP-IDF version instead.

Do not leave these floating for thesis experiments. Setup and flashing procedure:
`docs/HARDWARE_SETUP.md`.

## Firmware pipeline
Camera (240x240 RGB565) -> ESP-DL face detect + 5 landmarks, every 3rd frame with the
box held in between -> two 32x32 eye crops per frame -> eye-closed probability ->
PERCLOS + long blinks + yawn + nod fused into a risk score, sneeze-suppressed ->
temporal risk filter -> reason-specific voice alert (buzzer fallback), with the
frame and every intermediate number served to a browser over Wi-Fi.

This replaced the earlier whole-face 64x64 grayscale classifier, which learned driver
identity rather than eyelid state; see `PROJECT_STATE.md`.

## Hardware acceptance tests
- Camera initializes 100 consecutive boots.
- No heap exhaustion after 1 hour.
- Inference latency recorded over >=1000 frames.
- Peak RAM and flash usage recorded.
- Alert output verified physically (`POST /api/alert-test` makes this a one-liner
  rather than a firmware edit).
- Quantized predictions compared with Python on a shared test-image set.
- **Frame rate recorded twice: with a browser watching the preview and with
  none.** The delta is the JPEG encode, and it is the evidence that the
  preview costs the detector nothing it needs. `GET /api/status` reports `fps`
  and `stream.viewers`, so this test scripts cleanly.
- Preview opened and closed 20 times with heap sampled after each, to rule out
  a leak in the stream path.
