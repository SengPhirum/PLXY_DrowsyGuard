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

// --- baseline-relative deviations ---
constexpr float JAW_OPEN_DELTA = 0.10f;
constexpr float NOD_PITCH_DELTA = 0.06f;
constexpr float SNEEZE_JAW_DELTA = 0.13f;

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
    float roll = 0.0f;       // degrees
    float jaw_drop = 0.0f;   // eye-line to mouth distance / eye distance
    float nose_frac = 0.0f;  // pitch proxy
    float yaw = 0.0f;        // UNVALIDATED, see behavior.py
    float eye_dist = 0.0f;
};

struct BehaviorState {
    float score = 0.0f;
    float perclos = 0.0f;
    float eye_closed = 0.0f;
    bool mouth_open = false;
    bool head_down = false;
    bool suppressed = false;     // sneeze suppression active
    bool baselines_ready = false;
    uint8_t events = EVENT_NONE; // bitmask fired on this frame
    float blink_rate = 0.0f;
    float long_blink_rate = 0.0f;
    float yawn_rate = 0.0f;
    float nod_rate = 0.0f;
    uint16_t sneeze_count = 0;
    float closure_s = 0.0f;
};

// Reorder ESP-DL's keypoint array (10 floats) into canonical order.
Landmarks behavior_from_espdl_keypoints(const float *keypoint);

FaceGeometry behavior_face_geometry(const Landmarks &lm);

// Rolling median baseline over a fixed window, so per-driver anatomy cancels out.
class Baseline {
  public:
    static constexpr int WINDOW = 150;
    static constexpr int MIN_SAMPLES = 25;
    float update(float value);
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
    BehaviorState update(float p_closed, const FaceGeometry &geom, float perclos, float dt_s);
    void reset();

  private:
    float closed_threshold_;
    float fps_;
    Baseline jaw_;
    Baseline pitch_;
    EventRate blinks_, long_blinks_, yawns_, nods_;
    float t_ = 0.0f;
    float closure_start_ = -1.0f;
    float mouth_start_ = -1.0f;
    float nod_start_ = -1.0f;
    float suppress_until_ = -1.0f;
    uint16_t sneezes_ = 0;
    bool yawn_fired_ = false;
    bool nod_fired_ = false;
    bool sneeze_fired_ = false;
};
