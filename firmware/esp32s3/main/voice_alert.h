#pragma once

#include <cstdint>

enum class AlertLanguage : uint8_t {
    English = 0,
    Khmer = 1,
};

// What the alert is about. The driver is far more likely to act on a warning that
// names the behaviour it saw than on a generic chime, so each reason maps to its own
// recorded clip and on-screen banner.
enum class AlertReason : uint8_t {
    Drowsy = 0,     // sustained risk: "you appear drowsy, take a break"
    Microsleep = 1, // eyes closed for over a second: most urgent
    Yawning = 2,    // repeated yawns: early warning
    HeadNod = 3,    // head dropping
};

struct VoiceAlertConfig {
    AlertLanguage language = AlertLanguage::English;
    uint32_t cooldown_ms = 30000;
    uint32_t max_repeat_count = 3;
    bool buzzer_fallback = true;
};

bool voice_alert_init(const VoiceAlertConfig& config);

// Returns true when an announcement actually started (i.e. not suppressed by the
// cooldown or repeat cap).
bool voice_alert_trigger(uint32_t now_ms, AlertReason reason = AlertReason::Drowsy);
void voice_alert_set_language(AlertLanguage language);

// Short uppercase text for the on-screen banner, matching the spoken clip.
const char* voice_alert_banner_text(AlertReason reason);

// Asset basename for the recorded clip, without extension or language prefix.
const char* voice_alert_clip_name(AlertReason reason);

// True while an announcement is still playing, for the UI banner.
bool voice_alert_is_active(uint32_t now_ms);
