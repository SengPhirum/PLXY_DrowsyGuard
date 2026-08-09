# Voice Alert Hardware

## Recommended prototype architecture

`ESP32-S3 N16R8 -> I2S -> MAX98357A-class amplifier -> 4 ohm / 3 W speaker`

Keep a small active/passive buzzer as a fallback warning channel.

## Why prerecorded audio

- predictable flash/RAM/CPU requirements
- no text-to-speech model on the MCU
- deterministic warning wording
- straightforward English/Khmer selection
- measurable alert latency

## Suggested parts

- ESP32-S3 module/board with 16 MB flash and 8 MB PSRAM
- OV2640 camera for the initial prototype
- MAX98357A-compatible I2S mono amplifier breakout
- 4 ohm, approximately 3 W speaker
- optional buzzer
- automotive-appropriate regulated power stage for the later in-car prototype

## Connections

Final GPIO numbers MUST be assigned after the exact camera board revision is known because camera modules consume many ESP32-S3 pins.

Typical logical connections are:

| ESP32-S3 | Amplifier |
|---|---|
| 5 V / appropriate supply | VIN |
| GND | GND |
| chosen I2S BCLK GPIO | BCLK |
| chosen I2S LRCLK/WS GPIO | LRC/WS |
| chosen I2S DATA GPIO | DIN |

Do not copy arbitrary GPIO numbers from another ESP32-S3-CAM board.

## Alert state machine

1. Camera/model produces drowsiness probability.
2. Temporal risk filter rejects transient events.
3. Sustained drowsiness crosses the alert threshold.
4. Firmware begins the selected prerecorded warning.
5. A cooldown suppresses immediate repeated messages.
6. If risk persists after cooldown, warning may repeat up to the configured limit.
7. Buzzer remains available if audio initialization/playback fails.

## Thesis measurements

Measure at minimum:

- detection-to-audio-start latency (ms)
- inference latency (ms/frame)
- false alerts/hour
- missed drowsiness events
- alert repeat count
- audio subsystem memory footprint
- total device current/power during idle, inference and audio playback

## Safety

Voice volume must be clearly audible but not startling. Testing involving drowsiness must use an approved controlled protocol, simulator, or parked vehicle rather than deliberately drowsy public-road driving.
