# Thesis Plan

## Working title
**A Lightweight Camera-Based Driver Drowsiness Detection System for Retrofit Deployment on Resource-Constrained Microcontrollers**

## Problem statement
Modern vehicles increasingly include driver-monitoring systems, but many older vehicles lack them. A low-cost retrofit device could provide basic drowsiness warnings without replacing the vehicle electronics. The challenge is to obtain useful drowsiness classification while meeting microcontroller limits in memory, compute, power, latency and cost.

## Main research question
How accurately and efficiently can a compact vision model running on an ESP32-S3-class microcontroller detect driver drowsiness from a low-resolution driver-facing camera?

## Sub-questions
1. What accuracy/F1 tradeoff is obtained as model size is reduced?
2. How much does temporal aggregation reduce false alarms compared with single-frame classification?
3. How well does the model generalize to unseen drivers?
4. How do illumination, glasses and head pose affect performance?
5. What are the memory, latency and power costs of real-time deployment?

## Objectives
- Design a low-cost retrofit hardware/software architecture.
- Train a compact drowsiness classifier using subject-independent data splits.
- Quantize and deploy the model on ESP32-S3.
- Add temporal risk logic to suppress transient false alerts.
- Measure classification, latency, memory, power and false-alarm performance.
- Evaluate robustness across driver and environmental conditions.

## Hypotheses
H1: INT8 quantization can reduce model memory substantially with an acceptable reduction in F1.
H2: Temporal aggregation will reduce false alarms relative to frame-by-frame thresholding.
H3: Performance on unseen subjects will be lower than random-frame splits, demonstrating the importance of subject-independent evaluation.

## Variables
Independent: model size, quantization, temporal window, threshold, illumination, glasses, subject.
Dependent: F1, recall/sensitivity, specificity, ROC-AUC, false alarms/hour, latency, peak RAM, flash, power.

## Experimental groups
- Baseline A: single-frame tiny CNN.
- Baseline B: single-frame INT8 CNN.
- Proposed: INT8 CNN + temporal risk accumulator.
- Optional: proposed + IR illumination/night camera.

## Methodology
1. Prepare public/local dataset with subject IDs.
2. Split by subject.
3. Train compact models on workstation.
4. Evaluate float model on held-out subjects.
5. Quantize to ESP-DL INT8.
6. Evaluate quantized host/on-device output agreement.
7. Deploy to MCU and measure latency/RAM/power.
8. Run controlled simulator/parked-vehicle tests for false alarms and robustness.
9. Perform statistical comparison and ablation study.

## Ethics and safety
Do not induce dangerous drowsy driving on public roads. Prefer public datasets, controlled laboratory protocols, simulators, or parked-vehicle recordings. Obtain institutional ethics approval when collecting human-subject data.
