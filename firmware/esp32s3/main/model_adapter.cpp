#include "model_adapter.h"

#include <new>

#include "esp_heap_caps.h"
#include "esp_log.h"

#include "dl_detect_define.hpp"
#include "dl_image_define.hpp"
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

Eye open/closed: NOT bound. models/detectors/open_closed_eye.onnx is in the repo but
ESP-DL loads .espdl, and producing one needs esp-ppq plus a calibration set of real
eye crops - neither is in this repo, and scripts/quantize_espdl.py is still a stub
that refuses rather than guesses. model_eye_ready() reports that honestly instead of
letting a hardcoded 0.0 masquerade as evidence; main.cpp gates PERCLOS and alerting
on it. Note also gap 6 in PROJECT_STATE.md: that model is IR-trained and scores AUC
0.62 on visible light, so binding it will be mechanically correct and still classify
badly in daylight until it is fine-tuned.
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
    ESP_LOGW(TAG, "eye model not bound: PERCLOS and alerts stay disabled. "
                  "See docs/HARDWARE_SETUP.md stage 3 step 4.");
    return true;
}

bool model_ready() {
    return s_ready;
}

bool model_eye_ready() {
    return false;
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

float model_eye_closed_prob(const uint8_t *, int, int, const Landmarks &, int) {
    // Unbound; see the note at the top of this file. main.cpp gates on
    // model_eye_ready() so this 0.0 is never mistaken for an open eye.
    return 0.0f;
}
