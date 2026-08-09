#include "esp_log.h"
#include "driver/gpio.h"
#include "risk_filter.h"
#include "model_adapter.h"

static const char *TAG="drowsyguard";
static constexpr gpio_num_t BUZZER = GPIO_NUM_2;

extern "C" void app_main(void) {
    gpio_config_t io{}; io.pin_bit_mask = 1ULL << BUZZER; io.mode = GPIO_MODE_OUTPUT;
    gpio_config(&io); gpio_set_level(BUZZER, 0);

    ESP_LOGI(TAG, "DrowsyGuard ESP32-S3 research firmware starting");
    if (!model_init()) {
        ESP_LOGE(TAG, "Model adapter is not configured. See firmware/esp32s3/README.md");
        return;
    }

    // Camera capture + crop/resize is deliberately integrated only after selecting
    // the exact board because ESP32-S3 camera pins differ substantially by module.
    // RiskFilter filter; float p = model_predict_drowsy(frame64, 4096);
    // if (filter.update(p)) { gpio_set_level(BUZZER,1); ... }
}
