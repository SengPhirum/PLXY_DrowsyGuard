#pragma once
/*
ESP-DL binding. Version-specific on purpose: ESP-DL's C++ API has changed shape
between 2.x and 3.x, so everything that touches it is confined to this pair of files.

Two models, two cadences (see docs/FIRMWARE_PIPELINE.md):
  - face + 5 landmarks, every DETECT_EVERY frames  -> model_detect_face()
  - eye open/closed, one eye per call              -> model_eye_closed_prob()

Everything about *which* detection to believe and *where* to look for the next one
lives in face_gate.cpp instead, because it is arithmetic on five points and a
rectangle and can therefore be compiled and tested on the host. This file is only
the part that cannot be.
*/

#include <cstdint>

#include "behavior.h"
#include "face_gate.h"

struct FaceDetection {
    bool valid = false;
    int x = 0, y = 0, w = 0, h = 0;   // box in FRAME coordinates, ROI already undone
    float score = 0.0f;
    // Landmarks in DrowsyGuard's canonical order and in frame coordinates. ESP-DL's
    // own order differs (left eye, left mouth, nose, right eye, right mouth) and the
    // reorder now happens inside model_adapter.cpp, so callers cannot get it wrong -
    // which they previously could, silently, because a permuted landmark set still
    // produces plausible-looking numbers.
    Landmarks lm{};
};

// Loads the models. Returns false when the adapter has not been bound to a pinned
// ESP-DL release yet; the firmware then runs preview-only instead of refusing to
// boot, so the camera and panel can be validated on their own.
bool model_init();

// The two models bind independently, so they are reported independently. The face
// detector is bound (espressif/human_face_detect); the eye model runs in float from
// eye_model.cpp - see model_adapter.cpp.
//
// Callers MUST gate PERCLOS and alerting on this. model_eye_closed_prob() returns
// 0.0 while unbound, and 0.0 fed into PERCLOS is indistinguishable from a driver
// whose eyes are open forever: the risk score would sit at zero and the alarm would
// simply never fire.
bool model_eye_ready();

// Best face in one RGB565 frame, or false if there is none worth believing.
//
// Two things happen here that did not before, both of them accuracy rather than
// speed (see face_gate.h for the reasoning):
//
//   * candidates are filtered by landmark plausibility and then chosen by overlap
//     with the previous accepted box, not by raw size;
//   * once a face is being tracked, the search is a padded square crop around the
//     last box rather than the whole frame, which gives the face more pixels in the
//     detector's fixed-size input - 1.4x to 2.5x depending on how small it is, and
//     nothing once it is large enough not to need it.
//
// `full_frame` forces the crop off for this call. main.cpp asserts it periodically:
// a track that drifts onto something else would otherwise keep confirming itself
// inside its own crop, and a driver who moves outside the crop between sweeps would
// never be re-found.
bool model_detect_face(const uint8_t *rgb565, int width, int height, bool full_frame,
                       FaceDetection *out);

// Forget the tracked box, so the next call searches the whole frame. Call this when
// the caller gives up holding the last detection.
void model_detect_forget();

// What the last model_detect_face() call actually did. For the status page and the
// once-a-second log line: "no face" and "a face the gate threw away" are different
// problems with different fixes, and without this they look identical.
struct ModelDetectStats {
    bool used_roi = false;
    int roi_x = 0, roi_y = 0, roi_w = 0, roi_h = 0;
    int candidates = 0;   // boxes ESP-DL returned
    int rejected = 0;     // of those, how many failed the plausibility gate
    // Which check the first failing candidate failed. A count on its own is not
    // actionable - "gate dropped 2" names a symptom and no cause, which is how an
    // unvalidated gate silently killed detection on hardware once already.
    FaceReject reject = FaceReject::None;
    int64_t us = 0;       // wall time of the last call
};
void model_detect_stats(ModelDetectStats *out);

// Probability that one eye is closed. `eye`: 0 = right, 1 = left, in DrowsyGuard's
// canonical landmark order. `face_side` is the detected face box's side in pixels.
//
// One eye per call, and main.cpp alternates them: this is the most expensive thing
// in the frame budget by a wide margin, and the two eyes of one face close together,
// so sampling one per frame keeps full temporal resolution on the closure while
// halving the cost. docs/FIRMWARE_PIPELINE.md has the measured numbers.
//
// Preprocessing matches src/drowsyguard/eyestate.py, and it has to: the PERCLOS
// threshold and the fusion weights were tuned against that implementation, so a
// different crop or scaling here silently invalidates all of them. Specifically -
// a square patch of `face_side * 0.20` centred on the eye landmark, bilinear
// resize to 32x32, BGR channel order, and (pixel - 127) / 255.
//
// The published model card for open-closed-eye-0001 is wrong about the input
// scaling, the softmax and the output order; eyestate.py is the truth, and
// tests/test_eye_model_parity.py holds the network itself to the ONNX graph.
float model_eye_closed_prob(const uint8_t *rgb565, int width, int height,
                            const Landmarks &lm, int eye, int face_side);
