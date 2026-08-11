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

// Largest face in one RGB565 frame. False if no face is found.
bool model_detect_face(const uint8_t *rgb565, int width, int height, FaceDetection *out);

// Probability that one eye is closed. `eye`: 0 = right, 1 = left, in DrowsyGuard's
// canonical landmark order.
//
// Preprocessing must match src/drowsyguard/eyestate.py exactly or the desktop
// thresholds do not transfer: crop a square patch around the eye landmark sized
// from the inter-eye distance, resize to 32x32, and scale as (pixel - 127) / 255.
// The published model card for open-closed-eye-0001 is wrong about all three of
// the input scaling, the softmax, and the output order; eyestate.py is the truth.
float model_eye_closed_prob(const uint8_t *rgb565, int width, int height,
                            const Landmarks &lm, int eye);
