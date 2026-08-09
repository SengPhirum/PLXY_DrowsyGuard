# ESP32-S3 Deployment Notes

Espressif currently recommends ESP32-S3/ESP32-P4-class boards for ESP-DL and supports camera sensors through `esp32-camera`. ESP-DL deploys quantized `.espdl` models produced with ESP-PPQ.

## Version policy
Before the first real hardware build, record exact versions here:
- ESP-IDF: TBD (must satisfy selected ESP-DL release)
- ESP-DL: TBD
- ESP-PPQ: TBD
- Board: TBD
- Camera sensor: TBD

Do not leave these floating for thesis experiments.

## Firmware pipeline
Camera -> grayscale frame -> center crop -> 64x64 resize -> INT8 normalization -> ESP-DL model -> drowsy probability -> temporal risk filter -> buzzer.

## Hardware acceptance tests
- Camera initializes 100 consecutive boots.
- No heap exhaustion after 1 hour.
- Inference latency recorded over >=1000 frames.
- Peak RAM and flash usage recorded.
- Alert output verified physically.
- Quantized predictions compared with Python on a shared test-image set.
