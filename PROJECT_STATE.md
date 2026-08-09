# Project State

Last updated: 2026-08-09
Status: Initial research/engineering scaffold complete; hardware validation pending.

## Locked design decisions
- Target platform: ESP32-S3 with PSRAM and camera.
- Input: fixed driver-facing grayscale crop, 64x64.
- Model: very small CNN, two classes (`alert`, `drowsy`).
- Runtime decision logic: temporal risk accumulation rather than single-frame alarm.
- Evaluation: subject-independent splits only.
- Product intent: low-cost retrofit aid for older cars without built-in driver monitoring.

## Known gaps
1. No physical ESP32-S3 board has been flashed in this environment.
2. ESP-PPQ export API must be pinned to a specific version before model conversion code can be finalized.
3. Camera pin configuration depends on the exact development board selected.
4. Night performance likely requires an IR-capable sensor/illumination design.
5. Real-road drowsiness data collection requires careful ethics/safety planning.

## Next best action
Select one exact board (recommended: ESP32-S3-EYE-compatible board with PSRAM, or another supported ESP32-S3 camera board), then pin ESP-IDF/ESP-DL versions and complete hardware-in-the-loop validation.
