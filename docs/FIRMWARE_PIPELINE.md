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

### What was done about it

**One eye per frame, alternating.** The eye model ran on both eyes every frame; it
now runs on one, and PERCLOS is fed the mean of the two most recent readings. This is
an exact halving of the dominant cost for almost nothing: the two eyes of one face
close together, so there is still one closure measurement per frame - it is the
per-eye refresh interval that goes to two frames, not the time resolution of the
closure. The quantity handed to PERCLOS is the same mean of two eyes as before, one
frame staler on one side.

**Three accumulators in the convolution inner loop.** Worth 8% on the host, and
bit-identical output. Three other restructurings were tried and were slower;
`eye_model.h` lists all four with their numbers so they are not tried again. The
interesting failure was gathering each patch into a contiguous buffer to cut input
re-reads by an order of magnitude - it lost 20%, because the premise was wrong: the
inner loop was never a serial accumulate, so there was no stall to remove.

**The loop now measures itself.** `ms.detect` and `ms.eye` are in `/api/status`, on
the Device card and in the once-a-second log line, and `bench_eye_model()` prints one
figure for the eye model at boot on a fixed synthetic tensor. This section existed
for a long time with an estimate in it that was wrong by a factor of six; an estimate
nobody checks is a comment.

Still open, and now measurable rather than guessed at: int8 or int16 arithmetic
through the S3's vector unit - which is what ESP-DL does and what a quantized
`.espdl` would unlock - and running the eye model on the second core in parallel with
the face detector. ESP-DSP's `dsps_dotprod_f32` is *not* on that list: the S3's
128-bit vector unit is 8- and 16-bit integer only, so there is no float SIMD on this
part to reach.

### Frame rate is not a free parameter

Making the loop faster used to change how sensitive the alarm was, silently. The
PERCLOS window and the risk filter's confirmation and cooldown were all frame counts
chosen as durations - 8 frames is about half a second at 15 fps and a third of a
second at 25 - so a speed-up made the alarm twitchier and shortened the PERCLOS
window without anyone editing a threshold.

They are now durations (`PERCLOS_WINDOW_S`, `RISK_REQUIRED_S`, `RISK_COOLDOWN_S` in
`main.cpp`) converted to frames once a second from the measured rate. `Perclos`
keeps its samples across a resize rather than clearing - a PERCLOS of zero is
indistinguishable from eyes wide open, which is the one way that estimator must never
be wrong - and the risk filter keeps its streak.

| stage | model | input | cost | cadence |
| --- | --- | --- | --- | --- |
| face detect stage 1 | `msr_s8_v1` | 120x160x3 | 33.1 ms | every 3rd frame |
| face detect stage 2 | `mnp_s8_v1` | 48x48x3 | 5.8 ms | every 3rd frame |
| crop staging for the detector | — | up to 240x240 RGB565 | <0.1 ms | every 3rd frame, while tracking |
| eye state | `open_closed_eye` | 32x32x3 | **~22 ms measured, per eye** | one eye per frame, alternating |
| behaviour + PERCLOS | — | — | <1 ms | every frame |
| frame copy for the preview | — | 240x240 RGB565 | ~1-2 ms | only while a browser is connected |

Amortised: `(33.1 + 5.8) / 3 ≈ 13 ms` detector + ~22 ms for one eye + ~2 ms handoff
≈ **37 ms/frame while tracking**. Take the fps column above as the record of what the
board produced before this change; the numbers after it are what `ms.detect`,
`ms.eye` and the boot benchmark now report, and they should be re-read off the device
rather than copied from here.

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
3. **Detections are gated, not just scored.** The coarse detector stage has to run at
   a score threshold of 0.10 on this camera, so weak candidates arrive; `face_gate.cpp`
   checks that the five landmarks actually describe a face before anything is measured
   from them, and picks the candidate that overlaps the previous one rather than the
   biggest. See the detection-gate section below.

Memory: the eye model is 46 KB and the detectors are a few hundred KB, all comfortable
in 8 MB PSRAM. The preview adds two 115 KB RGB565 snapshot buffers and two 48 KB
JPEG buffers, all in PSRAM, allocated once at boot by `web_server_start()`.

## The detection gate

Everything downstream - jaw drop, mouth width, both pitch channels, the eye crops -
is computed from five landmark positions, so which detection is believed and how
precisely it is placed is not a detail. `face_gate.cpp` holds three decisions, and it
is deliberately free of ESP-IDF headers so `tests/test_face_gate.py` can compile it on
the host and check the arithmetic.

**Plausibility.** A five-point landmark set has structure: the eyes are roughly level,
the mouth is below them, the nose is between them, and the interocular distance is a
fairly fixed fraction of face width. `face_gate_plausible()` rejects anything that
violates it. This is what pays for the loose 0.10 stage threshold - lowering a score
gate without it is how a headrest ends up driving PERCLOS.

**Which face is the driver.** Previously the largest box won. A passenger leaning
forward is a bigger face than the driver, so that rule handed them the box, the
landmarks, the eye crops and the alarm. The gate now prefers the candidate that
overlaps the previously accepted one, and falls back to largest only when there is no
live track.

**Where to look.** While tracking, the detector is given a padded square crop around
the last box instead of the whole frame. Two effects: the face gets more pixels in the
detector's fixed-size input (1.4x linear at an 80-pixel box, 2.5x for a small one,
and nothing past ~93 pixels where the crop is not worth taking - so it helps exactly
where landmark precision is worst), and a face outside the window cannot be proposed
at all, which excludes a passenger geometrically.

Every tenth detection ignores the track and sweeps the whole frame anyway. Without
that, a crop that had drifted onto something else would keep confirming itself inside
its own window, and a driver who moved outside it would never be re-found. A miss
inside a crop costs the track rather than triggering a retry, so the next detection is
a full sweep - one detect interval later, which the held box already covers.

`/api/status` reports `face.roi`, `face.roi_w` and `face.rejected`, because "no face
in the frame" and "a face the gate threw away" look identical without them and have
completely different fixes.

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
