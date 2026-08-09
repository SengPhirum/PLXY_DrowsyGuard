#include "model_adapter.h"
#include "esp_log.h"
static const char *TAG="model";

bool model_init() {
    ESP_LOGW(TAG, "ESP-DL model adapter not bound yet: pin ESP-DL/ESP-PPQ version and replace this adapter.");
    return false;
}

float model_predict_drowsy(const uint8_t*, size_t) {
    return 0.0f;
}
