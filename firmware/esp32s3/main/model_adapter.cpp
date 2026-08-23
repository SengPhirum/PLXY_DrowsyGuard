#include "model_adapter.h"

#include <cmath>
#include <cstring>
#include <new>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"

#include "dl_detect_define.hpp"
#include "dl_image_define.hpp"
#include "board_camera.h"
#include "eye_model.h"
#include "face_gate.h"
#include "human_face_detect.hpp"

static const char *TAG = "model";
static bool s_ready = false;
static HumanFaceDetect *s_face = nullptr;

// The face currently being followed, in frame coordinates, and the crop staged for
// the detector. The crop buffer is PSRAM because it is up to 115 kB and internal RAM
// is the scarce pool on this board - the models, the HTTP stacks and the Wi-Fi
// buffers all want it.
static FaceBox s_track;
static uint8_t *s_crop = nullptr;
static size_t s_crop_cap = 0;
static ModelDetectStats s_stats;

// At most this many ESP-DL candidates are considered per frame. The detector returns
// far fewer in practice; the cap exists so the arrays below are fixed-size and the
// function has no allocation on the hot path.
static constexpr int MAX_CANDIDATES = 8;

// Stage thresholds. MSR is the coarse proposal stage, MNP the refinement; ESP-DL
// ships both at 0.5. See model_init() for why MSR is lowered and MNP is not.
// 0.30 was still too strict on this camera - measured empirically: 0.50 and 0.30
// both yield nothing, 0.10 yields a detection the MNP stage then scores at 1.00.
//
// A gate this loose has to be paid for somewhere, and it is: face_gate_plausible()
// checks that the five landmarks actually describe a face before the detection is
// used for anything. Lowering a score threshold without that is how a headrest ends
// up driving PERCLOS.
static constexpr float MSR_SCORE_THR = 0.10f;
static constexpr float MNP_SCORE_THR = 0.50f;

/*
Stage 3 of docs/HARDWARE_SETUP.md, half bound.

Face + 5 landmarks: espressif/human_face_detect 0.3.0 (msr_s8_v1 -> mnp_s8_v1),
weights in flash rodata. That is the component's own default and partitions.csv
already gives the app 6 MB, so no menuconfig change is needed.

Eye open/closed: bound, but NOT through ESP-DL. ESP-DL loads `.espdl`, which needs
esp-ppq to produce - and esp-ppq is not on PyPI at all, so the `pip install esp-ppq`
that scripts/quantize_espdl.py suggested simply fails. Quantizing would also need a
calibration set of real eye crops, and this repo has none.

Neither is worth solving for this network. It is four convolutions and 11,250
parameters, so eye_model.cpp runs it directly in float32 from weights exported by
scripts/export_eye_model.py, and tests/test_eye_model_parity.py holds that
implementation to the ONNX graph on the host to within 1e-5. Skipping quantization
also removes quantization error from the accuracy question, which matters here.

Still true and not fixed by binding it: gap 6 in PROJECT_STATE.md. This model is
IR-trained and scores AUC 0.62 on visible-light eye crops against its claimed
95.84% in-domain. PERCLOS now moves, which it never did before; do not read that as
the detector being accurate in daylight.
*/

bool model_init() {
    if (s_ready) return true;

    const size_t heap_before = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    const size_t psram_before = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);

    // lazy_load = false on purpose: a model that cannot be mapped should fail here
    // at boot, on the line after the camera and panel, rather than on the first
    // frame of the capture loop where it looks like a camera fault.
    s_face = new (std::nothrow) HumanFaceDetect(HumanFaceDetect::MSRMNP_S8_V1, false);
    if (s_face == nullptr) {
        ESP_LOGE(TAG, "human_face_detect allocation failed; running preview-only");
        return false;
    }
    s_ready = true;

    // Both stages default to 0.5, and at that setting this camera never produced a
    // single detection - not even on a frame desktop YuNet scores at 0.861. The
    // coarse MSR stage is what rejects it; once a candidate reaches MNP the refined
    // score comes back at 1.00. So relax the candidate gate and leave the final gate
    // where it was: recall improves and nothing weak survives to be reported.
    s_face->set_score_thr(MSR_SCORE_THR, 0);
    s_face->set_score_thr(MNP_SCORE_THR, 1);

    // Room for the largest crop the frame can produce. Failing to get it is not
    // fatal: face_gate_roi()'s result is then discarded and every detection is a
    // full-frame one, which is exactly the previous behaviour.
    s_crop_cap = static_cast<size_t>(CAM_FRAME_W) * static_cast<size_t>(CAM_FRAME_H) * 2u;
    s_crop = static_cast<uint8_t *>(heap_caps_malloc(s_crop_cap, MALLOC_CAP_SPIRAM));
    if (s_crop == nullptr) {
        s_crop_cap = 0;
        ESP_LOGW(TAG, "no PSRAM for the detection crop; detecting on full frames only");
    }

    ESP_LOGI(TAG,
             "human_face_detect msrmnp_s8_v1 loaded (internal %u -> %u B, psram %u -> %u B)",
             static_cast<unsigned>(heap_before),
             static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)),
             static_cast<unsigned>(psram_before),
             static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));
    ESP_LOGI(TAG, "eye model bound: open_closed_eye float32, %d params in flash",
             270 + 10 + 1800 + 20 + 9000 + 50 + 100);
    ESP_LOGW(TAG, "eye model is IR-trained (AUC 0.62 on visible light): PERCLOS now "
                  "moves, but do not read it as accurate in daylight. "
                  "See gap 6 in PROJECT_STATE.md.");
    return true;
}

bool model_ready() {
    return s_ready;
}

bool model_eye_ready() {
    // The weights are linked in, so this is true whenever the detector is: the eye
    // model needs the face landmarks to know where to crop.
    return s_ready;
}

// Shared tail: everything after the img_t is built is format independent.
//
// `roi` is the crop the image came from, so the winning box and its landmarks can be
// translated back into frame coordinates. Pass an invalid FaceBox for a full-frame
// run - face_gate_map_out() is then a no-op, which is the case that runs whenever
// the track is cold.
static bool run_and_pick(dl::image::img_t &img, const FaceBox &roi, FaceDetection *out) {
    auto &results = s_face->run(img);
    if (results.empty()) return false;

    FaceBox boxes[MAX_CANDIDATES];
    Landmarks lms[MAX_CANDIDATES];
    float scores[MAX_CANDIDATES];
    int n = 0;

    for (const auto &r : results) {
        if (n >= MAX_CANDIDATES) break;
        if (r.box.size() < 4) continue;
        if (r.keypoint.size() < 10) continue;   // no landmarks, nothing to verify

        FaceBox b;
        b.x = r.box[0];
        b.y = r.box[1];
        b.w = r.box[2] - r.box[0];
        b.h = r.box[3] - r.box[1];
        b.valid = b.w > 0 && b.h > 0;
        if (!b.valid) continue;

        // ESP-DL emits (left eye, left mouth, nose, right eye, right mouth) and
        // everything else in this project uses YuNet's order. The reorder happens
        // here, once, so no caller can index the raw array by accident.
        float kp[10];
        for (int i = 0; i < 10; ++i) kp[i] = static_cast<float>(r.keypoint[i]);

        boxes[n] = b;
        // Two steps, and both are needed. The first maps ESP-DL's index order onto
        // this project's; the second puts the eye pair in image order, because the
        // frame this detector was handed is horizontally mirrored and canonical order
        // assumes it is not. See behavior_orient_landmarks() - without it every
        // vertical cue comes out with the wrong sign.
        lms[n] = behavior_orient_landmarks(behavior_from_espdl_keypoints(kp));
        scores[n] = r.score;
        ++n;
    }

    s_stats.candidates = n;

    // The track is in frame coordinates and these candidates are in crop
    // coordinates, so the comparison has to happen in one of the two. Shifting the
    // track into the crop is cheaper than shifting every candidate out of it.
    FaceBox track_local = s_track;
    if (roi.valid && track_local.valid) {
        track_local.x -= roi.x;
        track_local.y -= roi.y;
    }

    // Run the plausibility check over every candidate once, before picking, and keep
    // the first failure with the numbers it failed on. This is the only window into
    // the gate's decisions, and it exists because the first version of it reported a
    // bare count: on hardware that read "gate dropped 2" while rejecting 100% of real
    // candidates, which is indistinguishable from "there was nothing there".
    int good = 0;
    FaceReject first_bad = FaceReject::None;
    int first_bad_i = -1;
    FaceGeometry first_bad_g{};
    for (int i = 0; i < n; ++i) {
        FaceGeometry g{};
        const FaceReject why = face_gate_check(lms[i], boxes[i], &g);
        if (why == FaceReject::None) {
            ++good;
        } else if (first_bad_i < 0) {
            first_bad = why;
            first_bad_i = i;
            first_bad_g = g;
        }
    }
    s_stats.rejected = n - good;
    s_stats.reject = first_bad;

    if (first_bad_i >= 0) {
        // Rate-limited to once a second: a rejection every third frame would flood the
        // monitor, and one sample a second is enough to read the distribution off.
        static int64_t last_log = 0;
        const int64_t now = esp_timer_get_time();
        if (now - last_log > 1000000) {
            last_log = now;
            const FaceBox &b = boxes[first_bad_i];
            ESP_LOGW(TAG,
                     "gate: %d cand, #%d would fail %s%s | box %d,%d %dx%d score %.2f"
                     " | eye_dist %.1f (%.2f of box w) roll %.1f jaw %.2f nose_frac %.2f",
                     n, first_bad_i, face_gate_reject_name(first_bad),
                     FACE_GATE_ENFORCE ? " (ENFORCED)" : " (advisory)",
                     b.x, b.y, b.w, b.h, scores[first_bad_i],
                     first_bad_g.eye_dist,
                     b.w > 0 ? first_bad_g.eye_dist / static_cast<float>(b.w) : 0.0f,
                     first_bad_g.roll, first_bad_g.jaw_drop, first_bad_g.nose_frac);
        }
    }

    const int pick = face_gate_pick(boxes, lms, n, track_local);
    if (pick < 0) return false;

    out->x = boxes[pick].x;
    out->y = boxes[pick].y;
    out->w = boxes[pick].w;
    out->h = boxes[pick].h;
    out->score = scores[pick];
    out->lm = lms[pick];
    out->valid = true;

    FaceBox won = boxes[pick];
    face_gate_map_out(roi, &won, &out->lm);
    out->x = won.x;
    out->y = won.y;
    return true;
}

// Copy a sub-rectangle of an RGB565 frame into a contiguous buffer, because ESP-DL
// takes an img_t with no stride field - a sub-rectangle of the frame is not a valid
// image on its own.
static bool stage_crop(const uint8_t *frame, int width, const FaceBox &roi) {
    const size_t need = static_cast<size_t>(roi.w) * static_cast<size_t>(roi.h) * 2u;
    if (s_crop == nullptr || need > s_crop_cap) return false;
    const size_t row_bytes = static_cast<size_t>(roi.w) * 2u;
    for (int y = 0; y < roi.h; ++y) {
        const uint8_t *src = frame + (static_cast<size_t>(roi.y + y) * width + roi.x) * 2u;
        memcpy(s_crop + static_cast<size_t>(y) * row_bytes, src, row_bytes);
    }
    return true;
}

// TEMP bring-up: same detector, RGB888 input, to isolate RGB565 handling.
bool model_detect_face_rgb888(const uint8_t *rgb888, int width, int height, FaceDetection *out) {
    if (out != nullptr) out->valid = false;
    if (!s_ready || s_face == nullptr || rgb888 == nullptr || out == nullptr) return false;
    dl::image::img_t img = {};
    img.data = const_cast<uint8_t *>(rgb888);
    img.width = static_cast<uint16_t>(width);
    img.height = static_cast<uint16_t>(height);
    img.pix_type = dl::image::DL_IMAGE_PIX_TYPE_RGB888;
    const FaceBox no_roi;
    return run_and_pick(img, no_roi, out);
}

bool model_detect_face(const uint8_t *rgb565, int width, int height, bool full_frame,
                       FaceDetection *out) {
    if (out != nullptr) out->valid = false;
    if (!s_ready || s_face == nullptr || rgb565 == nullptr || out == nullptr) return false;
    if (width <= 0 || height <= 0) return false;

    const int64_t t0 = esp_timer_get_time();
    s_stats = ModelDetectStats{};

    // Search the crop around the last known position when there is one. Both the
    // full-frame and cropped paths are otherwise identical, including the byte order:
    // on ESP32-S3 human_face_detect builds its preprocessor with
    // DL_IMAGE_CAP_RGB565_BIG_ENDIAN, which is what the sensor emits.
    // CAM_RGB565_BYTE_SWAP describes the preview path, not this one - swapping here
    // would scramble the channels the detector was trained on.
    FaceBox roi;
    if (!full_frame) roi = face_gate_roi(s_track, width, height);
    if (roi.valid && !stage_crop(rgb565, width, roi)) roi.valid = false;

    dl::image::img_t img = {};
    img.data = roi.valid ? s_crop : const_cast<uint8_t *>(rgb565);
    img.width = static_cast<uint16_t>(roi.valid ? roi.w : width);
    img.height = static_cast<uint16_t>(roi.valid ? roi.h : height);
    img.pix_type = dl::image::DL_IMAGE_PIX_TYPE_RGB565;

    s_stats.used_roi = roi.valid;
    s_stats.roi_x = roi.x;
    s_stats.roi_y = roi.y;
    s_stats.roi_w = roi.valid ? roi.w : width;
    s_stats.roi_h = roi.valid ? roi.h : height;

    const bool hit = run_and_pick(img, roi, out);
    s_stats.us = esp_timer_get_time() - t0;

    if (hit) {
        s_track.x = out->x;
        s_track.y = out->y;
        s_track.w = out->w;
        s_track.h = out->h;
        s_track.valid = true;
    } else if (roi.valid) {
        // Missing inside the crop is the one failure the crop itself can cause, so
        // it costs the track rather than being retried here. The next detection is
        // then a full-frame sweep - one detect interval later, which the caller is
        // already covering by holding the last box.
        s_track.valid = false;
    }
    return hit;
}

void model_detect_forget() {
    s_track.valid = false;
}

void model_detect_stats(ModelDetectStats *out) {
    if (out != nullptr) *out = s_stats;
}

// Reads one pixel of the camera frame as 8-bit BGR. esp32-camera emits RGB565
// most-significant byte first, which is why the two bytes are combined in this
// order and not swapped - the same convention model_detect_face() relies on.
static inline void rgb565_at(const uint8_t *frame, int width, int x, int y,
                             int *b, int *g, int *r) {
    const uint8_t *px = frame + (static_cast<size_t>(y) * width + x) * 2;
    const uint16_t v = static_cast<uint16_t>((px[0] << 8) | px[1]);
    *r = ((v >> 11) & 0x1F) << 3;
    *g = ((v >> 5) & 0x3F) << 2;
    *b = (v & 0x1F) << 3;
}

float model_eye_closed_prob(const uint8_t *rgb565, int width, int height,
                            const Landmarks &lm, int eye, int face_side) {
    if (rgb565 == nullptr || !lm.valid || width <= 0 || height <= 0) return 0.0f;
    if (eye < 0 || eye > 1) return 0.0f;

    // EYE_PATCH_SCALE = 0.20 in eyestate.py, chosen there by AUC over 0.12-0.36;
    // wider crops that take in brow and cheek measurably degraded it. The floor of
    // 8 px is eyestate.py's too.
    int side = static_cast<int>(static_cast<float>(face_side) * 0.20f);
    if (side < 8) side = 8;

    const float cx = lm.x[eye];   // canonical order: 0 right eye, 1 left eye
    const float cy = lm.y[eye];
    const float x0 = cx - side * 0.5f;
    const float y0 = cy - side * 0.5f;

    // Bilinear resize to 32x32 with half-pixel centres, which is cv2.resize's
    // convention - the desktop path this has to agree with runs through OpenCV.
    // Sampling straight from the frame rather than cropping first saves a copy and
    // makes edge clamping fall out of the coordinate clamp below.
    static float tensor[EYE_INPUT_FLOATS];
    const float step = static_cast<float>(side) / 32.0f;
    for (int oy = 0; oy < 32; ++oy) {
        const float sy = y0 + (oy + 0.5f) * step - 0.5f;
        int y1 = static_cast<int>(floorf(sy));
        const float fy = sy - y1;
        int y2 = y1 + 1;
        if (y1 < 0) y1 = 0; else if (y1 >= height) y1 = height - 1;
        if (y2 < 0) y2 = 0; else if (y2 >= height) y2 = height - 1;

        for (int ox = 0; ox < 32; ++ox) {
            const float sx = x0 + (ox + 0.5f) * step - 0.5f;
            int x1 = static_cast<int>(floorf(sx));
            const float fx = sx - x1;
            int x2 = x1 + 1;
            if (x1 < 0) x1 = 0; else if (x1 >= width) x1 = width - 1;
            if (x2 < 0) x2 = 0; else if (x2 >= width) x2 = width - 1;

            int b11, g11, r11, b12, g12, r12, b21, g21, r21, b22, g22, r22;
            rgb565_at(rgb565, width, x1, y1, &b11, &g11, &r11);
            rgb565_at(rgb565, width, x2, y1, &b12, &g12, &r12);
            rgb565_at(rgb565, width, x1, y2, &b21, &g21, &r21);
            rgb565_at(rgb565, width, x2, y2, &b22, &g22, &r22);

            const float w11 = (1.0f - fx) * (1.0f - fy);
            const float w12 = fx * (1.0f - fy);
            const float w21 = (1.0f - fx) * fy;
            const float w22 = fx * fy;

            // Channel-first, and BGR rather than RGB: eyestate.py feeds OpenCV
            // images, so BGR is the order every published number was measured in.
            const float bb = b11 * w11 + b12 * w12 + b21 * w21 + b22 * w22;
            const float gg = g11 * w11 + g12 * w12 + g21 * w21 + g22 * w22;
            const float rr = r11 * w11 + r12 * w12 + r21 * w21 + r22 * w22;
            const int at = oy * 32 + ox;
            tensor[0 * 1024 + at] = (bb - 127.0f) / 255.0f;
            tensor[1 * 1024 + at] = (gg - 127.0f) / 255.0f;
            tensor[2 * 1024 + at] = (rr - 127.0f) / 255.0f;
        }
    }

    return eye_model_infer_closed(tensor);
}
