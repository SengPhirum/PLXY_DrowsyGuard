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

FaceReject face_gate_check(const Landmarks &lm, const FaceBox &box,
                           FaceGeometry *measured) {
    if (measured != nullptr) *measured = FaceGeometry{};
    if (!lm.valid || !box.valid || box.w <= 0 || box.h <= 0) return FaceReject::NoLandmarks;

    // 1. The landmarks have to be near the box they came with. A detection whose
    //    keypoints land somewhere else entirely is two different objects reported as
    //    one, and every measurement taken from it would be nonsense.
    const float mx = static_cast<float>(box.w) * FACE_KP_MARGIN_FRAC;
    const float my = static_cast<float>(box.h) * FACE_KP_MARGIN_FRAC;
    const float x_lo = static_cast<float>(box.x) - mx;
    const float x_hi = static_cast<float>(box.x + box.w) + mx;
    const float y_lo = static_cast<float>(box.y) - my;
    const float y_hi = static_cast<float>(box.y + box.h) + my;
    for (int i = 0; i < 5; ++i) {
        if (lm.x[i] < x_lo || lm.x[i] > x_hi) return FaceReject::OutsideBox;
        if (lm.y[i] < y_lo || lm.y[i] > y_hi) return FaceReject::OutsideBox;
    }

    // 2. Everything else is exactly the geometry the behaviour cues are built on, so
    //    it is computed by the same function rather than re-derived here. If the
    //    geometry cannot be formed at all - degenerate eye distance - there is
    //    nothing to measure and nothing to trust.
    const FaceGeometry g = behavior_face_geometry(lm);
    if (measured != nullptr) *measured = g;
    if (!g.valid) return FaceReject::Degenerate;

    const float w = static_cast<float>(box.w);
    if (g.eye_dist < FACE_EYE_DIST_MIN_FRAC * w) return FaceReject::EyeDistSmall;
    if (g.eye_dist > FACE_EYE_DIST_MAX_FRAC * w) return FaceReject::EyeDistLarge;
    if (std::fabs(g.roll) > FACE_MAX_ROLL_DEG) return FaceReject::Roll;

    // 3. The mouth is below the eye line and the nose is between them. In the
    //    de-rotated frame that is just a sign check plus an ordering, and it is what
    //    catches a landmark set that has been permuted or fitted to something that
    //    is not a face. jaw_drop is signed, so a negative value means the detector
    //    put the mouth above the eyes.
    if (g.jaw_drop < FACE_JAW_MIN) return FaceReject::JawSmall;
    if (g.jaw_drop > FACE_JAW_MAX) return FaceReject::JawLarge;
    // nose_frac is nose height over mouth height in the same frame: 0 puts the nose
    // on the eye line, 1 puts it at the mouth. Real faces sit near 0.55.
    if (g.nose_frac < 0.15f) return FaceReject::NoseHigh;
    if (g.nose_frac > 1.15f) return FaceReject::NoseLow;

    return FaceReject::None;
}

const char *face_gate_reject_name(FaceReject r) {
    switch (r) {
        case FaceReject::None: return "ok";
        case FaceReject::NoLandmarks: return "no-landmarks";
        case FaceReject::OutsideBox: return "landmarks-outside-box";
        case FaceReject::Degenerate: return "degenerate-eye-distance";
        case FaceReject::EyeDistSmall: return "eye-distance-too-small";
        case FaceReject::EyeDistLarge: return "eye-distance-too-large";
        case FaceReject::Roll: return "roll-too-steep";
        case FaceReject::JawSmall: return "mouth-not-below-eyes";
        case FaceReject::JawLarge: return "mouth-too-far-below-eyes";
        case FaceReject::NoseHigh: return "nose-above-eye-line";
        case FaceReject::NoseLow: return "nose-at-or-below-mouth";
        default: return "?";
    }
}

bool face_gate_plausible(const Landmarks &lm, const FaceBox &box) {
    return face_gate_check(lm, box, nullptr) == FaceReject::None;
}

int face_gate_pick(const FaceBox *boxes, const Landmarks *lms, int n, const FaceBox &track) {
    if (boxes == nullptr || lms == nullptr || n <= 0) return -1;

    int best = -1;
    float best_iou = 0.0f;
    int biggest = -1;
    long biggest_area = 0;

    for (int i = 0; i < n; ++i) {
        // Set FACE_GATE_ENFORCE to 0 to make this advisory - the check still runs
        // and is still logged, it just stops excluding. That is the first thing to
        // try if detection ever dies after a change here; see face_gate.h for why
        // that escape hatch exists.
        if (FACE_GATE_ENFORCE && !face_gate_plausible(lms[i], boxes[i])) continue;

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
