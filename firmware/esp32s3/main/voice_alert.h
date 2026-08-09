#pragma once

#include <cstdint>

enum class AlertLanguage : uint8_t {
    English = 0,
    Khmer = 1,
};

struct VoiceAlertConfig {
    AlertLanguage language = AlertLanguage::English;
    uint32_t cooldown_ms = 30000;
    uint32_t max_repeat_count = 3;
    bool buzzer_fallback = true;
};

bool voice_alert_init(const VoiceAlertConfig& config);
bool voice_alert_trigger(uint32_t now_ms);
void voice_alert_set_language(AlertLanguage language);
