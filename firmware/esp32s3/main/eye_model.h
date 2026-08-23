#pragma once
/*
open-closed-eye-0001, run directly in float32.

This is the eye-closure classifier the whole drowsiness mechanism depends on:
PERCLOS is the fraction of a rolling window in which the eyes were closed, it
carries 0.55 of the fused risk score, and without it the alarm can only
under-report. It was unbound for a long time and this file is what binds it.

Not via ESP-DL, which is what the face detector uses, because that route needs a
quantized `.espdl` and both halves of producing one are missing: **esp-ppq is not
on PyPI** (so the old `pip install esp-ppq` advice fails outright) and there is no
calibration set in this repository. Neither is worth solving for four convolutions
and 11,250 parameters - 45 kB of float32 weights, which this board has spare in
flash. Running it in float also takes quantization error off the table, which
matters because the model's accuracy is already the weak link (below).

Deliberately free of ESP-IDF headers. That is what lets
tests/test_eye_model_parity.py compile this exact file on the host and compare it
against onnxruntime running the original ONNX - the only way to be sure a
hand-written forward pass matches the graph it claims to implement, rather than
merely producing plausible-looking numbers.

Cost, measured rather than assumed - see docs/FIRMWARE_PIPELINE.md for the figure
this board actually produces. Roughly 693k multiply-accumulates per eye. The two
Conv+MaxPool pairs are fused so the 10x30x30 and 20x13x13 intermediates are never
materialised, which keeps the whole thing inside ~15 kB of activations and lets
them live in internal RAM instead of PSRAM.

**Accuracy warning, which binding this does not fix.** This model is IR-trained
and scores AUC 0.62 on DDD's visible-light eye crops against its claimed 95.84%
in-domain (gap 6 in PROJECT_STATE.md). Mechanically correct, still weak in
daylight. Treat a working PERCLOS number as "the pipeline is complete", not as
"the detector is accurate".
*/

#include <cstdint>

// Input tensor: 3x32x32 float, channel-first, **BGR** order, each element
// (pixel - 127.0) / 255.0.
//
// All three of those are load-bearing and none are in the model's published card;
// src/drowsyguard/eyestate.py is the source of truth and established them
// empirically. Feeding raw 0-255 overflows the network to NaN. BGR because the
// desktop path feeds OpenCV images and that is what the validation numbers were
// measured with.
#define EYE_INPUT_FLOATS (3 * 32 * 32)

// Probability that the eye is CLOSED.
//
// Index 0 of the output is `closed`, despite the model card saying the output is
// `[open, closed]`. The network's own softmax tail is included in the graph, so
// the two outputs already sum to 1 and must not be softmaxed again.
float eye_model_infer_closed(const float *input);

// Both class scores, for tests and for anyone checking the pair sums to 1.
void eye_model_infer(const float *input, float *out_closed, float *out_open);
