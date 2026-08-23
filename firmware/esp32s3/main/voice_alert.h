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
    // Announcements per episode. The cap exists so a driver who is already awake
    // and pulling over is not nagged; it is NOT a lifetime budget.
    uint32_t max_repeat_count = 3;
    // How long the driver has to stay out of trouble before the repeat cap resets
    // and a new episode can be announced. Without this the cap is permanent: after
    // three warnings on a long drive the device would go silent for the rest of the
    // trip, which is the one failure mode a drowsiness alarm must not have.
    uint32_t repeat_reset_ms = 300000;   // 5 minutes
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

// True while an announcement is still playing, for the web banner.
bool voice_alert_is_active(uint32_t now_ms);

// Announcements made since boot. Reported on the status page, and the only record
// there is now that nothing is drawn on a panel.
uint32_t voice_alert_count();

// Silences the speaker without stopping detection - for bench work, and for a
// passenger-seat demo where the alarm has already been demonstrated. Risk scoring,
// the event log and the counters all keep running.
void voice_alert_set_muted(bool muted);
bool voice_alert_muted();

// Plays one announcement immediately, bypassing the cooldown and the repeat cap.
// This is the speaker self-test the web UI exposes: with no display, it is the only
// way to separate "no alert fired" from "the amplifier is not wired". Returns false
// when the alerts are muted, so the page can say so rather than look broken.
bool voice_alert_test(AlertReason reason);
