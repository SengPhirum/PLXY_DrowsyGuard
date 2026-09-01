---
title: Live dashboard
---

# Live dashboard

Real-time webcam testing in the browser, before any board exists.

```bash
pip install -e ".[live]"
drowsyguard fetch-models              # one-off: YuNet detector + eye-state model
python -m drowsyguard.cli live        # eye mode is the default; no checkpoint needed
# then open http://127.0.0.1:8000
```

!!! danger "No authentication"
    The dashboard is a local research tool. It binds to `127.0.0.1` and has no
    authentication of any kind, and it streams your webcam. Only pass
    `--host 0.0.0.0` on a network you trust. See [Security](../security.md).

## Why it exists

The dashboard runs the **same** preprocessing as training (`preprocess_gray`)
and the **same** decision logic as the firmware —
`drowsyguard.risk.RiskFilter` mirrors `firmware/esp32s3/main/risk_filter.cpp`,
and `tests/test_firmware_parity.py` fails if they drift. So a threshold that
looks right here is a threshold that behaves the same on the board.

The **Copy as C++** button emits the `RiskFilter` constructor line to paste into
`risk_filter.h`. That is the intended path from tuning to firmware.

## The panels

| Panel | Shows |
| --- | --- |
| Camera feed | the frame, with the detected face box and YuNet's five landmarks |
| Model input | the exact grayscale tensor the model receives |
| Eyes | both eye crops, P(closed) per eye, and the PERCLOS bar |
| Risk | p(drowsy) over time, plus the streak and cooldown state of `RiskFilter` |
| Sliders | trigger, required and cooldown, applied live |

Run without `--checkpoint` in face mode and the model is untrained: the camera
and the decision path are real but the probabilities are meaningless, and the
page says so.

## Two modes

=== "Eye mode (default)"

    Measures eyelid closure directly and integrates it into PERCLOS. Needs no
    checkpoint — it uses the downloaded `open-closed-eye-0001` model.

    ```bash
    python -m drowsyguard.cli live
    ```

=== "Face mode"

    The old whole-face drowsiness CNN. Requires a checkpoint, and is kept for
    comparison rather than recommended — it learned to recognise *drivers*.

    ```bash
    python -m drowsyguard.cli live --mode face --checkpoint models/<file>.pt
    ```

## Face detection and tracking

The dashboard detects the face with OpenCV's **YuNet** and crops to it
automatically, so the model receives a face-filling input like its training data
instead of a face in a room. (OpenCV 5 removed `CascadeClassifier` and ships no
bundled cascades, so Haar is not an option; YuNet is downloaded by
`fetch-models`.)

Detection alone is not enough, and that is not a theoretical point: a hand, a
headrest, a phone or a patch of low-light noise all produce boxes, and one believed
frame is enough to start feeding a rolling baseline and a PERCLOS window with
measurements of something that is not a person. Every candidate therefore goes
through `drowsyguard.facegate`, which is a transcription of the firmware's own gate —
confidence, box size and shape, the five landmarks' geometry, then agreement across
consecutive detections. The dashboard is where thresholds get tuned, so it has to
apply the ones the device will.

What that means in practice:

- **A driver is not present until two consecutive detections agree.** At 30 fps that
  is about 70 ms at acquisition. One frame is not evidence.
- **The box is EMA-smoothed and held** for `FACE_HOLD_DETECTIONS` attempts when
  detection drops, because detectors lose the face at exactly the moment of interest —
  eyes closing, head nodding — and without the hold the crop would jump back to the
  whole frame right then.
- **A candidate that has teleported is refused** while the track is warm. That is
  what stops the crop stepping off the driver onto a passenger who leaned in.
- **After a longer gap the requirement is dropped** so a driver who genuinely moved can
  be found again — but the new track is *pending* and has to earn confirmation from
  scratch. Presence is re-earned across a discontinuity, never inherited.

The status pill reads `face 0.94`, `face held`, or — when something was in frame and
the gate refused it — the reason: `score-too-low`, `box-not-head-shaped`,
`nose-outside-eye-pair`, `moved-too-far`. Each names a different fix, and none of them
is the same problem as an empty room.

### The driver readout

Next to the counters, `driver` shows either `present`, how long the seat has looked
empty against the threshold, or `camera fault` / `model fault`. When the absence
crosses the threshold, the state pill reads **NO DRIVER** and an alert is logged with
that reason — once per absence episode, not once per frame.

This is the same `PresenceMonitor` the firmware runs, parity-tested against it, so
what you see here is what the board will do. Turn it off, or change the threshold,
without restarting:

```bash
curl -X POST http://127.0.0.1:8000/config \
     -H 'Content-Type: application/json' \
     -d '{"no_driver_after": 8.0}'
curl -X POST http://127.0.0.1:8000/config \
     -H 'Content-Type: application/json' \
     -d '{"no_driver_alert": false}'
```

The second is worth knowing about for bench work, where an empty seat is the normal
state and the announcement is only noise.

Measured over 400 DDD images the detected face box is ~1.02× the image side,
i.e. DDD crops are extremely tight, so `--face-margin` defaults to `0`: the
detected box *is* the training framing. Raising it moves the live input out of
distribution.

Use `--no-face-detect` to fall back to the centre crop, in which case `--zoom`
applies:

```bash
python -m drowsyguard.cli live --no-face-detect --zoom 0.45
```

## Sneezes

The **sneezes** readout shows two numbers — detected, and announced. They are
deliberately different: one sneeze is often two or three closures a second apart, each
a real detection, and announcing every one of them is noise. Detection is per closure;
the announcement is edge-triggered with a 2.5 s cooldown, so a fit becomes one alert.

Each detection also suppresses the drowsiness score for `SNEEZE_MAX_S`, which is the
original reason the cue exists — a sneeze slams the eyes shut for about a second, and
an eye-closure detector would otherwise record a microsleep. Watch the **sneeze filter
active** pill during one: a suppressed false alarm is visible rather than silent.

To see the discrimination working, try a deliberate yawn with the eyes closed. It must
*not* register as a sneeze, and the microsleep must still fire — a yawn misread as a
sneeze would silence a genuine drowsiness cue. What separates them is when the mouth
opened relative to the eyes closing, not how wide.

## Replaying a recording

```bash
python -m drowsyguard.cli live --source path/to/clip.mp4
```

Replays at the clip's native frame rate instead of reading a webcam, which makes
threshold comparisons repeatable.

## Flags

Every `live` flag is listed in the [CLI reference](../reference/cli.md#live).
The HTTP endpoints the page itself uses are in the
[Dashboard HTTP API](../reference/dashboard-api.md).

## If capture is slow

On Windows, prefer `python -m drowsyguard.cli live` over the installed
`drowsyguard live` console script: the launcher can throttle webcam capture to
~1 fps. Benchmark the backends with:

```bash
drowsyguard camera-test --index 0 --frames 20
```

The dashboard also warns when capture is abnormally slow. More in
[Troubleshooting](../troubleshooting.md#the-webcam-runs-at-1-fps).
