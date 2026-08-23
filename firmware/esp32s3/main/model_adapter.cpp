#include "model_adapter.h"

#include <cmath>
#include <new>

#include "esp_heap_caps.h"
#include "esp_log.h"

#include "dl_detect_define.hpp"
#include "dl_image_define.hpp"
#include "eye_model.h"
#include "human_face_detect.hpp"

static const char *TAG = "model";
static bool s_ready = false;
static HumanFaceDetect *s_face = nullptr;

// Stage thresholds. MSR is the coarse proposal stage, MNP the refinement; ESP-DL
// ships both at 0.5. See model_init() for why MSR is lowered and MNP is not.
// 0.30 was still too strict on this camera - measured empirically: 0.50 and 0.30
// both yield nothing, 0.10 yields a detection the MNP stage then scores at 1.00.
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
static bool run_and_pick(dl::image::img_t &img, FaceDetection *out) {
    auto &results = s_face->run(img);
    if (results.empty()) return false;

    // Largest box wins: the driver is the closest face in the cabin.
    const dl::detect::result_t *best = nullptr;
    for (const auto &r : results) {
        if (r.box.size() < 4) continue;
        if (best == nullptr || r.box_area() > best->box_area()) best = &r;
    }
    if (best == nullptr) return false;

    out->x = best->box[0];
    out->y = best->box[1];
    out->w = best->box[2] - best->box[0];
    out->h = best->box[3] - best->box[1];
    out->score = best->score;

    // ESP-DL emits (left eye, left mouth, nose, right eye, right mouth); everything
    // in this project uses YuNet's order. behavior_from_espdl_keypoints() does the
    // mapping and tests/test_firmware_parity.py guards it - never index this here.
    const int n = static_cast<int>(best->keypoint.size());
    for (int i = 0; i < 10; ++i) {
        out->keypoint[i] = (i < n) ? static_cast<float>(best->keypoint[i]) : 0.0f;
    }
    out->valid = true;
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
    return run_and_pick(img, out);
}

bool model_detect_face(const uint8_t *rgb565, int width, int height, FaceDetection *out) {
    if (out != nullptr) out->valid = false;
    if (!s_ready || s_face == nullptr || rgb565 == nullptr || out == nullptr) return false;
    if (width <= 0 || height <= 0) return false;

    // The frame goes in untouched. On ESP32-S3 human_face_detect builds its
    // preprocessor with DL_IMAGE_CAP_RGB565_BIG_ENDIAN, which is exactly the order
    // the OV3660 emits. CAM_RGB565_BYTE_SWAP describes the panel path, not this one;
    // swapping here would scramble the channels the detector was trained on.
    dl::image::img_t img = {};
    img.data = const_cast<uint8_t *>(rgb565);
    img.width = static_cast<uint16_t>(width);
    img.height = static_cast<uint16_t>(height);
    img.pix_type = dl::image::DL_IMAGE_PIX_TYPE_RGB565;
    return run_and_pick(img, out);
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
