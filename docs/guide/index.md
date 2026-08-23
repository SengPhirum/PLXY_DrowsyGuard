---
title: User manual
---

# User manual

Everything the project does, grouped by what you are holding.

## Hardware build

| Page | What it covers |
| --- | --- |
| [Hardware setup tutorial](../tutorials/hardware-setup/README.md) | The long form: four components, seven wires, eleven labelled diagrams, per-component tests, a final verification checklist |
| [Toolchain and firmware install](../HARDWARE_SETUP.md) | The shorter reference — parts justification, wiring table, ESP-IDF setup, and the three staged bring-up milestones |
| [Voice alert hardware](../VOICE_ALERT_HARDWARE.md) | The MAX98357A amplifier, the clip set, and the alert state machine |

## Working with the device

| Page | What it covers |
| --- | --- |
| [Firmware dev loop](dev-loop.md) | `plxy.sh` end to end: build, flash, monitor, and the two hardware quirks it works around |
| [Using the device](device.md) | Joining the access point, reading the live page, alerts, snapshots and SD-card events |

## Desktop toolkit

| Page | What it covers |
| --- | --- |
| [Live dashboard](live-dashboard.md) | Real-time webcam testing and threshold tuning before any board exists |
| [Datasets](datasets.md) | The subject-independent layout, video decoding, and importing DDD |
| [Training and export](training.md) | Train, evaluate per driver, export ONNX, quantize to `.espdl` |

## The rule that shapes all of it

> Use subject-independent train/validation/test splits. Never leak neighbouring
> frames from the same driver across splits.

A random split over DDD's flat class folders puts the same face — and adjacent
frames of one video — in both train and test. That is why published DDD
accuracies near 99% are usually not comparable to a subject-independent number,
and it is why [`prepare`](datasets.md) refuses to split into a non-empty output.
