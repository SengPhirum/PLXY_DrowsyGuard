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

Tracking is not bare per-frame detection: the box is EMA-smoothed and **held for
15 frames** when detection drops. That hold matters here, because detectors tend
to lose the face at exactly the moment of interest — eyes closing, head nodding —
and without it the crop would jump back to the whole frame right then. The status
pill reads `face 0.94`, `face held`, or `no face`.

Measured over 400 DDD images the detected face box is ~1.02× the image side,
i.e. DDD crops are extremely tight, so `--face-margin` defaults to `0`: the
detected box *is* the training framing. Raising it moves the live input out of
distribution.

Use `--no-face-detect` to fall back to the centre crop, in which case `--zoom`
applies:

```bash
python -m drowsyguard.cli live --no-face-detect --zoom 0.45
```

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
