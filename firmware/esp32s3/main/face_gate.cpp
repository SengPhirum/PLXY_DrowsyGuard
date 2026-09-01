#include "face_gate.h"

#include <cmath>

FaceBox face_gate_roi(const FaceBox &box, int frame_w, int frame_h) {
    FaceBox roi;
    if (!box.valid || box.w <= 0 || box.h <= 0 || frame_w <= 0 || frame_h <= 0) return roi;

    const int longest = box.w > box.h ? box.w : box.h;
    int side = static_cast<int>(static_cast<float>(longest) * (1.0f + 2.0f * FACE_ROI_PAD));
    if (side < FACE_ROI_MIN_SIDE) side = FACE_ROI_MIN_SIDE;

    const int shortest_frame = frame_w < frame_h ? frame_w : frame_h;
    // Square, so the aspect ratio the detector sees is the same whichever frame size
    // it is fed. A non-square crop would stretch the face relative to the full-frame
    // case and move the landmarks systematically.
    if (side >= static_cast<int>(static_cast<float>(shortest_frame) * FACE_ROI_MAX_FRAC)) {
        return roi;    // not worth cropping; caller uses the whole frame
    }

    // Even side and even origin. RGB565 is two bytes a pixel so any offset is
    // addressable, but keeping both even means the crop lands on the same pixel grid
    // as the full frame, which is one less way for the mapping back to drift.
    side &= ~1;

    const int cx = box.x + box.w / 2;
    const int cy = box.y + box.h / 2;
    int x = cx - side / 2;
    int y = cy - side / 2;
    if (x < 0) x = 0;
    if (y < 0) y = 0;
    if (x + side > frame_w) x = frame_w - side;
    if (y + side > frame_h) y = frame_h - side;
    x &= ~1;
    y &= ~1;

    roi.x = x;
    roi.y = y;
    roi.w = side;
    roi.h = side;
    roi.valid = true;
    return roi;
}

void face_gate_map_out(const FaceBox &roi, FaceBox *box, Landmarks *lm) {
    if (!roi.valid) return;    // detection was run on the whole frame already
    if (box != nullptr && box->valid) {
        box->x += roi.x;
        box->y += roi.y;
    }
    if (lm != nullptr && lm->valid) {
        for (int i = 0; i < 5; ++i) {
            lm->x[i] += static_cast<float>(roi.x);
            lm->y[i] += static_cast<float>(roi.y);
        }
    }
}

float face_gate_iou(const FaceBox &a, const FaceBox &b) {
    if (!a.valid || !b.valid || a.w <= 0 || a.h <= 0 || b.w <= 0 || b.h <= 0) return 0.0f;

    const int x0 = a.x > b.x ? a.x : b.x;
    const int y0 = a.y > b.y ? a.y : b.y;
    const int x1 = (a.x + a.w) < (b.x + b.w) ? (a.x + a.w) : (b.x + b.w);
    const int y1 = (a.y + a.h) < (b.y + b.h) ? (a.y + a.h) : (b.y + b.h);
    if (x1 <= x0 || y1 <= y0) return 0.0f;

    const float inter = static_cast<float>(x1 - x0) * static_cast<float>(y1 - y0);
    const float area_a = static_cast<float>(a.w) * static_cast<float>(a.h);
    const float area_b = static_cast<float>(b.w) * static_cast<float>(b.h);
    const float uni = area_a + area_b - inter;
    return uni > 0.0f ? (inter / uni) : 0.0f;
}

FaceReject face_gate_check(const Landmarks &lm, const FaceBox &box, float score,
                           int frame_w, int frame_h, FaceGeometry *measured) {
    if (measured != nullptr) *measured = FaceGeometry{};
    if (!lm.valid || !box.valid || box.w <= 0 || box.h <= 0) return FaceReject::NoLandmarks;

    // 0. Confidence, first, because it is the cheapest and because a candidate the
    //    detector itself does not believe should not be argued about geometrically.
    //    A NaN score fails this: comparisons against NaN are false either way, so it
    //    is spelled as "not >= threshold" rather than "< threshold".
    if (!(score >= FACE_MIN_SCORE)) return FaceReject::LowScore;

    // 1. Box shape and size. Frame-relative checks are skipped when the caller did
    //    not supply a frame, which is the honest thing to do - inventing a frame size
    //    would silently change what "too small" means.
    const float bw = static_cast<float>(box.w);
    const float bh = static_cast<float>(box.h);
    if (frame_w > 0 && frame_h > 0) {
        const float shortest = static_cast<float>(frame_w < frame_h ? frame_w : frame_h);
        const float side = bw > bh ? bw : bh;
        if (side < FACE_MIN_SIDE_FRAC * shortest) return FaceReject::TooSmall;
        if (side > FACE_MAX_SIDE_FRAC * shortest) return FaceReject::TooLarge;
    }
    const float aspect = bw / bh;
    if (aspect < FACE_ASPECT_MIN || aspect > FACE_ASPECT_MAX) return FaceReject::Aspect;

    // 2. The landmarks have to be near the box they came with. A detection whose
    //    keypoints land somewhere else entirely is two different objects reported as
    //    one, and every measurement taken from it would be nonsense.
    const float mx = bw * FACE_KP_MARGIN_FRAC;
    const float my = bh * FACE_KP_MARGIN_FRAC;
    const float x_lo = static_cast<float>(box.x) - mx;
    const float x_hi = static_cast<float>(box.x + box.w) + mx;
    const float y_lo = static_cast<float>(box.y) - my;
    const float y_hi = static_cast<float>(box.y + box.h) + my;
    for (int i = 0; i < 5; ++i) {
        if (lm.x[i] < x_lo || lm.x[i] > x_hi) return FaceReject::OutsideBox;
        if (lm.y[i] < y_lo || lm.y[i] > y_hi) return FaceReject::OutsideBox;
    }

    // 3. Everything else is exactly the geometry the behaviour cues are built on, so
    //    it is computed by the same function rather than re-derived here. If the
    //    geometry cannot be formed at all - degenerate eye distance - there is
    //    nothing to measure and nothing to trust.
    const FaceGeometry g = behavior_face_geometry(lm);
    if (measured != nullptr) *measured = g;
    if (!g.valid) return FaceReject::Degenerate;

    if (g.eye_dist < FACE_EYE_DIST_MIN_FRAC * bw) return FaceReject::EyeDistSmall;
    if (g.eye_dist > FACE_EYE_DIST_MAX_FRAC * bw) return FaceReject::EyeDistLarge;
    if (std::fabs(g.roll) > FACE_MAX_ROLL_DEG) return FaceReject::Roll;

    // 4. The mouth is below the eye line and the nose is between them. In the
    //    de-rotated frame that is just a sign check plus an ordering, and it is what
    //    catches a landmark set that has been permuted or fitted to something that
    //    is not a face. jaw_drop is signed, so a negative value means the detector
    //    put the mouth above the eyes.
    if (g.jaw_drop < FACE_JAW_MIN) return FaceReject::JawSmall;
    if (g.jaw_drop > FACE_JAW_MAX) return FaceReject::JawLarge;
    // nose_frac is nose height over mouth height in the same frame: 0 puts the nose
    // on the eye line, 1 puts it at the mouth. Real faces sit near 0.55.
    if (g.nose_frac < FACE_NOSE_FRAC_MIN) return FaceReject::NoseHigh;
    if (g.nose_frac > FACE_NOSE_FRAC_MAX) return FaceReject::NoseLow;

    // 5. Two lateral checks the vertical ones cannot make. Both catch the same class
    //    of failure from different directions: five points fitted to something with
    //    face-like vertical structure and no face-like horizontal structure, which is
    //    what a hand, a forearm or a seat edge produces.
    if (std::fabs(g.yaw) > FACE_YAW_MAX) return FaceReject::Yaw;
    if (g.mouth_ratio < FACE_MOUTH_MIN) return FaceReject::MouthNarrow;
    if (g.mouth_ratio > FACE_MOUTH_MAX) return FaceReject::MouthWide;

    return FaceReject::None;
}

const char *face_gate_reject_name(FaceReject r) {
    switch (r) {
        case FaceReject::None: return "ok";
        case FaceReject::NoLandmarks: return "no-landmarks";
        case FaceReject::LowScore: return "score-too-low";
        case FaceReject::TooSmall: return "face-too-small";
        case FaceReject::TooLarge: return "face-too-large";
        case FaceReject::Aspect: return "box-not-head-shaped";
        case FaceReject::OutsideBox: return "landmarks-outside-box";
        case FaceReject::Degenerate: return "degenerate-eye-distance";
        case FaceReject::EyeDistSmall: return "eye-distance-too-small";
        case FaceReject::EyeDistLarge: return "eye-distance-too-large";
        case FaceReject::Roll: return "roll-too-steep";
        case FaceReject::JawSmall: return "mouth-not-below-eyes";
        case FaceReject::JawLarge: return "mouth-too-far-below-eyes";
        case FaceReject::NoseHigh: return "nose-above-eye-line";
        case FaceReject::NoseLow: return "nose-at-or-below-mouth";
        case FaceReject::Yaw: return "nose-outside-eye-pair";
        case FaceReject::MouthNarrow: return "mouth-too-narrow";
        case FaceReject::MouthWide: return "mouth-too-wide";
        case FaceReject::Discontinuous: return "moved-too-far";
        default: return "?";
    }
}

bool face_gate_plausible(const Landmarks &lm, const FaceBox &box, float score,
                         int frame_w, int frame_h) {
    return face_gate_check(lm, box, score, frame_w, frame_h, nullptr) == FaceReject::None;
}

bool face_gate_continuous(const FaceBox &prev, const FaceBox &cand) {
    // Nothing to be discontinuous with. Answering true here is what makes the first
    // detection of a track acceptable without a special case at every call site.
    if (!prev.valid || prev.w <= 0 || prev.h <= 0) return true;
    if (!cand.valid || cand.w <= 0 || cand.h <= 0) return false;

    const float pcx = prev.x + prev.w * 0.5f;
    const float pcy = prev.y + prev.h * 0.5f;
    const float ccx = cand.x + cand.w * 0.5f;
    const float ccy = cand.y + cand.h * 0.5f;

    // Mean of the two sides, so a shrinking box and a growing one are treated the
    // same way. Using only the previous side would make a shrinking face look like it
    // had jumped further than it did.
    const float pside = 0.5f * (prev.w + prev.h);
    const float cside = 0.5f * (cand.w + cand.h);
    const float mean = 0.5f * (pside + cside);
    if (mean <= 0.0f) return false;

    const float dx = ccx - pcx;
    const float dy = ccy - pcy;
    if (std::sqrt(dx * dx + dy * dy) > FACE_JUMP_MAX_FRAC * mean) return false;

    const float ratio = pside > cside ? (pside / cside) : (cside / pside);
    return ratio <= FACE_SCALE_MAX_RATIO;
}

int face_gate_pick(const FaceBox *boxes, const Landmarks *lms, const float *scores,
                   int n, const FaceBox &track, int frame_w, int frame_h) {
    if (boxes == nullptr || lms == nullptr || n <= 0) return -1;

    int best = -1;
    float best_iou = 0.0f;
    int biggest = -1;
    long biggest_area = 0;

    for (int i = 0; i < n; ++i) {
        // A null scores array means the caller has already applied its own
        // confidence policy; 1.0 then passes the check without weakening it for
        // callers that do supply scores.
        const float score = scores != nullptr ? scores[i] : 1.0f;
        // Set FACE_GATE_ENFORCE to 0 to make this advisory - the check still runs
        // and is still logged, it just stops excluding. That is the first thing to
        // try if detection ever dies after a change here; see face_gate.h for why
        // that escape hatch exists.
        if (FACE_GATE_ENFORCE &&
            !face_gate_plausible(lms[i], boxes[i], score, frame_w, frame_h)) {
            continue;
        }

        const long area = static_cast<long>(boxes[i].w) * static_cast<long>(boxes[i].h);
        if (area > biggest_area) {
            biggest_area = area;
            biggest = i;
        }
        const float iou = face_gate_iou(boxes[i], track);
        if (iou > best_iou) {
            best_iou = iou;
            best = i;
        }
    }

    // Continuity first, size only as the opening guess. Handing over to a larger box
    // the moment one appears is how a passenger takes the driver's alarm.
    if (best >= 0 && best_iou >= FACE_TRACK_MIN_IOU) return best;
    return biggest;
}

// ---------------- FaceTrack ----------------

FaceTrackResult FaceTrack::snapshot() const {
    FaceTrackResult r;
    r.present = present();
    r.confirmed = confirmed_;
    r.misses = misses_;
    r.agreed = agreed_;
    r.box = box_;
    r.lm = lm_;
    return r;
}

FaceTrackResult FaceTrack::peek() const { return snapshot(); }

FaceTrackResult FaceTrack::update(bool have, const FaceBox &box, const Landmarks &lm,
                                  float score, int frame_w, int frame_h) {
    const bool was_present = present();

    FaceReject why = FaceReject::None;
    bool accept = false;

    // Whether this candidate could be the same face as the tracked one. Computed
    // unconditionally, because it answers a question about the world; how much
    // weight to give it is a separate decision, taken twice below.
    bool same_face = false;

    if (have) {
        why = face_gate_check(lm, box, score, frame_w, frame_h, nullptr);
        if (why == FaceReject::None) {
            same_face = face_gate_continuous(box_, box);
            // While the track is warm, a candidate somewhere else is a different
            // object and is refused outright - that is what stops a passenger taking
            // over a live track. Once the face has been missing for
            // FACE_REACQUIRE_AFTER attempts the driver may genuinely have moved, so a
            // discontinuous candidate is allowed in; see below for what it costs.
            const bool warm = box_.valid && misses_ < FACE_REACQUIRE_AFTER;
            if (warm && !same_face) {
                why = FaceReject::Discontinuous;
            } else {
                accept = true;
            }
        }
    }

    if (accept) {
        // A candidate that does not line up with what we had starts a NEW track
        // rather than inheriting the old one's confirmation. Presence is re-earned
        // across a discontinuity, never inherited - that is the property that stops
        // a track sliding off the driver and onto whatever is behind them.
        //
        // Note what this deliberately does not do: a face that reappears where it
        // was, after any number of misses inside the hold, DOES continue the track.
        // Ending the track there would drop presence for a detection interval every
        // time the detector blinked, which is exactly the moment - eyes closing, head
        // nodding - that the hold exists to carry.
        if (box_.valid && !same_face) {
            agreed_ = 0;
            confirmed_ = false;
        }
        if (agreed_ < FACE_CONFIRM_DETECTIONS) ++agreed_;
        box_ = box;
        lm_ = lm;
        misses_ = 0;
        if (agreed_ >= FACE_CONFIRM_DETECTIONS) confirmed_ = true;
    } else {
        ++misses_;
        // A pending track has nothing to hold: it was never believed in the first
        // place, so one miss ends it rather than leaving a half-formed track around
        // to be resumed by an unrelated candidate.
        if (!confirmed_) {
            agreed_ = 0;
            box_.valid = false;
            lm_.valid = false;
        } else if (misses_ > FACE_HOLD_DETECTIONS) {
            // The hold has expired. Drop everything, so the next detection is a fresh
            // acquisition and the caller can tell the detector to stop searching where
            // the driver used to be.
            confirmed_ = false;
            agreed_ = 0;
            box_.valid = false;
            lm_.valid = false;
        }
    }

    FaceTrackResult r = snapshot();
    r.fresh = accept;
    r.reject = accept ? FaceReject::None : why;
    r.acquired = r.present && !was_present;
    r.lost = was_present && !r.present;
    return r;
}

void FaceTrack::reset() {
    box_ = FaceBox{};
    lm_ = Landmarks{};
    agreed_ = 0;
    misses_ = 0;
    confirmed_ = false;
}
