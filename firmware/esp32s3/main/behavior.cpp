#include "behavior.h"

#include <algorithm>
#include <cmath>

Landmarks behavior_from_espdl_keypoints(const float *keypoint) {
    Landmarks lm;
    if (keypoint == nullptr) return lm;
    // ESP-DL: 0 left eye, 1 left mouth, 2 nose, 3 right eye, 4 right mouth.
    // Canonical: 0 right eye, 1 left eye, 2 nose, 3 right mouth, 4 left mouth.
    const int src_for_canonical[5] = {3, 0, 2, 4, 1};
    for (int i = 0; i < 5; ++i) {
        const int s = src_for_canonical[i];
        lm.x[i] = keypoint[2 * s];
        lm.y[i] = keypoint[2 * s + 1];
    }
    lm.valid = true;
    return lm;
}

FaceGeometry behavior_face_geometry(const Landmarks &lm) {
    FaceGeometry g;
    if (!lm.valid) return g;

    const float rx = lm.x[0], ry = lm.y[0];   // right eye
    const float lx = lm.x[1], ly = lm.y[1];   // left eye
    const float nx = lm.x[2], ny = lm.y[2];   // nose
    const float mrx = lm.x[3], mry = lm.y[3]; // right mouth
    const float mlx = lm.x[4], mly = lm.y[4]; // left mouth

    const float eye_dist = std::sqrt((lx - rx) * (lx - rx) + (ly - ry) * (ly - ry));
    if (eye_dist < 1e-3f) return g;

    const float roll = std::atan2(ly - ry, lx - rx) * 180.0f / static_cast<float>(M_PI);
    const float eye_mid_x = (rx + lx) * 0.5f, eye_mid_y = (ry + ly) * 0.5f;
    const float mouth_mid_x = (mrx + mlx) * 0.5f, mouth_mid_y = (mry + mly) * 0.5f;

    // De-rotate about the eye midpoint so vertical cues do not move with head tilt.
    const float a = -roll * static_cast<float>(M_PI) / 180.0f;
    const float ca = std::cos(a), sa = std::sin(a);
    auto rot_y = [&](float px, float py) {
        const float dx = px - eye_mid_x, dy = py - eye_mid_y;
        return sa * dx + ca * dy;
    };
    auto rot_x = [&](float px, float py) {
        const float dx = px - eye_mid_x, dy = py - eye_mid_y;
        return ca * dx - sa * dy;
    };

    const float mouth_ry = rot_y(mouth_mid_x, mouth_mid_y);
    const float nose_ry = rot_y(nx, ny);
    const float nose_rx = rot_x(nx, ny);

    g.valid = true;
    g.roll = roll;
    g.jaw_drop = mouth_ry / eye_dist;
    g.nose_frac = (std::fabs(mouth_ry) > 1e-6f) ? (nose_ry / mouth_ry) : 0.0f;
    g.yaw = nose_rx / eye_dist;
    g.eye_dist = eye_dist;
    return g;
}

// ---------------- Baseline ----------------

float Baseline::update(float value) {
    buf_[head_] = value;
    head_ = (head_ + 1) % WINDOW;
    if (count_ < WINDOW) ++count_;
    if (count_ < MIN_SAMPLES) return 0.0f;
    return value - this->value();
}

float Baseline::value() const {
    if (count_ == 0) return 0.0f;
    float tmp[WINDOW];
    for (int i = 0; i < count_; ++i) tmp[i] = buf_[i];
    std::nth_element(tmp, tmp + count_ / 2, tmp + count_);
    return tmp[count_ / 2];
}

void Baseline::reset() {
    head_ = 0;
    count_ = 0;
}

// ---------------- EventRate ----------------

void EventRate::add(float now_s) {
    times_[head_] = now_s;
    head_ = (head_ + 1) % CAPACITY;
    if (count_ < CAPACITY) ++count_;
}

float EventRate::rate(float now_s) {
    int live = 0;
    for (int i = 0; i < count_; ++i) {
        const int idx = (head_ - 1 - i + 2 * CAPACITY) % CAPACITY;
        if (now_s - times_[idx] <= RATE_WINDOW_S) ++live;
    }
    if (live == 0) return 0.0f;
    const float span = std::max(std::min(now_s, RATE_WINDOW_S), 1e-6f);
    return live * 60.0f / span;
}

void EventRate::reset() {
    head_ = 0;
    count_ = 0;
}

// ---------------- Perclos ----------------

Perclos::Perclos(int window, float closed_threshold)
    : window_(std::min(std::max(window, 1), MAX_WINDOW)), closed_threshold_(closed_threshold) {}

float Perclos::update(float p_closed) {
    const uint8_t flag = (p_closed >= closed_threshold_) ? 1 : 0;
    if (count_ == window_) {
        const int tail = (head_ - count_ + 2 * MAX_WINDOW) % MAX_WINDOW;
        closed_ -= flags_[tail];
        --count_;
    }
    flags_[head_] = flag;
    head_ = (head_ + 1) % MAX_WINDOW;
    closed_ += flag;
    ++count_;
    return value();
}

float Perclos::value() const {
    if (count_ == 0) return 0.0f;
    return static_cast<float>(closed_) / static_cast<float>(count_);
}

void Perclos::reset() {
    head_ = 0;
    count_ = 0;
    closed_ = 0;
}

void Perclos::set_window(int window) {
    window_ = std::min(std::max(window, 1), MAX_WINDOW);
    reset();
}

// ---------------- BehaviorAnalyzer ----------------

BehaviorAnalyzer::BehaviorAnalyzer(float closed_threshold, float fps)
    : closed_threshold_(closed_threshold), fps_(std::max(fps, 1.0f)) {}

void BehaviorAnalyzer::reset() {
    jaw_.reset();
    pitch_.reset();
    blinks_.reset();
    long_blinks_.reset();
    yawns_.reset();
    nods_.reset();
    t_ = 0.0f;
    closure_start_ = mouth_start_ = nod_start_ = -1.0f;
    suppress_until_ = -1.0f;
    sneezes_ = 0;
    yawn_fired_ = nod_fired_ = sneeze_fired_ = false;
}

BehaviorState BehaviorAnalyzer::update(float p_closed, const FaceGeometry &geom,
                                       float perclos, float dt_s) {
    t_ += (dt_s > 0.0f) ? dt_s : (1.0f / fps_);
    const float now = t_;
    BehaviorState st;
    st.events = EVENT_NONE;

    const bool closed = p_closed >= closed_threshold_;
    const float jaw_dev = geom.valid ? jaw_.update(geom.jaw_drop) : 0.0f;
    const float pitch_dev = geom.valid ? pitch_.update(geom.nose_frac) : 0.0f;

    const bool mouth_open = geom.valid && jaw_dev >= JAW_OPEN_DELTA;
    const bool head_down = geom.valid && pitch_dev <= -NOD_PITCH_DELTA;

    // Mouth: a sustained opening is a yawn; a brief one is speech.
    if (mouth_open) {
        if (mouth_start_ < 0.0f) {
            mouth_start_ = now;
            yawn_fired_ = false;
        } else if (!yawn_fired_ && (now - mouth_start_) >= YAWN_MIN_S) {
            yawns_.add(now);
            st.events |= EVENT_YAWN;
            yawn_fired_ = true;
        }
    } else {
        mouth_start_ = -1.0f;
        yawn_fired_ = false;
    }

    // Head: a down-and-back excursion is a nod.
    if (head_down) {
        if (nod_start_ < 0.0f) {
            nod_start_ = now;
            nod_fired_ = false;
        }
    } else {
        if (nod_start_ >= 0.0f && !nod_fired_ && (now - nod_start_) <= NOD_MAX_S) {
            nods_.add(now);
            st.events |= EVENT_NOD;
        }
        nod_start_ = -1.0f;
        nod_fired_ = false;
    }

    // Eyes.
    float closure_s = 0.0f;
    if (closed) {
        if (closure_start_ < 0.0f) {
            closure_start_ = now;
            sneeze_fired_ = false;
        }
        closure_s = now - closure_start_;
        // A long closure with a pronounced mouth movement is a sneeze, not a
        // microsleep. Decide during the event so the alert is suppressed, not retracted.
        if (!sneeze_fired_ && closure_s >= BLINK_MAX_S && closure_s <= SNEEZE_MAX_S &&
            jaw_dev >= SNEEZE_JAW_DELTA) {
            ++sneezes_;
            suppress_until_ = now + SNEEZE_MAX_S;
            st.events |= EVENT_SNEEZE;
            sneeze_fired_ = true;
        }
    } else {
        if (closure_start_ >= 0.0f) {
            const float dur = now - closure_start_;
            const bool sneezing = now < suppress_until_;
            if (dur <= BLINK_MAX_S) {
                blinks_.add(now);
                st.events |= EVENT_BLINK;
            } else if (dur >= MICROSLEEP_MIN_S && !sneezing) {
                long_blinks_.add(now);
                st.events |= EVENT_MICROSLEEP;
            } else if (!sneezing) {
                long_blinks_.add(now);
                st.events |= EVENT_LONG_BLINK;
            }
            closure_start_ = -1.0f;
        }
    }

    const bool suppressed = now < suppress_until_;
    const float blink_rate = blinks_.rate(now);
    const float long_rate = long_blinks_.rate(now);
    const float yawn_rate = yawns_.rate(now);
    const float nod_rate = nods_.rate(now);

    auto norm = [](float rate, float full) {
        const float v = rate / full;
        return v < 0.0f ? 0.0f : (v > 1.0f ? 1.0f : v);
    };
    const float pc = perclos < 0.0f ? 0.0f : (perclos > 1.0f ? 1.0f : perclos);

    float score = W_PERCLOS * pc + W_LONG_BLINK * norm(long_rate, LONG_BLINK_RATE_FULL) +
                  W_YAWN * norm(yawn_rate, YAWN_RATE_FULL) + W_NOD * norm(nod_rate, NOD_RATE_FULL);
    if (suppressed) score = std::min(score, W_PERCLOS * pc);

    st.score = std::min(score, 1.0f);
    st.perclos = pc;
    st.eye_closed = p_closed;
    st.mouth_open = mouth_open;
    st.head_down = head_down;
    st.suppressed = suppressed;
    st.baselines_ready = jaw_.ready() && pitch_.ready();
    st.blink_rate = blink_rate;
    st.long_blink_rate = long_rate;
    st.yawn_rate = yawn_rate;
    st.nod_rate = nod_rate;
    st.sneeze_count = sneezes_;
    st.closure_s = closed ? closure_s : 0.0f;
    return st;
}
