#include "behavior.h"

#include <algorithm>
#include <cmath>

// Spelled out rather than taken from <cmath>: the usual constant there is a POSIX
// extension, not standard C++, and libc++ does not define it under strict
// conformance. This file is compiled on the host by tests/test_face_gate.py, so it
// has to build on more than the Xtensa toolchain.
static constexpr float kPi = 3.14159265358979323846f;

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

Landmarks behavior_orient_landmarks(const Landmarks &lm) {
    if (!lm.valid) return lm;
    if (lm.x[0] <= lm.x[1]) return lm;      // already in image order

    Landmarks out = lm;
    // The eyes swap, and the mouth corners have to swap with them or the pair stops
    // describing the same side of the face as the eye it is named after.
    out.x[0] = lm.x[1]; out.y[0] = lm.y[1];
    out.x[1] = lm.x[0]; out.y[1] = lm.y[0];
    out.x[3] = lm.x[4]; out.y[3] = lm.y[4];
    out.x[4] = lm.x[3]; out.y[4] = lm.y[3];
    return out;
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

    const float roll = std::atan2(ly - ry, lx - rx) * 180.0f / kPi;
    const float eye_mid_x = (rx + lx) * 0.5f, eye_mid_y = (ry + ly) * 0.5f;
    const float mouth_mid_x = (mrx + mlx) * 0.5f, mouth_mid_y = (mry + mly) * 0.5f;

    // De-rotate about the eye midpoint so vertical cues do not move with head tilt.
    const float a = -roll * kPi / 180.0f;
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

    // Mouth corner separation, measured in the de-rotated frame so head tilt does
    // not shorten it. This is the second half of the mouth signal the five
    // landmarks actually carry: opening the jaw wide purses the lips and pulls the
    // corners inward, so this falls while jaw_drop rises. Using only jaw_drop threw
    // that away - see MOUTH_NARROW_W.
    const float mouth_dx = rot_x(mlx, mly) - rot_x(mrx, mry);
    const float mouth_dy = rot_y(mlx, mly) - rot_y(mrx, mry);
    const float mouth_w = std::sqrt(mouth_dx * mouth_dx + mouth_dy * mouth_dy);

    g.valid = true;
    g.roll = roll;
    g.jaw_drop = mouth_ry / eye_dist;
    g.nose_frac = (std::fabs(mouth_ry) > 1e-6f) ? (nose_ry / mouth_ry) : 0.0f;
    // The same pitch cue normalised by eye distance instead of by the eye-to-mouth
    // span. eye_dist is the one length on the face the jaw cannot change, so unlike
    // nose_frac this does not move when the mouth opens. Requiring both to agree is
    // what stops speech, chewing and short yawns from being reported as head nods.
    g.nose_norm = nose_ry / eye_dist;
    g.mouth_ratio = mouth_w / eye_dist;
    g.yaw = nose_rx / eye_dist;
    g.eye_dist = eye_dist;
    return g;
}

// ---------------- EyeGate ----------------

EyeGate::EyeGate(float threshold, float hysteresis)
    : enter_(threshold + hysteresis), exit_(threshold - hysteresis) {}

bool EyeGate::update(float p_closed) {
    hist_[2] = hist_[1];
    hist_[1] = hist_[0];
    hist_[0] = p_closed;
    if (n_ < 3) ++n_;

    if (n_ == 1) {
        med_ = hist_[0];
    } else if (n_ == 2) {
        med_ = std::min(hist_[0], hist_[1]);   // conservative until there are three
    } else {
        // Median of three by two comparisons' worth of min/max - no sort, no branches
        // worth worrying about, and it runs on every frame of the capture loop.
        const float a = hist_[0], b = hist_[1], c = hist_[2];
        med_ = std::max(std::min(a, b), std::min(std::max(a, b), c));
    }

    if (closed_) {
        if (med_ < exit_) closed_ = false;
    } else {
        if (med_ >= enter_) closed_ = true;
    }
    return closed_;
}

void EyeGate::reset() {
    hist_[0] = hist_[1] = hist_[2] = 0.0f;
    n_ = 0;
    med_ = 0.0f;
    closed_ = false;
}

// ---------------- Baseline ----------------

float Baseline::update(float value) {
    buf_[head_] = value;
    head_ = (head_ + 1) % WINDOW;
    if (count_ < WINDOW) ++count_;
    if (count_ < MIN_SAMPLES) return 0.0f;
    return value - this->value();
}

float Baseline::deviation(float value) const {
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
//
// Left on a plain threshold on purpose, while BehaviorAnalyzer's closure timing
// runs through EyeGate. PERCLOS is a fraction over a window - an averaging
// operation - so a single flipped frame moves it by 1/window and nothing more.
// Filtering the input would add phase lag and would shift the numbers RISK_TRIGGER
// was tuned against, to fix a problem this estimator does not have.

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
    window = std::min(std::max(window, 1), MAX_WINDOW);
    if (window == window_) return;

    // Keep the most recent samples rather than clearing, which is what
    // PerclosTracker.resize() does on the desktop and what
    // tests/test_eyestate.py asserts. It matters here because main.cpp resizes this
    // window whenever the measured frame rate moves: clearing would blank PERCLOS
    // each time, and a PERCLOS of zero is indistinguishable from eyes wide open -
    // the one way this estimator must never be wrong.
    const int keep = std::min(count_, window);
    uint8_t tmp[MAX_WINDOW];
    for (int i = 0; i < keep; ++i) {
        const int idx = (head_ - keep + i + 2 * MAX_WINDOW) % MAX_WINDOW;
        tmp[i] = flags_[idx];
    }
    int c = 0;
    for (int i = 0; i < keep; ++i) {
        flags_[i] = tmp[i];
        c += tmp[i];
    }
    head_ = keep % MAX_WINDOW;
    count_ = keep;
    closed_ = c;
    window_ = window;
}

// ---------------- BehaviorAnalyzer ----------------

BehaviorAnalyzer::BehaviorAnalyzer(float closed_threshold, float fps)
    : fps_(std::max(fps, 1.0f)), gate_(closed_threshold, CLOSED_HYSTERESIS) {}

void BehaviorAnalyzer::reset() {
    gate_.reset();
    jaw_.reset();
    pitch_.reset();
    width_.reset();
    nose_.reset();
    blinks_.reset();
    long_blinks_.reset();
    yawns_.reset();
    nods_.reset();
    t_ = 0.0f;
    closure_start_ = closure_lapse_ = -1.0f;
    mouth_start_ = mouth_lapse_ = -1.0f;
    mouth_peak_ = 0.0f;
    nod_start_ = nod_lapse_ = -1.0f;
    nod_peak_ = 0.0f;
    suppress_until_ = -1.0f;
    sneezes_ = 0;
    yawn_fired_ = micro_fired_ = sneeze_fired_ = false;
}

BehaviorState BehaviorAnalyzer::update(float p_closed, const FaceGeometry &geom,
                                       float perclos, float dt_s, bool fresh) {
    t_ += (dt_s > 0.0f) ? dt_s : (1.0f / fps_);
    const float now = t_;
    BehaviorState st;
    st.events = EVENT_NONE;

    const bool closed = gate_.update(p_closed);
    // Held landmarks are byte-identical to the last real ones, so they are evidence
    // about a moment that has passed. They must not enter the baselines and must not
    // advance the geometric state machines; the eye path still runs, because the eye
    // crop is taken from the current frame.
    const bool use_geom = geom.valid && fresh;

    float open_index = 0.0f, pitch_dev = 0.0f, nose_dev = 0.0f;
    if (use_geom) {
        const float jaw_dev = jaw_.update(geom.jaw_drop);
        const float width_dev = width_.update(geom.mouth_ratio);
        pitch_dev = pitch_.update(geom.nose_frac);
        nose_dev = nose_.update(geom.nose_norm);
        // Both halves of the mouth signal, in one number. The corners drop
        // (jaw_dev up) and close in (width_dev down) as the mouth opens, so the
        // two terms add rather than fight.
        open_index = jaw_dev - MOUTH_NARROW_W * width_dev;
    }

    const bool mouth_open = use_geom && open_index >= JAW_OPEN_DELTA;
    // Two channels, both required. nose_frac alone cannot tell a head that dropped
    // from a mouth that opened, because opening the mouth grows its denominator.
    const bool head_down = use_geom && pitch_dev <= -NOD_PITCH_DELTA &&
                           nose_dev <= -NOD_NORM_DELTA;

    if (use_geom) {
        // --- mouth: a sustained, pronounced opening is a yawn ---
        if (mouth_open) {
            mouth_lapse_ = -1.0f;
            if (mouth_start_ < 0.0f) {
                mouth_start_ = now;
                mouth_peak_ = 0.0f;
                yawn_fired_ = false;
            }
            if (open_index > mouth_peak_) mouth_peak_ = open_index;
            // Duration says "this is not speech"; the peak says "this is not a
            // slow drift or a baseline that has not settled". Re-checked every
            // frame, so a yawn that widens late still fires.
            if (!yawn_fired_ && (now - mouth_start_) >= YAWN_MIN_S &&
                mouth_peak_ >= YAWN_PEAK_DELTA) {
                yawns_.add(now);
                st.events |= EVENT_YAWN;
                yawn_fired_ = true;
            }
        } else if (mouth_start_ >= 0.0f) {
            if (mouth_lapse_ < 0.0f) mouth_lapse_ = now;
            if ((now - mouth_lapse_) >= CUE_GAP_S) {
                mouth_start_ = mouth_lapse_ = -1.0f;
                mouth_peak_ = 0.0f;
                yawn_fired_ = false;
            }
        }

        // --- head: a down-and-back excursion is a nod ---
        if (head_down) {
            nod_lapse_ = -1.0f;
            if (nod_start_ < 0.0f) {
                nod_start_ = now;
                nod_peak_ = 0.0f;
            }
            if (-pitch_dev > nod_peak_) nod_peak_ = -pitch_dev;
        } else if (nod_start_ >= 0.0f) {
            if (nod_lapse_ < 0.0f) nod_lapse_ = now;
            if ((now - nod_lapse_) >= CUE_GAP_S) {
                // Measured to where the head came back up, not to where the
                // tolerance expired.
                const float dur = nod_lapse_ - nod_start_;
                if (dur >= NOD_MIN_S && dur <= NOD_MAX_S && nod_peak_ >= NOD_PEAK_DELTA) {
                    nods_.add(now);
                    st.events |= EVENT_NOD;
                }
                nod_start_ = nod_lapse_ = -1.0f;
                nod_peak_ = 0.0f;
            }
        }
    }

    // --- eyes ---
    float closure_s = 0.0f;
    if (closed) {
        closure_lapse_ = -1.0f;
        if (closure_start_ < 0.0f) {
            closure_start_ = now;
            sneeze_fired_ = false;
            micro_fired_ = false;
        }
        closure_s = now - closure_start_;

        // A long closure with a pronounced mouth movement is a sneeze, not a
        // microsleep. Decided during the event so the alert is suppressed, not
        // retracted.
        if (!sneeze_fired_ && closure_s >= BLINK_MAX_S && closure_s <= SNEEZE_MAX_S &&
            open_index >= SNEEZE_JAW_DELTA) {
            ++sneezes_;
            suppress_until_ = now + SNEEZE_MAX_S;
            st.events |= EVENT_SNEEZE;
            sneeze_fired_ = true;
        }

        // Microsleep, announced WHILE THE EYES ARE STILL SHUT.
        //
        // This used to wait for the driver to open their eyes again, because the
        // closure was only classified on release. A driver whose eyes stay shut for
        // five seconds got five seconds of silence - the alarm was gated on the
        // event it exists to interrupt. Firing at the threshold instead means the
        // warning arrives MICROSLEEP_MIN_S into the closure and not one frame later.
        //
        // The wait is longer only when the mouth says this could still be a sneeze:
        // a sneeze resolves inside SNEEZE_MAX_S, so surviving that long is itself
        // the proof that it was not one.
        const float need = (open_index >= SNEEZE_JAW_DELTA) ? SNEEZE_MAX_S : MICROSLEEP_MIN_S;
        if (!micro_fired_ && closure_s >= need && now >= suppress_until_) {
            long_blinks_.add(now);
            st.events |= EVENT_MICROSLEEP;
            micro_fired_ = true;
        }
    } else if (closure_start_ >= 0.0f) {
        if (closure_lapse_ < 0.0f) closure_lapse_ = now;
        if ((now - closure_lapse_) >= CUE_GAP_S) {
            // The closure is genuinely over. Its duration ends where the eyes
            // reopened, not where the tolerance expired - otherwise the tolerance
            // itself would promote a 0.9 s long blink into a 1.1 s microsleep.
            const float dur = closure_lapse_ - closure_start_;
            const bool sneezing = closure_lapse_ < suppress_until_;
            if (dur <= BLINK_MAX_S) {
                blinks_.add(now);
                st.events |= EVENT_BLINK;
            } else if (!micro_fired_ && !sneezing) {
                // Between a blink and a microsleep, or a microsleep that was
                // suppressed while it was happening. Counts toward the long-blink
                // rate either way; micro_fired_ stops it being counted twice.
                long_blinks_.add(now);
                st.events |= EVENT_LONG_BLINK;
            }
            closure_start_ = closure_lapse_ = -1.0f;
        } else {
            closure_s = closure_lapse_ - closure_start_;   // frozen through the gap
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
    st.eye_smooth = gate_.smoothed();
    st.closed = closed;
    st.mouth_open = mouth_open;
    st.head_down = head_down;
    st.suppressed = suppressed;
    st.stale = geom.valid && !fresh;
    st.baselines_ready = jaw_.ready() && pitch_.ready() && width_.ready() && nose_.ready();
    st.open_index = open_index;
    st.pitch_dev = pitch_dev;
    st.blink_rate = blink_rate;
    st.long_blink_rate = long_rate;
    st.yawn_rate = yawn_rate;
    st.nod_rate = nod_rate;
    st.sneeze_count = sneezes_;
    st.closure_s = closure_s;
    return st;
}
