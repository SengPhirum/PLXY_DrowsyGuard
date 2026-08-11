#include "model_adapter.h"

#include "esp_log.h"

static const char *TAG = "model";
static bool s_ready = false;

/*
Stage 3 of docs/HARDWARE_SETUP.md. Until the two functions below are bound to a
pinned ESP-DL release, model_init() reports false and main.cpp runs the preview-only
bring-up path. That is deliberate: an unbound adapter that silently returns 0.0
would look like a driver who never blinks.

Sketch against esp-dl 3.x + human_face_detect 0.3.x (uncomment the dependencies in
main/idf_component.yml first):

    #include "human_face_detect.hpp"
    #include "dl_image_define.hpp"

    static HumanFaceDetect *s_face = nullptr;

    bool model_init() {
        s_face = new HumanFaceDetect();      // model location set in menuconfig
        s_ready = (s_face != nullptr);
        return s_ready;
    }

    bool model_detect_face(const uint8_t *rgb565, int width, int height,
                           FaceDetection *out) {
        dl::image::img_t img = {
            .data = const_cast<uint8_t *>(rgb565),
            .width = width,
            .height = height,
            .pix_type = dl::image::DL_IMAGE_PIX_TYPE_RGB565,
        };
        auto results = s_face->run(img);
        if (results.empty()) return false;
        // Largest box wins: the driver is the closest face.
        const auto *best = &results.front();
        for (const auto &r : results) {
            if ((r.box[2] - r.box[0]) > (best->box[2] - best->box[0])) best = &r;
        }
        out->x = best->box[0];
        out->y = best->box[1];
        out->w = best->box[2] - best->box[0];
        out->h = best->box[3] - best->box[1];
        out->score = best->score;
        for (int i = 0; i < 10; ++i) out->keypoint[i] = (float)best->keypoint[i];
        out->valid = true;
        return true;
    }

The eye model is not in the registry - it is the quantized open-closed-eye-0001
produced by scripts/quantize_espdl.py. Load it with dl::Model from the espdl blob
and drive it with the preprocessing described in model_adapter.h.
*/

bool model_init() {
    ESP_LOGW(TAG, "ESP-DL adapter not bound; running preview-only. "
                  "See docs/HARDWARE_SETUP.md stage 3.");
    s_ready = false;
    return s_ready;
}

bool model_ready() {
    return s_ready;
}

bool model_detect_face(const uint8_t *, int, int, FaceDetection *out) {
    if (out != nullptr) out->valid = false;
    return false;
}

float model_eye_closed_prob(const uint8_t *, int, int, const Landmarks &, int) {
    return 0.0f;
}
