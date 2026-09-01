---
title: Install the toolkit
---

# Install the desktop toolkit

!!! tip "Only want the firmware on a board?"
    You do not need any of this. [Install to ESP32 from your browser](install-esp32.md)
    writes the firmware over USB from Chrome or Edge, with no toolchain at all. This
    page is for the training, evaluation and live-dashboard tooling.

The desktop side of DrowsyGuard is a normal Python package. It prepares
datasets, trains and exports the model, and serves the live tuning dashboard.
None of it needs a board.

## Requirements

- Python **3.10 or newer**
- A webcam, for the live dashboard
- ESP-IDF is **not** required here — see [the firmware dev loop](../guide/dev-loop.md)

## Install

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1

pip install -e .                   # runtime deps + the `drowsyguard` command
pip install -e ".[live]"           # ...plus FastAPI + uvicorn for the dashboard
```

Runtime dependencies are declared once, in `pyproject.toml`, so they cannot
drift apart from `requirements.txt` — that file exists only so
`pip install -r requirements.txt` keeps working out of habit.

## Verify

```bash
drowsyguard doctor
```

It prints the Python version and platform, then one line per dependency:

```text
python 3.13.0
platform Windows-11-10.0.26200-SP0
torch: OK
onnx: OK
onnxruntime: OK
yaml: OK
PIL: OK
esp_ppq: OPTIONAL/MISSING (needed only for .espdl quantization)
live UI: OK
```

`esp_ppq` is only needed for the final [quantization step](../guide/training.md#quantize-to-espdl);
everything else should read `OK` before you go further.

## Download the pretrained models

```bash
drowsyguard fetch-models
```

This fetches two files:

| Model | Source | Used for |
| --- | --- | --- |
| YuNet face detector | OpenCV Zoo | finding the face and its five landmarks |
| `open-closed-eye-0001` | OpenVINO Model Zoo (Intel, Apache-2.0) | P(closed) per eye |

The eye model is **11.3k parameters**, 0.0014 GFLOPs, 46 KB — realistic for an
ESP32-S3 at INT8, measured at ~0.9 ms per frame for both eyes.

!!! warning "Known domain gap"
    `open-closed-eye-0001` was trained on the MRL *infrared* eye dataset and does
    not transfer to DDD's visible-light crops, where the eye region is only ~45 px
    and blurry: separating DDD alert/drowsy with it reaches only AUC 0.62 against
    its claimed 95.84% in-domain. It behaves much better on a sharp live webcam,
    and the project plans IR illumination for night use, which matches the
    model's training domain. Fine-tuning it on visible-light eye-state labels is
    the open task.

## Windows note

Prefer `python -m drowsyguard.cli live` over the installed `drowsyguard live`
console script: the launcher can throttle webcam capture to ~1 fps. Check with
`drowsyguard camera-test`, which benchmarks each capture backend. The dashboard
also warns when capture is abnormally slow.

## Next

- [Quickstart](quickstart.md) — the dashboard, then the board
- [Live dashboard](../guide/live-dashboard.md) — every panel and flag
- [Datasets](../guide/datasets.md) — how splits must be built
