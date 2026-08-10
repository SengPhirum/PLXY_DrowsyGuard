# On-device pipeline, compute budget and board choice

## Do not buy an ESP32-S2 for this project

The S2 cannot run this pipeline:

| | ESP32-S2 | ESP32-S3 |
| --- | --- | --- |
| cores | 1 | 2 |
| AI vector instructions | **none** | 128-bit SIMD for 8/16/32-bit ops |
| ESP-DL face detection support | **not supported** | supported |
| PSRAM | limited/none on many modules | up to 8 MB octal |

ESP-DL's `human_face_detect` models list ESP32-S3 and ESP32-P4 as their supported
targets; the S2 is absent. The S3's vector instructions are the whole reason its
inference numbers below are achievable — they are what ESP-NN/ESP-DL accelerate. On an
S2 the face detector would be several times slower with no optimized kernels available,
so face detection plus two eye inferences per frame is not viable.

**Recommended board: ESP32-S3-EYE.** It already carries everything this project needs
— OV2640 2 MP camera, 1.3" 240x240 LCD, digital microphone, 8 MB PSRAM, 8 MB flash —
and it is the reference board for ESP-WHO vision work. It is also what
`PROJECT_STATE.md` already recommended. Audio output for spoken alerts still needs the
MAX98357A-class I2S amplifier in the roadmap; the on-board mic is an input.

## Frame budget on ESP32-S3

Latencies for the detector are Espressif's published figures for ESP-DL
`human_face_detect`; the eye model figure is an estimate scaled from `mnp_s8_v1`
(48x48 in 5.8 ms) down to 32x32 and 11.3k parameters, and is the main number to
re-measure on real hardware.

| stage | model | input | cost | cadence |
| --- | --- | --- | --- | --- |
| face detect stage 1 | `msr_s8_v1` | 120x160x3 | 33.1 ms | every 3rd frame |
| face detect stage 2 | `mnp_s8_v1` | 48x48x3 | 5.8 ms | every 3rd frame |
| eye state x2 | `open_closed_eye` | 32x32x3 | ~4-8 ms (estimate) | every frame |
| behaviour + PERCLOS | — | — | <1 ms | every frame |
| LCD compose + blit | — | 240x240 RGB565 | ~2-4 ms | every frame |

Amortised: `(33.1 + 5.8) / 3 ≈ 13 ms` detector + ~6 ms eyes + ~4 ms UI ≈ **23 ms/frame,
so 15-20 fps** with headroom on a 240 MHz dual-core part.

Two decisions make that budget work:

1. **Detect every 3rd frame, track in between.** Face position changes slowly compared
   to eyelids. `DETECT_EVERY = 3` in `main.cpp`, with the last box held when detection
   drops — deliberately, because detectors tend to lose the face exactly when the eyes
   close, which is the moment of interest.
2. **15 fps is enough, by design.** PERCLOS needs temporal *coverage*, not a high frame
   rate: at 15 fps a 1 s microsleep is still 15 samples. Blink counting degrades at low
   frame rates, which is why blinks carry only 0.20 of the fused score and PERCLOS 0.55.

Memory: the eye model is 46 KB and the detectors are a few hundred KB, all comfortable
in 8 MB PSRAM. The 240x240 RGB565 framebuffer is 115 KB and is statically allocated in
`display_ui`.

## Landmark order differs between desktop and device

This will silently corrupt every geometric cue if it is copied naively.

| | order |
| --- | --- |
| YuNet (desktop) | right eye, left eye, nose, right mouth, left mouth |
| ESP-DL `human_face_detect` | left eye, left mouth, nose, right eye, right mouth |

`behavior_from_espdl_keypoints()` reorders into the canonical (YuNet) order. Never index
ESP-DL keypoints directly. `tests/test_firmware_parity.py` asserts the mapping table.

## Keeping device and desktop in step

The thresholds are tuned on the desktop dashboard, so they must be numerically identical
on the device or the tuning is meaningless. `firmware/esp32s3/main/behavior.h` mirrors
`src/drowsyguard/behavior.py`, and `tests/test_firmware_parity.py` parses the header and
fails if any constant, the fusion weights, or the `RiskFilter` defaults diverge.

## Driver-facing screen

`display_ui.cpp` renders, in RGB565 with no LVGL dependency:

- live camera preview with the tracked face box (green open, red closed, amber held),
- `EYES OPEN` / `EYES CLOSED` and a PERCLOS bar,
- a risk bar with a tick marking the alert trigger, so the driver can see how close
  they are to a warning rather than being surprised by one,
- yawn/nod counts, `MOUTH`, `HEAD DOWN`, `CALIBRATING`, and fps,
- the last named event, including `SNEEZE IGNORED` so a suppressed false alarm is
  visible rather than mysterious,
- a full-width banner naming the reason when an alert fires.

Panel initialization is intentionally left as a TODO with a blit callback, for the same
reason as `board_camera.h`: the LCD controller and pins depend on the board, and a
guessed pin map that appears to work is worse than an explicit gap.

## Spoken alerts

`voice_alert.h` defines `AlertReason` — `Drowsy`, `Microsleep`, `Yawning`, `HeadNod` —
each mapping to its own recorded clip and screen banner (`WAKE UP`, `TAKE A BREAK`,
`STAY ALERT`, `DROWSY`). A named cause is far more actionable than a generic chime.
Clips are prerecorded PCM in flash rather than on-MCU synthesis, for predictable latency
and multilingual output; see `assets/audio/README.md`. I2S streaming is still a TODO
pending the amplifier and pin map.

## Status

None of this firmware has been compiled or flashed — no ESP32-S3 board exists in this
environment. The behaviour logic is a direct port of Python that is unit-tested, and the
constants are guarded by a parity test, but treat the ESP-DL call sites, the LCD blit and
the I2S path as unverified scaffolding.
