#include "eye_model.h"

#include <cmath>

#include "eye_model_weights.h"

/*
Layer-for-layer transcription of the ONNX graph. Written out longhand rather than
looped over a layer table: there are four convolutions, they all differ, and a
generic mini-framework would be more code than the thing it abstracts - and much
harder to check against the graph by eye.

    input 3x32x32
    conv1  3->10  3x3 valid  +bias      -> 10x30x30  |  fused with the pool below
    maxpool 2x2 s2, then relu           -> 10x15x15  |
    conv2  10->20 3x3 valid  +bias      -> 20x13x13  |  fused
    maxpool 2x2 s2, then relu           -> 20x6x6    |  (13/2 rounds down)
    conv3  20->50 3x3 valid  +bias      -> 50x4x4       no activation after this
    conv4  50->2  1x1        NO bias    -> 2x4x4
    maxpool 4x4 s4                      -> 2x1x1
    softmax over the 2 channels

Two things here are easy to get wrong and are the reason for the parity test:
conv3 is followed directly by conv4 with **no ReLU between them**, and conv4 has
**no bias term**. Both are what the graph says.
*/

// Activations, static rather than stacked: the largest is 10x15x15 and app_main's
// stack is 8 kB. Only ever touched from the capture loop.
static float s_p1[EYE_C1 * 15 * 15];   //  9.0 kB - conv1 + pool + relu
static float s_p2[EYE_C2 * 6 * 6];     //  2.9 kB - conv2 + pool + relu
static float s_a3[EYE_C3 * 4 * 4];     //  3.2 kB - conv3
static float s_a4[EYE_OUT * 4 * 4];    //  0.1 kB - conv4

// conv1 (3->10, 3x3 valid on 32x32 -> 30x30) fused with maxpool 2x2 s2 and relu.
// Fused so the 10x30x30 intermediate (36 kB) is never allocated: each pooled
// output looks at the four convolution positions under it and keeps the largest.
static void conv1_pool_relu(const float *in) {
    for (int oc = 0; oc < EYE_C1; ++oc) {
        const float bias = kEye_conv1_bias[oc];
        const float *w = kEye_conv1_weight + oc * (EYE_IN_C * 9);
        for (int py = 0; py < 15; ++py) {
            for (int px = 0; px < 15; ++px) {
                float best = -INFINITY;
                for (int dy = 0; dy < 2; ++dy) {
                    for (int dx = 0; dx < 2; ++dx) {
                        const int oy = py * 2 + dy;   // 0..29
                        const int ox = px * 2 + dx;
                        float sum = bias;
                        for (int ic = 0; ic < EYE_IN_C; ++ic) {
                            const float *plane = in + ic * (EYE_IN_HW * EYE_IN_HW);
                            const float *k = w + ic * 9;
                            for (int ky = 0; ky < 3; ++ky) {
                                const float *row = plane + (oy + ky) * EYE_IN_HW + ox;
                                sum += row[0] * k[ky * 3 + 0] +
                                       row[1] * k[ky * 3 + 1] +
                                       row[2] * k[ky * 3 + 2];
                            }
                        }
                        if (sum > best) best = sum;
                    }
                }
                s_p1[oc * 225 + py * 15 + px] = best > 0.0f ? best : 0.0f;
            }
        }
    }
}

// conv2 (10->20, 3x3 valid on 15x15 -> 13x13) fused with maxpool 2x2 s2 and relu.
// 13 is odd, so with ONNX's default ceil_mode=0 the pool covers rows/columns 0..11
// and the last one is dropped: 6x6 out, not 7x7.
static void conv2_pool_relu() {
    for (int oc = 0; oc < EYE_C2; ++oc) {
        const float bias = kEye_conv2_bias[oc];
        const float *w = kEye_conv2_weight + oc * (EYE_C1 * 9);
        for (int py = 0; py < 6; ++py) {
            for (int px = 0; px < 6; ++px) {
                float best = -INFINITY;
                for (int dy = 0; dy < 2; ++dy) {
                    for (int dx = 0; dx < 2; ++dx) {
                        const int oy = py * 2 + dy;   // 0..11
                        const int ox = px * 2 + dx;
                        float sum = bias;
                        for (int ic = 0; ic < EYE_C1; ++ic) {
                            const float *plane = s_p1 + ic * 225;
                            const float *k = w + ic * 9;
                            for (int ky = 0; ky < 3; ++ky) {
                                const float *row = plane + (oy + ky) * 15 + ox;
                                sum += row[0] * k[ky * 3 + 0] +
                                       row[1] * k[ky * 3 + 1] +
                                       row[2] * k[ky * 3 + 2];
                            }
                        }
                        if (sum > best) best = sum;
                    }
                }
                s_p2[oc * 36 + py * 6 + px] = best > 0.0f ? best : 0.0f;
            }
        }
    }
}

// conv3 (20->50, 3x3 valid on 6x6 -> 4x4). No activation follows it in the graph.
static void conv3() {
    for (int oc = 0; oc < EYE_C3; ++oc) {
        const float bias = kEye_conv3_bias[oc];
        const float *w = kEye_conv3_weight + oc * (EYE_C2 * 9);
        for (int oy = 0; oy < 4; ++oy) {
            for (int ox = 0; ox < 4; ++ox) {
                float sum = bias;
                for (int ic = 0; ic < EYE_C2; ++ic) {
                    const float *plane = s_p2 + ic * 36;
                    const float *k = w + ic * 9;
                    for (int ky = 0; ky < 3; ++ky) {
                        const float *row = plane + (oy + ky) * 6 + ox;
                        sum += row[0] * k[ky * 3 + 0] +
                               row[1] * k[ky * 3 + 1] +
                               row[2] * k[ky * 3 + 2];
                    }
                }
                s_a3[oc * 16 + oy * 4 + ox] = sum;
            }
        }
    }
}

// conv4 (50->2, 1x1) - a per-pixel linear combination across channels, and it has
// no bias tensor at all.
static void conv4() {
    for (int oc = 0; oc < EYE_OUT; ++oc) {
        const float *w = kEye_conv4_weight + oc * EYE_C3;
        for (int i = 0; i < 16; ++i) {
            float sum = 0.0f;
            for (int ic = 0; ic < EYE_C3; ++ic) sum += s_a3[ic * 16 + i] * w[ic];
            s_a4[oc * 16 + i] = sum;
        }
    }
}

void eye_model_infer(const float *input, float *out_closed, float *out_open) {
    if (input == nullptr) return;

    conv1_pool_relu(input);
    conv2_pool_relu();
    conv3();
    conv4();

    // maxpool 4x4 s4 over the 4x4 map: one value per class.
    float logit[EYE_OUT];
    for (int oc = 0; oc < EYE_OUT; ++oc) {
        float best = -INFINITY;
        for (int i = 0; i < 16; ++i) {
            const float v = s_a4[oc * 16 + i];
            if (v > best) best = v;
        }
        logit[oc] = best;
    }

    // The graph's own softmax tail (Exp / ReduceSum / Div), shifted by the max
    // first. The graph does not do that shift, but it is algebraically identical
    // and it is what stops exp() overflowing to inf on an out-of-range crop -
    // which is exactly the NaN the model card's missing normalisation causes.
    const float m = logit[0] > logit[1] ? logit[0] : logit[1];
    const float e0 = expf(logit[0] - m);
    const float e1 = expf(logit[1] - m);
    const float sum = e0 + e1;
    if (out_closed != nullptr) *out_closed = e0 / sum;
    if (out_open != nullptr) *out_open = e1 / sum;
}

float eye_model_infer_closed(const float *input) {
    float closed = 0.0f;
    eye_model_infer(input, &closed, nullptr);
    return closed;
}
