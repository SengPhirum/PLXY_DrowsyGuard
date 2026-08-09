# DrowsyGuard MCU

Low-cost, camera-based driver drowsiness detection research project designed for retrofit use in older vehicles.

## Target
- MCU: ESP32-S3 with PSRAM
- Camera: OV2640/OV5640 class camera
- Runtime: ESP-IDF + ESP-DL
- Model: tiny grayscale CNN, INT8 quantized to `.espdl`
- Safety logic: temporal smoothing + sustained-risk trigger + cooldown

## Workflow
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

drowsyguard doctor
drowsyguard prepare --input data/raw --output data/processed
drowsyguard train --config configs/train.yaml
drowsyguard evaluate --config configs/train.yaml --checkpoint models/best.pt
drowsyguard export-onnx --config configs/train.yaml --checkpoint models/best.pt
drowsyguard quantize-espdl --onnx models/drowsyguard.onnx --calib data/processed/train --output models/drowsyguard.espdl
```

## Thesis principle
Use subject-independent train/validation/test splits. Never leak neighboring frames from the same driver across splits.

## Project memory
Read `PROJECT_STATE.md`, `ROADMAP.md`, `CHANGELOG.md`, and `docs/AI_HANDOFF.md` before major changes.

## Safety
Research prototype only; not a certified automotive safety device.
