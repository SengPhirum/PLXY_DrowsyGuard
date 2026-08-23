---
title: Training and export
---

# Training and export

The path from a prepared dataset to a file the ESP32-S3 can run:

```text
prepare -> train -> evaluate -> export-onnx -> quantize-espdl -> flash
```

## Train

```bash
drowsyguard train --config configs/train.yaml
```

The config carries every hyperparameter; nothing is passed on the command line
except which config to use. See [Configuration](../configuration/index.md#training-configuration)
for the full field list, and `configs/train_ddd.yaml` for the DDD variant
(larger batch, fewer epochs, 8 dataloader workers because PNG decoding dominates
on CPU).

## Evaluate

```bash
drowsyguard evaluate --config configs/train.yaml --checkpoint models/best.pt
```

| Flag | Effect |
| --- | --- |
| `--per-subject` | break accuracy down by driver |
| `--split {train,val,test}` | evaluate a specific split |

**Always look at `--per-subject`.** A mean accuracy across drivers hides the
drivers the model fails on completely, which is exactly the failure mode this
project has already hit once: a whole-face classifier that scored well on
average had learned to recognise *drivers*, not drowsiness.

## Export to ONNX

```bash
drowsyguard export-onnx --config configs/train.yaml --checkpoint models/best.pt
```

Writes the path named by `onnx_output` in the config (`models/drowsyguard.onnx`
by default).

## Quantize to `.espdl`

```bash
drowsyguard quantize-espdl \
    --onnx models/drowsyguard.onnx \
    --calib data/processed/train \
    --output models/drowsyguard.espdl
```

This is the only step that needs `esp-ppq`; `drowsyguard doctor` reports it as
`OPTIONAL/MISSING` until you install it. The calibration set should be real
training frames — INT8 ranges derived from anything else will not match what the
camera produces.

The resulting `.espdl` is what ESP-DL loads on the device. Binding it into the
firmware is [stage 3 of the bring-up](../HARDWARE_SETUP.md), and the compute
budget it has to fit is in the [on-device pipeline](../FIRMWARE_PIPELINE.md).

## The eye model

The shipped detection path does **not** use a trained-from-scratch drowsiness
classifier. It uses `open-closed-eye-0001` (OpenVINO Model Zoo, Intel,
Apache-2.0) — 11.3k parameters, 0.0014 GFLOPs, 46 KB, ~0.9 ms per frame for both
eyes on device.

`scripts/export_eye_model.py` and `scripts/quantize_espdl.py` handle that model's
export and quantization; the weights the firmware compiles in live in
`firmware/esp32s3/main/eye_model_weights.h`.

!!! warning "The open task"
    `open-closed-eye-0001` was trained on the MRL **infrared** eye dataset. On
    DDD's visible-light crops — eye region ~45 px, blurry — it separates
    alert/drowsy at only AUC 0.62 against its claimed 95.84% in-domain. It is
    expected to behave much better on a sharp live webcam, and the project plans
    IR illumination for night use, which matches its training domain.
    Fine-tuning it on visible-light eye-state labels is the open task.

## Keeping device and desktop in step

`tests/test_firmware_parity.py` parses the `RiskFilter` constructor in
`firmware/esp32s3/main/risk_filter.h` and compares it with
`src/drowsyguard/risk.py`. Run it after any threshold change:

```bash
./plxy.sh test
```
