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
— OV2640 2 MP camera, digital microphone, 8 MB PSRAM, 8 MB flash — and it is the
reference board for ESP-WHO vision work. It is also what `PROJECT_STATE.md`
already recommended. Audio output for spoken alerts still needs the
MAX98357A-class I2S amplifier in the roadmap; the on-board mic is an input. Its
on-board LCD is now surplus rather than a selling point: the shipped build is
headless and serves its preview over Wi-Fi.

## Frame budget on ESP32-S3

Latencies for the detector are Espressif's published figures for ESP-DL
`human_face_detect`. **The eye-model figure is now measured, not estimated, and it
came out far worse than the estimate**: the old guess was 4-8 ms for both eyes,
scaled from `mnp_s8_v1`. On hardware it is roughly 45 ms for the pair.

The estimate assumed ESP-DL's int8 kernels, which use the S3's vector
instructions. This model does not go through ESP-DL - it cannot, see the note in
`eye_model.h` - so it runs as plain scalar float32, at about 7.7 cycles per
multiply-accumulate against roughly 693k MACs per eye. That is the price of
skipping quantization, and the observable effect is direct:

| what the detector is doing | measured fps |
| --- | --- |
| no face in frame (eye model idle) | 19.7 |
| face held (eye model on both eyes, every frame) | 10.2-10.7 |
| a browser streaming as well | 16-19, dipping to 10 while tracking |

Still above the 15 fps target on average, and 10 fps while actively tracking is
enough for PERCLOS - a 1 s closure is 10 samples. But it is the obvious next thing
to optimise, and there are two known routes: ESP-DSP's `dsps_dotprod_f32` for the
inner loops (it is already a dependency, pulled in by esp-dl), or running the eye
model every second frame and halving the cost for a PERCLOS sampling rate of
~8 Hz.

| stage | model | input | cost | cadence |
| --- | --- | --- | --- | --- |
| face detect stage 1 | `msr_s8_v1` | 120x160x3 | 33.1 ms | every 3rd frame |
| face detect stage 2 | `mnp_s8_v1` | 48x48x3 | 5.8 ms | every 3rd frame |
| eye state x2 | `open_closed_eye` | 32x32x3 | **~45 ms measured** | every frame a face is held |
| behaviour + PERCLOS | — | — | <1 ms | every frame |
| frame copy for the preview | — | 240x240 RGB565 | ~1-2 ms | only while a browser is connected |

Amortised: `(33.1 + 5.8) / 3 ≈ 13 ms` detector + ~6 ms eyes + ~2 ms handoff ≈
**21 ms/frame, so 15-20 fps** with headroom on a 240 MHz dual-core part.

The JPEG encode for the preview — roughly 20 ms for a 240x240 frame at quality 80
— is deliberately *not* in that table. It runs in the stream task, pinned to
core 1, on a second snapshot buffer; the capture loop's only cost is the copy
above, and even that is skipped when no browser is connected. The preview is a
diagnostic, and a diagnostic that slows the thing it measures is worth less than
no diagnostic at all.

Two decisions make that budget work:

1. **Detect every 3rd frame, track in between.** Face position changes slowly compared
   to eyelids. `DETECT_EVERY = 3` in `main.cpp`, with the last box held when detection
   drops — deliberately, because detectors tend to lose the face exactly when the eyes
   close, which is the moment of interest.
2. **15 fps is enough, by design.** PERCLOS needs temporal *coverage*, not a high frame
   rate: at 15 fps a 1 s microsleep is still 15 samples. Blink counting degrades at low
   frame rates, which is why blinks carry only 0.20 of the fused score and PERCLOS 0.55.

Memory: the eye model is 46 KB and the detectors are a few hundred KB, all comfortable
in 8 MB PSRAM. The preview adds two 115 KB RGB565 snapshot buffers and two 48 KB
JPEG buffers, all in PSRAM, allocated once at boot by `web_server_start()`.

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

## The interface: a web page, not a panel

The build is headless. `web_server.cpp` runs a SoftAP and two HTTP servers, and
`web/index.html` — compiled into the binary as flash rodata — renders:

- the live MJPEG preview with the tracked face box drawn client-side over it in a
  canvas (green fresh, amber held, red while alerting),
- the fused risk score, its trigger and the current streak toward an alert, so a
  warning can be seen coming rather than only heard,
- the PERCLOS bar, per-frame eye-closure probability and current closure length,
- blink / long-blink / yawn / nod rates per minute, head roll, jaw drop and the
  pitch proxy, plus `mouth open`, `head down`, `learning baselines` and
  `sneeze filter active` — so a suppressed false alarm is visible rather than
  mysterious,
- a two-minute risk sparkline with the trigger drawn as a dashed line,
- an event log, the alert count, and a mute switch and speaker self-test,
- uptime, frame count, fps, viewer count, free heap and free PSRAM.

Why this replaced the SPI panel, in one line each:

- **Cost.** The panel held five GPIOs, a 150 KB PSRAM framebuffer, a per-frame
  software blit and one managed component per panel variant.
- **Legibility.** Everything above is *more* than 240x320 of 8-pixel text could
  carry, on a screen large enough to read from the passenger seat.
- **Instrumentation.** `GET /api/status` returns the same numbers as JSON, so the
  acceptance tests in `DEPLOYMENT.md` script instead of being read off glass.
- **Isolation.** The detection loop hands over a frame and returns; it does no
  encoding, and does nothing at all when no browser is connected. The alert path
  never touches the network.

The one thing lost is a display in the car with no phone in it. That is a real
regression for a shipping product and a non-issue for a thesis instrument, and it
is the reason the speaker path — not the preview — is what the safety-relevant
code protects.

## Spoken alerts

`voice_alert.h` defines `AlertReason` — `Drowsy`, `Microsleep`, `Yawning`, `HeadNod` —
each mapping to its own recorded clip and on-page banner (`WAKE UP`, `TAKE A BREAK`,
`STAY ALERT`, `DROWSY`). A named cause is far more actionable than a generic chime.
Clips are prerecorded PCM in flash rather than on-MCU synthesis, for predictable latency
and multilingual output; see `assets/audio/README.md`. Until approved clips exist each
reason plays its own tone pattern over I2S.

Two properties matter more now that the speaker is the only output the driver
perceives: playback runs on its own task so it can never stall the capture loop, and the
three-per-episode repeat cap resets after five minutes of calm (`repeat_reset_ms`) so a
long drive cannot silence the alarm permanently.

## Status

The firmware builds clean against ESP-IDF v5.5, but it has never been flashed — no
ESP32-S3 board exists in this environment. The behaviour logic is a direct port of
Python that is unit-tested, and the constants are guarded by a parity test, but treat
the ESP-DL call sites, the I2S path and the Wi-Fi/HTTP path as unverified scaffolding.
