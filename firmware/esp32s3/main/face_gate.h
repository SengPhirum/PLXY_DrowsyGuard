#pragma once
/*
Which detection to trust, and where to look for the next one.

Split out of model_adapter.cpp for one concrete reason: model_adapter.cpp includes
ESP-DL and so can only be compiled by the Xtensa toolchain, while everything here is
arithmetic on five points and a rectangle. Keeping it free of ESP-IDF headers is what
lets tests/test_face_gate.py compile this exact file on the host and check the
coordinate mapping against a reference implementation - and an off-by-one in a
crop-and-map-back is precisely the kind of bug that survives code review, produces
plausible-looking boxes, and silently shifts every eye crop by a few pixels.

Two jobs:

  1. **Reject implausible detections.** The coarse MSR stage runs at a score
     threshold of 0.10 (model_adapter.cpp explains why it has to), so weak
     candidates do get through, and the old selection rule - largest box wins - has
     no way to tell a face from a headrest that scored 0.11. A five-point landmark
     set has a great deal of structure: the eyes are level-ish, the mouth is below
     them, the nose is between them, and the interocular distance is a fairly fixed
     fraction of face width. Anything violating that is not a face, whatever it
     scored.

  2. **Look where the face was.** Once a face is being tracked, the search is a
     padded square around the last box rather than the whole frame. Two separate
     effects, and it is worth keeping them apart:

     *Resolution.* The detector resizes whatever it is given to a fixed input, so a
     smaller window means more pixels per face. On a 240x240 frame that is 1.36x
     linear for an 80-pixel box, 1.8x at 60, and 2.5x for anything small enough to
     hit the FACE_ROI_MIN_SIDE floor - and nothing at all past about 93 pixels,
     where the padded window would no longer be worth taking. In other words it
     helps most exactly where landmark precision is worst, and stays out of the way
     for a driver sitting close, who already has resolution to spare.

     That matters because every cue downstream - jaw drop, mouth width, both pitch
     channels, and the eye crops themselves - is computed from those five landmark
     positions. Landmark precision is not cosmetic here; it is the input to all of
     it.

     *Exclusion.* Independently of magnification, a face outside the window cannot be
     proposed as a candidate at all. A passenger is ruled out geometrically rather
     than by out-scoring them.
*/

#include "behavior.h"

// A face box, in frame pixels.
struct FaceBox {
    int x = 0, y = 0, w = 0, h = 0;
    bool valid = false;
};

// --- plausibility limits -----------------------------------------------------
// Interocular distance as a fraction of the box width. A frontal face sits near
// 0.45; the range is wide because a profile compresses it and the detector's box is
// not tightly calibrated. The point is to reject 0.02 and 1.5, not to be precise.
constexpr float FACE_EYE_DIST_MIN_FRAC = 0.15f;
constexpr float FACE_EYE_DIST_MAX_FRAC = 0.95f;

// Head tilt past this is either a genuinely extreme pose or a scrambled landmark
// set, and the geometric cues are meaningless either way - they are all measured in
// a frame de-rotated by exactly this angle.
constexpr float FACE_MAX_ROLL_DEG = 45.0f;

// Eye-line to mouth distance over interocular distance. Around 1.05 on a frontal
// face, which is where behavior.py's synthetic landmarks put it.
constexpr float FACE_JAW_MIN = 0.35f;
constexpr float FACE_JAW_MAX = 2.60f;

// How far outside the reported box a landmark may sit, as a fraction of box size.
// Not zero: the detector regresses keypoints and the box separately, so they
// disagree slightly at the edges, and rejecting on that would throw away good
// detections.
constexpr float FACE_KP_MARGIN_FRAC = 0.30f;

// --- region of interest ------------------------------------------------------
// The ROI is the last box grown by this fraction on every side. 0.6 is enough to
// hold a head that moved half its own width between detections, which at
// DETECT_EVERY = 3 is a fast movement.
constexpr float FACE_ROI_PAD = 0.60f;

// Below this, cropping is not worth doing: the ROI already covers most of the frame,
// so the resize gains nothing and the only effect is a copy and a chance to lose the
// face at the edge.
constexpr float FACE_ROI_MAX_FRAC = 0.85f;

// Smallest ROI side, in pixels. A distant face gives a small box, and cropping to
// 40x40 would upsample noise rather than reveal detail.
constexpr int FACE_ROI_MIN_SIDE = 96;

// Square search window for a face last seen at `box`, clamped inside the frame.
// Returns valid = false when the whole frame should be used instead.
FaceBox face_gate_roi(const FaceBox &box, int frame_w, int frame_h);

// Translate a detection made inside `roi` back into frame coordinates, in place.
//
// The whole of the crop-and-map-back is these two additions, and they live here
// rather than in model_adapter.cpp so the round trip can be checked on the host: an
// origin applied once, twice, or not at all all produce boxes that look reasonable
// on a preview, and the only symptom would be every eye crop landing a few pixels
// off - which reads as a weak eye model, not as a coordinate bug.
void face_gate_map_out(const FaceBox &roi, FaceBox *box, Landmarks *lm);

// Intersection over union. 0 when either box is empty, so it is safe to call with
// an unset track.
float face_gate_iou(const FaceBox &a, const FaceBox &b);

// Does this landmark set look like a face inside this box?
//
// `lm` must already be in DrowsyGuard's canonical order - pass ESP-DL's array
// through behavior_from_espdl_keypoints() first. Indexing raw ESP-DL output here
// would make every check test the wrong point, and every check would still pass
// often enough to look like it worked.
bool face_gate_plausible(const Landmarks &lm, const FaceBox &box);

// Which of several candidates is the driver.
//
// `track` is the previously accepted box; pass an invalid one when there is none.
// With a live track the best candidate is the one that overlaps it, not the biggest:
// a passenger leaning forward is a bigger face than the driver, and "largest wins"
// hands them the alarm. Without a track, largest is the right guess - the driver is
// the closest face to a dashboard camera.
//
// Returns the index into `boxes`, or -1 if none is acceptable.
int face_gate_pick(const FaceBox *boxes, const Landmarks *lms, int n, const FaceBox &track);

// Minimum overlap with the track for a candidate to count as the same face.
constexpr float FACE_TRACK_MIN_IOU = 0.15f;

// Whether a failed plausibility check actually excludes a candidate.
//
// On, and the history is worth keeping because it is the whole argument for the reason
// logging below. The first version of this gate rejected **100% of real candidates**
// on hardware - `face 0/20 ... gate dropped 2`, with detection time climbing from
// 39.6 ms to 73.9 ms in the same intervals, because the refinement stage runs per
// candidate and the candidates were therefore certainly real.
//
// The limits were not the fault. The frame is horizontally mirrored - the sensor is
// mounted upside down, so board_camera.h applies vflip without hmirror - which
// reversed the eye pair and put roll at +/-170 degrees instead of 0. Every candidate
// failed `roll-too-steep`, and behind that every vertical cue had its sign inverted.
// behavior_orient_landmarks() fixes the cause; see its comment.
//
// With that fixed, the device reports `gate would drop 0 ok` and the values sit well
// inside these limits:
//
//     measured on device        limit
//     eye_dist/box_w  0.21-0.46    0.15 - 0.95
//     roll            ~0           +/- 45
//     jaw_drop        positive     0.35 - 2.60   (was -1.0 to -2.1)
//     nose_frac       0.31-0.68    0.15 - 1.15
//
// Two things to keep from that episode. A bare rejection count is not a diagnosis -
// "gate dropped 2" is indistinguishable from an empty frame - which is why
// face_gate_check() reports *which* check failed and model_adapter.cpp logs it with
// the numbers. And setting this to 0 is the first thing to try if detection ever dies
// after a change here: it makes the gate advisory without disabling anything else,
// because track preference only reorders candidates and the ROI only decides where to
// look. Neither rejects anything.
#ifndef FACE_GATE_ENFORCE
#define FACE_GATE_ENFORCE 1
#endif

// Why a candidate was not believed. Ordered so the first failing check is reported,
// which is the one worth acting on.
enum class FaceReject : uint8_t {
    None = 0,
    NoLandmarks,      // no usable landmark set or box at all
    OutsideBox,       // landmarks are not near the box they arrived with
    Degenerate,       // eyes coincident, so nothing can be normalised
    EyeDistSmall,
    EyeDistLarge,
    Roll,
    JawSmall,         // mouth at or above the eye line
    JawLarge,
    NoseHigh,         // nose above the eye line
    NoseLow,          // nose at or below the mouth
};

// The full check, reporting the first failure. `measured` receives the geometry the
// decision was made on, so a rejection can be argued with rather than guessed at.
FaceReject face_gate_check(const Landmarks &lm, const FaceBox &box, FaceGeometry *measured);

const char *face_gate_reject_name(FaceReject r);
