#include "esp_log.h"
#include "esp_timer.h"
#include "risk_filter.h"
#include "model_adapter.h"
#include "voice_alert.h"

static const char *TAG="drowsyguard";

extern "C" void app_main(void) {
    ESP_LOGI(TAG, "DrowsyGuard ESP32-S3 research firmware starting");

    VoiceAlertConfig alert_config{};
    alert_config.language = AlertLanguage::English; // switch to Khmer after approved recording is embedded
    alert_config.cooldown_ms = 30000;
    alert_config.max_repeat_count = 3;
    alert_config.buzzer_fallback = true;
    if (!voice_alert_init(alert_config)) {
        ESP_LOGE(TAG, "Alert subsystem initialization failed");
    }

    if (!model_init()) {
        ESP_LOGE(TAG, "Model adapter is not configured. See firmware/esp32s3/README.md");
        return;
    }

    // Camera capture + crop/resize is integrated only after selecting the exact
    // board because ESP32-S3 camera pin maps differ by module/revision.
    //
    // RiskFilter filter;
    // float p = model_predict_drowsy(frame64, 4096);
    // if (filter.update(p)) {
    //     const uint32_t now_ms = static_cast<uint32_t>(esp_timer_get_time() / 1000ULL);
    //     voice_alert_trigger(now_ms);
    // }
}
