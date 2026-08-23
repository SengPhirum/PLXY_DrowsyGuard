#pragma once

// Device-side mirror of src/drowsyguard/behavior.py.
//
// These constants MUST stay numerically identical to the Python module, because the
// desktop dashboard is how they get tuned. tests/test_firmware_parity.py parses this
// header and fails if the two drift apart.
//
// Landmark order here is DrowsyGuard's canonical order, which matches YuNet:
//   0 right eye, 1 left eye, 2 nose, 3 right mouth, 4 left mouth
// ESP-DL's human_face_detect uses a DIFFERENT order:
//   0 left eye, 1 left mouth, 2 nose, 3 right eye, 4 right mouth
// Use behavior_from_espdl_keypoints() to reorder; do not index ESP-DL output directly.

#include <cstdint>

// --- durations in seconds (mirrored from behavior.py) ---
constexpr float BLINK_MAX_S = 0.40f;
constexpr float MICROSLEEP_MIN_S = 1.00f;
constexpr float YAWN_MIN_S = 1.20f;
constexpr float SNEEZE_MAX_S = 1.20f;
constexpr float NOD_MAX_S = 1.50f;

// Shortest excursion that can be a head nod. Below this it is landmark jitter:
// the pitch proxy is a ratio of two five-point distances, so a single frame of
// keypoint noise can carry it past NOD_PITCH_DELTA and back. Without a floor,
// six such frames a minute drive the nod cue to full scale on their own.
constexpr float NOD_MIN_S = 0.30f;

// --- baseline-relative deviations ---
constexpr float JAW_OPEN_DELTA = 0.10f;
constexpr float NOD_PITCH_DELTA = 0.06f;
constexpr float SNEEZE_JAW_DELTA = 0.13f;

// Peak magnitudes an event has to reach, as opposed to the threshold that starts
// it. Two thresholds rather than one is what separates "the cue is present" from
// "the cue is pronounced enough to be the thing we think it is": entering at the
// lower number keeps the event's measured *duration* honest, while the peak gate
// is what rejects a slow drift that never becomes a real yawn or nod.
constexpr float YAWN_PEAK_DELTA = 0.16f;
constexpr float NOD_PEAK_DELTA = 0.10f;

// Second, mouth-independent pitch channel. nose_frac divides by the eye-to-mouth
// distance, so opening the mouth lowers it even when the head has not moved at all:
// a 0.25 jaw drop registers as a 0.11 pitch drop, comfortably past NOD_PITCH_DELTA.
//
// Measured against the previous version, with the head held perfectly still: a mouth
// open for 0.5 s fired a nod, 1.0 s fired a nod, and 1.4 s fired a yawn AND a nod -
// double-counted in the fused score, and announced as the nod, because main.cpp
// tests that bit first. Only openings past 1.5 s escaped, and only because they
// outlasted NOD_MAX_S. In other words ordinary speech and chewing were being
// reported as head nods.
//
// Requiring nose_norm (nose distance over *eye* distance, which the jaw cannot
// change) to agree is what separates a real head drop from an open mouth.
constexpr float NOD_NORM_DELTA = 0.03f;

// Hysteresis half-width on the eye-closure decision. The gate closes at
// threshold + this and opens at threshold - this, so a probability sitting on the
// threshold cannot emit a burst of one-frame blinks. See EyeGate.
constexpr float CLOSED_HYSTERESIS = 0.10f;

// How long a cue may lapse without the event being treated as over. One number for
// all three cues, because it is one phenomenon: a brief dropout in the middle of an
// event that is still happening.
//
// This is the most valuable number in this header, and it is here because of an
// arithmetic fact. A microsleep is a closure of at least MICROSLEEP_MIN_S = 1.0 s,
// which at 15 fps is 15 frames, and one frame reading "open" in the middle used to
// split it into two 0.5 s long-blinks. So the most dangerous event was the one most
// easily destroyed by a single noisy frame - and the eye model is IR-trained with an
// AUC of 0.62 on visible light, so noisy frames are its normal behaviour, not an
// anomaly.
//
// The same argument applies to a yawn (YAWN_MIN_S is 1.2 s of *continuous* opening,
// and one noisy frame reset the timer and lost the yawn) and to a nod, where a
// dropout is worse than a miss: it splits one excursion into two, and two counted
// nods are twice the contribution to the fused risk score of the one that happened.
constexpr float CUE_GAP_S = 0.20f;

// Weight of mouth *narrowing* in the opening index, relative to jaw drop.
//
// Five landmarks give the mouth corners and nothing else, so there is no lip gap to
// measure and no mouth aspect ratio to compute. What the corners do give is two
// signals, not one: as the jaw drops they move down (jaw_drop rises) and they also
// move inward (mouth_ratio falls), because the lips purse as the mouth opens wide.
// The old code used only the first. Combining them roughly doubles the separation
// between a wide yawn and ordinary speech at the same jaw drop.
constexpr float MOUTH_NARROW_W = 0.60f;

constexpr float RATE_WINDOW_S = 60.0f;

// --- fusion weights ---
constexpr float W_PERCLOS = 0.55f;
constexpr float W_LONG_BLINK = 0.20f;
constexpr float W_YAWN = 0.15f;
constexpr float W_NOD = 0.10f;

// --- rates (events/min) at which a cue counts as fully expressed ---
constexpr float YAWN_RATE_FULL = 4.0f;
constexpr float NOD_RATE_FULL = 6.0f;
constexpr float LONG_BLINK_RATE_FULL = 12.0f;

enum BehaviorEvent : uint8_t {
    EVENT_NONE = 0,
    EVENT_BLINK = 1 << 0,
    EVENT_LONG_BLINK = 1 << 1,
    EVENT_MICROSLEEP = 1 << 2,
    EVENT_YAWN = 1 << 3,
    EVENT_NOD = 1 << 4,
    EVENT_SNEEZE = 1 << 5,
};

struct Landmarks {
    // Canonical order; see the note above.
    float x[5];
    float y[5];
    bool valid = false;
};

struct FaceGeometry {
    bool valid = false;
    float roll = 0.0f;         // degrees
    float jaw_drop = 0.0f;     // eye-line to mouth distance / eye distance
    float nose_frac = 0.0f;    // pitch proxy, relative to the eye-mouth span
    float nose_norm = 0.0f;    // pitch proxy, relative to eye distance only
    float mouth_ratio = 0.0f;  // mouth corner separation / eye distance
    float yaw = 0.0f;          // UNVALIDATED, see behavior.py
    float eye_dist = 0.0f;
};

struct BehaviorState {
    float score = 0.0f;
    float perclos = 0.0f;
    float eye_closed = 0.0f;     // probability as handed in, unfiltered
    float eye_smooth = 0.0f;     // after the median filter EyeGate applies
    bool closed = false;         // the debounced decision the events are built on
    bool mouth_open = false;
    bool head_down = false;
    bool suppressed = false;     // sneeze suppression active
    bool baselines_ready = false;
    bool stale = false;          // geometry was held, not freshly detected
    uint8_t events = EVENT_NONE; // bitmask fired on this frame
    float open_index = 0.0f;     // jaw drop + narrowing, baseline-relative
    float pitch_dev = 0.0f;      // nose_frac deviation; negative is head-down
    float blink_rate = 0.0f;
    float long_blink_rate = 0.0f;
    float yawn_rate = 0.0f;
    float nod_rate = 0.0f;
    uint16_t sneeze_count = 0;
    float closure_s = 0.0f;
};

// Reorder ESP-DL's keypoint array (10 floats) into canonical order.
Landmarks behavior_from_espdl_keypoints(const float *keypoint);

// Put the eye pair in image order, swapping the mouth corners with them.
//
// Canonical order expects the right eye at the smaller x, which holds for a frame the
// way a camera sees it and fails for a mirrored one. This board mirrors: the sensor is
// mounted upside down, so board_camera.h applies vflip without hmirror, and a vertical
// flip of an upside-down image is an upright image that is horizontally mirrored.
//
// Getting this wrong is not a cosmetic problem. A reversed eye pair puts roll at 180
// degrees instead of 0, behavior_face_geometry() then de-rotates by 180 degrees, and
// every vertical cue changes sign - jaw_drop reads -1.2 instead of +1.2 and *falls* as
// the mouth opens, so mouth_open, which wants a rise, can never fire. Measured on
// hardware before this existed: roll -170.9, jaw -1.64, on detections scoring 1.00.
//
// Ordering by position rather than by label is what makes it robust: it is right for a
// mirrored frame, an unmirrored one, and any future change to the flip settings. The
// cost is that index 0 means "the image-left eye" rather than the driver's anatomical
// right eye, and nothing downstream cares - jaw drop, mouth width, both pitch channels
// and roll are all symmetric, and the eye crops are two crops either way.
Landmarks behavior_orient_landmarks(const Landmarks &lm);

FaceGeometry behavior_face_geometry(const Landmarks &lm);

// Turns a noisy per-frame closure probability into a stable open/closed decision.
//
// Two mechanisms with two different jobs:
//
//   median-of-3   removes an isolated frame that flipped. A median is used rather
//                 than an EMA on purpose: an EMA smooths the *edges* of a closure
//                 and so distorts its duration, and duration is precisely what
//                 MICROSLEEP_MIN_S and BLINK_MAX_S measure. A 3-tap median costs
//                 one frame of lag and moves neither edge by more than that.
//   hysteresis    stops a probability parked on the threshold from emitting a burst
//                 of one-frame blinks, each of which would be counted as a real
//                 blink in the rate window.
//
// Deliberately NOT applied to Perclos below. PERCLOS is a fraction over a window,
// which is already an averaging operation and is inherently immune to single-frame
// flips; filtering its input would only add phase lag and would move the numbers
// that RISK_TRIGGER was tuned against.
class EyeGate {
  public:
    explicit EyeGate(float threshold = 0.5f, float hysteresis = CLOSED_HYSTERESIS);
    bool update(float p_closed);
    bool closed() const { return closed_; }
    float smoothed() const { return med_; }
    void reset();

  private:
    float enter_;
    float exit_;
    float hist_[3] = {0.0f, 0.0f, 0.0f};
    int n_ = 0;
    float med_ = 0.0f;
    bool closed_ = false;
};

// Rolling median baseline over a fixed window, so per-driver anatomy cancels out.
class Baseline {
  public:
    static constexpr int WINDOW = 150;
    static constexpr int MIN_SAMPLES = 25;
    float update(float value);
    // Deviation from the baseline without contributing a sample to it. Mirrors
    // Baseline.deviation() in behavior.py.
    //
    // Note what is deliberately NOT done with it: the baseline is not frozen while
    // an event is in progress. A yawn is at most ~3 s of a 10 s window, and the
    // baseline is a *median*, which is exactly why - 30% of the window shifting
    // barely moves the 50th percentile. Freezing would buy nothing and would
    // deadlock a cue that never falls back below its own threshold.
    float deviation(float value) const;
    float value() const;
    bool ready() const { return count_ >= MIN_SAMPLES; }
    void reset();

  private:
    float buf_[WINDOW] = {0};
    int head_ = 0;
    int count_ = 0;
};

// Counts events inside a rolling time window and reports events per minute.
class EventRate {
  public:
    static constexpr int CAPACITY = 64;
    void add(float now_s);
    float rate(float now_s);
    void reset();

  private:
    float times_[CAPACITY] = {0};
    int head_ = 0;
    int count_ = 0;
};

// PERCLOS: fraction of the recent window in which the eyes were closed.
class Perclos {
  public:
    static constexpr int MAX_WINDOW = 300;
    explicit Perclos(int window = 90, float closed_threshold = 0.5f);
    float update(float p_closed);
    float value() const;
    void reset();
    void set_window(int window);
    int window() const { return window_; }

  private:
    int window_;
    float closed_threshold_;
    uint8_t flags_[MAX_WINDOW] = {0};
    int head_ = 0;
    int count_ = 0;
    int closed_ = 0;
};

class BehaviorAnalyzer {
  public:
    explicit BehaviorAnalyzer(float closed_threshold = 0.5f, float fps = 15.0f);

    // `fresh` is false when the caller is re-using the previous frame's landmarks
    // because the detector missed. Time still advances and the eye path still runs -
    // the eye crop comes from the current frame - but the geometric cues do not,
    // because held landmarks are byte-identical to the last real ones. Feeding them
    // in would push up to a second of duplicate samples into a 10 s baseline window
    // and would keep the mouth and nod timers running on evidence that no longer
    // exists. A head that pitches far enough down to lose the detector is exactly
    // when that used to happen.
    BehaviorState update(float p_closed, const FaceGeometry &geom, float perclos,
                         float dt_s, bool fresh = true);
    void reset();

  private:
    float fps_;
    EyeGate gate_;
    Baseline jaw_;
    Baseline pitch_;
    Baseline width_;
    Baseline nose_;
    EventRate blinks_, long_blinks_, yawns_, nods_;
    float t_ = 0.0f;
    float closure_start_ = -1.0f;
    float closure_lapse_ = -1.0f;   // when the eyes last reopened mid-closure
    float mouth_start_ = -1.0f;
    float mouth_lapse_ = -1.0f;
    float mouth_peak_ = 0.0f;
    float nod_start_ = -1.0f;
    float nod_lapse_ = -1.0f;
    float nod_peak_ = 0.0f;
    float suppress_until_ = -1.0f;
    uint16_t sneezes_ = 0;
    bool yawn_fired_ = false;
    bool micro_fired_ = false;
    bool sneeze_fired_ = false;
};
