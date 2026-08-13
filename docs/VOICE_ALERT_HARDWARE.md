# Voice Alert Hardware

## Prototype architecture

`ESP32-S3 N16R8 -> I2S -> MAX98357A amplifier -> 4 ohm / 3 W speaker`

All of it is now bought and wired; a small active buzzer on GPIO 2 remains the
automatic fallback when I2S fails to initialize.

## Why prerecorded audio

- predictable flash/RAM/CPU requirements
- no text-to-speech model on the MCU
- deterministic warning wording
- straightforward English/Khmer selection
- measurable alert latency

## Parts in hand

- ESP32-S3-WROOM-1 **N16R8** board, 16 MB flash / 8 MB octal PSRAM (item 2991)
- **OV3660** camera on the board's FPC connector (shipped with item 2991)
- **MAX98357A** I2S filterless class-D mono amplifier (item 2724)
- **4 ohm, 3 W** 40x22 mm speaker (item 2554)
- buzzer on GPIO 2 as the fallback channel (not yet purchased)
- automotive-appropriate regulated power stage for the later in-car prototype

## Connections

GPIO numbers are now fixed. The DVP camera consumes fourteen pins and the octal
PSRAM reserves 33-37, so these three are what remained after the display took
14/21/41/42/47. They are the microSD slot's pins, which this project does not use.

| ESP32-S3 | Amplifier | Defined in |
|---|---|---|
| `5V` | VIN | - |
| `GND` | GND | - |
| `GPIO 39` | BCLK | `AUDIO_PIN_BCLK` |
| `GPIO 38` | LRC / WS | `AUDIO_PIN_LRCLK` |
| `GPIO 40` | DIN | `AUDIO_PIN_DIN` |
| - | GAIN: leave floating (9 dB) | - |
| - | SD: leave alone | - |

Source of truth: `firmware/esp32s3/main/board_audio.h`. Do not copy arbitrary GPIO
numbers from another ESP32-S3-CAM board. Full wiring walkthrough with diagrams:
[docs/tutorials/hardware-setup](./tutorials/hardware-setup/README.md).

### Electrical notes

- VIN accepts 2.5-5.5 V. Use 5 V: the 3.2 W-into-4-ohm figure is a 5 V number.
- BCLK/LRC/DIN accept 3.3 V logic directly. No level shifter.
- No MCLK line - the MAX98357A recovers its own clock.
- Filterless class D: the speaker connects straight to the screw terminal, and
  the output is a bridged pair, so neither speaker lead may be grounded.
- `SD` selects channel as well as shutdown. The firmware writes the same sample
  to both I2S slots so the setting cannot matter, except for full shutdown
  (`SD` below 0.16 V).

## Current implementation status

`main/board_audio.cpp` brings up I2S and can play 16-bit PCM at 16 kHz or generate
tones. `main/voice_alert.cpp` runs playback on a dedicated FreeRTOS task, because
the capture loop's frame budget is ~23 ms and inline playback would freeze the
preview during the alert.

Recorded speech is not embedded yet. Until approved clips exist, each reason plays
a distinct tone pattern:

| Reason | Pattern |
|---|---|
| Microsleep | 3 x 150 ms at 1200 Hz - highest and fastest, most urgent |
| Drowsy | 2 x 220 ms at 880 Hz |
| HeadNod | 2 x 200 ms at 780 Hz |
| Yawning | 1 x 260 ms at 660 Hz |

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
