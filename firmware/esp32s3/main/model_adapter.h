#pragma once
/*
ESP-DL binding. Version-specific on purpose: ESP-DL's C++ API has changed shape
between 2.x and 3.x, so everything that touches it is confined to this pair of files.

Two models, two cadences (see docs/FIRMWARE_PIPELINE.md):
  - face + 5 landmarks, every DETECT_EVERY frames  -> model_detect_face()
  - eye open/closed, both eyes, every frame        -> model_eye_closed_prob()
*/

#include <cstdint>

#include "behavior.h"

struct FaceDetection {
    bool valid = false;
    int x = 0, y = 0, w = 0, h = 0;   // box in frame coordinates
    float score = 0.0f;
    // ESP-DL keypoint order: left eye, left mouth, nose, right eye, right mouth.
    // Never index this directly - pass it to behavior_from_espdl_keypoints().
    float keypoint[10] = {0};
};

// Loads the models. Returns false when the adapter has not been bound to a pinned
// ESP-DL release yet; the firmware then runs preview-only instead of refusing to
// boot, so the camera and panel can be validated on their own.
bool model_init();
bool model_ready();

// The two models bind independently, so they are reported independently. The face
// detector is bound (espressif/human_face_detect); the eye model is not, because it
// needs an .espdl that this repo cannot yet produce - see model_adapter.cpp.
//
// Callers MUST gate PERCLOS and alerting on this. model_eye_closed_prob() returns
// 0.0 while unbound, and 0.0 fed into PERCLOS is indistinguishable from a driver
// whose eyes are open forever: the risk score would sit at zero and the alarm would
// simply never fire.
bool model_eye_ready();

// Largest face in one RGB565 frame. False if no face is found.
bool model_detect_face(const uint8_t *rgb565, int width, int height, FaceDetection *out);

// TEMP bring-up: same detector fed RGB888, to isolate RGB565 handling from content.
bool model_detect_face_rgb888(const uint8_t *rgb888, int width, int height, FaceDetection *out);

// Probability that one eye is closed. `eye`: 0 = right, 1 = left, in DrowsyGuard's
// canonical landmark order. `face_side` is the detected face box's side in pixels.
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
