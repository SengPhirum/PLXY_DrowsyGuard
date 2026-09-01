#pragma once
/*
Which detection to trust, where to look for the next one, and when to believe a
driver is really there.

Split out of model_adapter.cpp for one concrete reason: model_adapter.cpp includes
ESP-DL and so can only be compiled by the Xtensa toolchain, while everything here is
arithmetic on five points and a rectangle. Keeping it free of ESP-IDF headers is what
lets tests/test_face_gate.py compile this exact file on the host and check the
coordinate mapping against a reference implementation - and an off-by-one in a
crop-and-map-back is precisely the kind of bug that survives code review, produces
plausible-looking boxes, and silently shifts every eye crop by a few pixels.

Four jobs:

  1. **Reject implausible detections.** The coarse MSR stage runs at a score
     threshold of 0.10 (model_adapter.cpp explains why it has to), so weak
     candidates do get through, and the old selection rule - largest box wins - has
     no way to tell a face from a headrest that scored 0.11. A five-point landmark
     set has a great deal of structure: the eyes are level-ish, the mouth is below
     them, the nose is between them, and the interocular distance is a fairly fixed
     fraction of face width. Anything violating that is not a face, whatever it
     scored. The refined score, the box size and the box aspect ratio are checked
     alongside the landmarks, because a hand held up to the camera is the case that
     satisfies the landmark checks least often but the box checks most often.

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

  3. **Require agreement across time.** A single frame is not evidence. One
     implausibly-lucky detection on a headrest passes every static check often
     enough to matter at five detections a second, so FaceTrack below will not call
     a driver present until FACE_CONFIRM_DETECTIONS detections in a row agree, and
     it refuses a candidate that has jumped further than a head can move between
     detections. Both are cheap, and both reject exactly the failures the static
     checks cannot see.

  4. **Reacquire safely.** The counterpart to (3): a gate that only ever tightens
     eventually locks onto nothing. After FACE_REACQUIRE_AFTER consecutive misses
     the continuity requirement is dropped - the driver may genuinely have moved -
     but the track starts again as *pending* and has to earn confirmation from
     scratch. Presence is therefore never inherited across a discontinuity, which
     is the property that stops a track sliding off a driver and onto a passenger.

src/drowsyguard/facegate.py is the desktop mirror of all four, and
tests/test_facegate_parity.py drives both implementations through the same
sequences and requires the same answers.
*/

#include "behavior.h"

// A face box, in frame pixels.
struct FaceBox {
    int x = 0, y = 0, w = 0, h = 0;
    bool valid = false;
};

// --- confidence --------------------------------------------------------------
// Refined (MNP-stage) score a candidate has to reach. ESP-DL's own default for that
// stage is 0.50 and model_adapter.cpp leaves it there, so this is a second, higher
// bar applied where the geometry is also available: a real face on this camera comes
// back at 1.00 (measured on hardware), while the things that scrape past a 0.50
// refinement cluster just above it. Set with a wide margin all the same, because a
// partly-turned head does score lower and silence is the dangerous way for a
// drowsiness detector to be wrong.
constexpr float FACE_MIN_SCORE = 0.55f;

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

// Nose height between the eye line (0) and the mouth (1), in the de-rotated frame.
// Real faces sit near 0.55; measured on this board, 0.31-0.68.
constexpr float FACE_NOSE_FRAC_MIN = 0.15f;
constexpr float FACE_NOSE_FRAC_MAX = 1.15f;

// Horizontal nose offset from the eye midpoint, over interocular distance. Zero on a
// frontal face and grows with yaw; past this the nose is outside the eye pair
// entirely, which no head pose produces and a landmark set fitted to a hand or a
// seat edge produces readily. Generous - a hard profile is around 0.5 - because
// rejecting a turned head costs recall on the one pose where the eye crops are
// already marginal.
constexpr float FACE_YAW_MAX = 0.75f;

// Mouth corner separation over interocular distance. The mouth is narrower than the
// eye span on essentially every face (0.5-1.0), and a set of five points scattered
// over a hand routinely is not. Both ends are checked because a collapsed mouth and
// a mouth wider than the head are different failures of the same fit.
constexpr float FACE_MOUTH_MIN = 0.25f;
constexpr float FACE_MOUTH_MAX = 1.60f;

// --- box size and shape ------------------------------------------------------
// Box side as a fraction of the frame's shorter side. Below the floor the face is
// too small for the eye crop to contain an eye: at 240 px and 0.10 the box is 24 px,
// so EYE_PATCH_SCALE * 24 = 4.8 px, already under eyestate.py's 8 px floor - the
// closure reading is then upsampled noise whether or not the detection was real.
// Above the ceiling the "face" is most of the frame, which on a dashboard camera is
// a hand or a jacket, not a head at driving distance.
constexpr float FACE_MIN_SIDE_FRAC = 0.10f;
constexpr float FACE_MAX_SIDE_FRAC = 0.95f;

// Box aspect ratio, width over height. Heads are close to square in a detector's
// box; the two things this device sees most often that are not are a hand and the
// vertical edge of a headrest. Deliberately loose, because the box is regressed
// rather than fitted and a partly cropped face at the frame edge is legitimately
// oblong.
constexpr float FACE_ASPECT_MIN = 0.55f;
constexpr float FACE_ASPECT_MAX = 1.80f;

// How far outside the reported box a landmark may sit, as a fraction of box size.
// Not zero: the detector regresses keypoints and the box separately, so they
// disagree slightly at the edges, and rejecting on that would throw away good
// detections.
constexpr float FACE_KP_MARGIN_FRAC = 0.30f;

// --- temporal consistency ----------------------------------------------------
// Consecutive accepted detections before a track counts as a driver. Two, not one:
// the static checks are geometric, and a single frame of landmark noise on a
// non-face can satisfy all of them - but two in a row that also agree with each
// other spatially (see FACE_JUMP_MAX_FRAC) is a different proposition. Two, not
// five, because detection runs every DETECT_EVERY frames and a driver who has just
// sat down should not wait a second to be seen.
constexpr int FACE_CONFIRM_DETECTIONS = 2;

// Detection attempts a confirmed track survives with nothing accepted. At
// DETECT_EVERY = 3 and ~15 fps that is about one second of holding the last box,
// which covers the case this whole subsystem exists for: detectors drop the face
// exactly when the eyes close and the head nods.
constexpr int FACE_HOLD_DETECTIONS = 5;

// Centre displacement between consecutive accepted detections, as a fraction of the
// mean box side. A head at the wheel moves a small fraction of its own width in the
// ~200 ms between detections; a whole box side is already implausible and 1.2 of
// them is another object. This is the check that stops a track stepping off the
// driver onto a passenger without ever being lost.
constexpr float FACE_JUMP_MAX_FRAC = 1.20f;

// Box side ratio between consecutive accepted detections, larger over smaller. A
// face does not double in size in 200 ms. Regressed boxes are noisy, so this is
// loose enough to absorb that and tight enough to reject a substitution.
constexpr float FACE_SCALE_MAX_RATIO = 1.80f;

// Consecutive misses after which continuity stops being required. The driver may
// genuinely have moved while the detector could not see them, so insisting on
// continuity forever would mean never finding them again. The track that results is
// *pending*, not confirmed: it has to earn FACE_CONFIRM_DETECTIONS again before
// anything downstream treats it as a driver.
constexpr int FACE_REACQUIRE_AFTER = 2;

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
    LowScore,         // detector confidence below FACE_MIN_SCORE
    TooSmall,         // box too small for a usable eye crop
    TooLarge,         // box is most of the frame; not a head at driving distance
    Aspect,           // box is not head-shaped
    OutsideBox,       // landmarks are not near the box they arrived with
    Degenerate,       // eyes coincident, so nothing can be normalised
    EyeDistSmall,
    EyeDistLarge,
    Roll,
    JawSmall,         // mouth at or above the eye line
    JawLarge,
    NoseHigh,         // nose above the eye line
    NoseLow,          // nose at or below the mouth
    Yaw,              // nose outside the eye pair horizontally
    MouthNarrow,      // mouth corners collapsed together
    MouthWide,        // mouth wider than the head
    Discontinuous,    // plausible on its own, but not where the tracked face was
};

// The full static check, reporting the first failure. `measured` receives the
// geometry the decision was made on, so a rejection can be argued with rather than
// guessed at.
//
// `lm` must already be in DrowsyGuard's canonical order - pass ESP-DL's array
// through behavior_from_espdl_keypoints() first. Indexing raw ESP-DL output here
// would make every check test the wrong point, and every check would still pass
// often enough to look like it worked.
//
// `score` is the detector's refined confidence. `frame_w`/`frame_h` size the
// frame-relative checks; pass 0 for both to skip those two and check only what does
// not depend on the frame, which is what a caller with no frame context should do
// rather than invent one.
FaceReject face_gate_check(const Landmarks &lm, const FaceBox &box, float score,
                           int frame_w, int frame_h, FaceGeometry *measured);

// Does this candidate look like a face? Convenience wrapper over face_gate_check().
bool face_gate_plausible(const Landmarks &lm, const FaceBox &box, float score,
                         int frame_w, int frame_h);

const char *face_gate_reject_name(FaceReject r);

// Could `cand` be the same face as `prev`, one detection interval later?
//
// Purely kinematic: how far the centre moved relative to the boxes' own size, and
// how much the size changed. Both are scale-free, so it behaves the same for a
// driver sitting close and one sitting back, and it needs no frame rate - the
// caller only ever asks about consecutive detections. An invalid `prev` means there
// is nothing to be discontinuous with, so it answers true.
bool face_gate_continuous(const FaceBox &prev, const FaceBox &cand);

// Which of several candidates is the driver.
//
// `track` is the previously accepted box; pass an invalid one when there is none.
// With a live track the best candidate is the one that overlaps it, not the biggest:
// a passenger leaning forward is a bigger face than the driver, and "largest wins"
// hands them the alarm. Without a track, largest is the right guess - the driver is
// the closest face to a dashboard camera.
//
// `scores` may be null, in which case every candidate is treated as having passed
// the confidence check and the caller owns that decision.
//
// Returns the index into `boxes`, or -1 if none is acceptable.
int face_gate_pick(const FaceBox *boxes, const Landmarks *lms, const float *scores,
                   int n, const FaceBox &track, int frame_w, int frame_h);

// Minimum overlap with the track for a candidate to count as the same face.
constexpr float FACE_TRACK_MIN_IOU = 0.15f;

// What one detection attempt did to the track.
struct FaceTrackResult {
    // A confirmed driver is in front of the camera. False while a track is still
    // pending confirmation and false once the hold has expired - the two states the
    // rest of the pipeline must not treat as "someone is there".
    bool present = false;
    bool fresh = false;      // a detection was accepted on this attempt
    bool confirmed = false;  // the track has passed FACE_CONFIRM_DETECTIONS
    bool acquired = false;   // rising edge of `confirmed`
    bool lost = false;       // falling edge: the track was given up on this attempt
    // Why an offered candidate was refused. None when there was no candidate, or
    // when it was accepted.
    FaceReject reject = FaceReject::None;
    int misses = 0;          // consecutive attempts with nothing accepted
    int agreed = 0;          // consecutive accepted detections
    FaceBox box;             // last accepted box, in frame coordinates
    Landmarks lm;            // its landmarks, canonical order
};

// The driver, across time.
//
// Deliberately separate from the ROI track inside model_adapter.cpp, which answers a
// different question - "where should the next search window go" - and is allowed to
// be wrong, because a bad window costs one detection interval. This one answers "is
// there a driver, and is this box theirs", and being wrong about that is what sends
// an alarm to a headrest or withholds one from a person.
//
// Every decision it makes is arithmetic on boxes and landmarks, so it compiles and
// runs on the host and tests/test_face_gate.py drives it through whole sequences:
// hands, empty frames, occlusion, head movement, and a passenger appearing mid-track.
class FaceTrack {
  public:
    // One detection *attempt*, i.e. one call of the detector, not one frame.
    // `have` is false when the detector returned nothing at all; the box, landmarks
    // and score are then ignored.
    FaceTrackResult update(bool have, const FaceBox &box, const Landmarks &lm,
                           float score, int frame_w, int frame_h);

    // The current state, without offering a detection. Frames between detection
    // attempts still pass, but they are not evidence either way - the hold is
    // counted in attempts, so this never expires it.
    FaceTrackResult peek() const;

    void reset();

    bool present() const { return confirmed_ && misses_ <= FACE_HOLD_DETECTIONS; }
    const FaceBox &box() const { return box_; }
    const Landmarks &landmarks() const { return lm_; }

  private:
    FaceTrackResult snapshot() const;

    FaceBox box_{};
    Landmarks lm_{};
    int agreed_ = 0;
    int misses_ = 0;
    bool confirmed_ = false;
};
