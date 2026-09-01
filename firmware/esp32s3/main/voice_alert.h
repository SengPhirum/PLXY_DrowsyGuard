#pragma once

#include <cstdint>

enum class AlertLanguage : uint8_t {
    English = 0,
    Khmer = 1,
};

// What the alert is about. The driver is far more likely to act on a warning that
// names the behaviour it saw than on a generic chime, so each reason maps to its own
// recorded clip and on-screen banner.
//
// The numbering is part of the HTTP API - /api/alert-test takes it as ?reason=N and
// docs/reference/device-api.md publishes the table - so values are appended, never
// renumbered.
enum class AlertReason : uint8_t {
    Drowsy = 0,     // sustained risk: "you appear drowsy, take a break"
    Microsleep = 1, // eyes closed for over a second: most urgent
    Yawning = 2,    // repeated yawns: early warning
    HeadNod = 3,    // head dropping
    // A confirmed sneeze. Not a drowsiness cue - the point of detecting it is to
    // suppress the microsleep alarm it would otherwise trigger - but it is announced
    // in its own right, because a driver whose alarm just went quiet during a violent
    // reflex should be told the system noticed and decided, rather than left to
    // wonder whether it saw anything at all.
    Sneeze = 4,
    // Nobody in front of the camera, for long enough that it is not a glance away.
    // The most important thing this device can say when it is not watching a driver:
    // a monitoring system that has silently stopped monitoring is worse than none,
    // because the driver believes they are covered.
    NoDriver = 5,
};

constexpr uint8_t ALERT_REASON_COUNT = 6;

// Alerts do not all belong to the same conversation, and giving them one shared
// cooldown was wrong: a sneeze announcement suppressed by the drowsiness cooldown is
// a sneeze that silently did nothing, and a no-driver alert suppressed by either of
// them is a safety message lost to an unrelated rate limit. Each channel keeps its
// own cooldown, its own repeat cap and its own episode timer.
enum class AlertChannel : uint8_t {
    Drowsiness = 0,   // Drowsy, Microsleep, Yawning, HeadNod
    Sneeze = 1,
    Presence = 2,     // NoDriver
    Count = 3,
};

AlertChannel voice_alert_channel(AlertReason reason);

struct VoiceAlertChannelConfig {
    uint32_t cooldown_ms = 30000;
    // Announcements per episode. The cap exists so a driver who is already awake
    // and pulling over is not nagged; it is NOT a lifetime budget. Zero means no cap.
    uint32_t max_repeat_count = 3;
    // How long the driver has to stay out of trouble before the repeat cap resets
    // and a new episode can be announced. Without this the cap is permanent: after
    // three warnings on a long drive the device would go silent for the rest of the
    // trip, which is the one failure mode a drowsiness alarm must not have. Zero
    // disables the cap reset, which is only sensible when max_repeat_count is 0.
    uint32_t repeat_reset_ms = 300000;   // 5 minutes
};

struct VoiceAlertConfig {
    AlertLanguage language = AlertLanguage::English;

    VoiceAlertChannelConfig drowsiness{30000, 3, 300000};

    // The behaviour analyzer already emits at most one sneeze alert per
    // SNEEZE_ALERT_COOLDOWN_S (2.5 s), so this is a backstop against a caller that
    // triggers by hand rather than the primary rate limit - hence the shorter
    // cooldown and no repeat cap. A sneeze episode is not something to nag about,
    // but it is also not something to go quiet about after three of them.
    VoiceAlertChannelConfig sneeze{2000, 0, 0};

    // PresenceMonitor fires exactly once per absence episode, so this too is a
    // backstop. No cap, because "nobody is driving" must never be a message the
    // device has used up its allowance of.
    VoiceAlertChannelConfig presence{5000, 0, 0};

    bool buzzer_fallback = true;
};

bool voice_alert_init(const VoiceAlertConfig& config);

// Two-letter code for the current language: "en" or "km". This is what selects
// which clip plays, so it is also what voice_clips.h looks up.
const char* voice_alert_language_code();

// True when the language survived a reboot, i.e. it was read back from NVS rather
// than defaulted. Reported on the status page: "the setting did not stick" and
// "the setting was never changed" look identical otherwise.
bool voice_alert_language_persisted();

// Returns true when an announcement actually started (i.e. not suppressed by the
// cooldown or repeat cap of the reason's channel).
bool voice_alert_trigger(uint32_t now_ms, AlertReason reason = AlertReason::Drowsy);
// Persists to NVS as well as taking effect immediately. A driver who set the
// warnings to Khmer should not find them back in English after a power cycle -
// this is the one setting where the wrong value is actively unhelpful rather than
// merely inconvenient.
void voice_alert_set_language(AlertLanguage language);
bool voice_alert_set_language_code(const char* code);   // "en" / "km"; false if unknown

// Short uppercase text for the on-screen banner, matching the spoken clip.
const char* voice_alert_banner_text(AlertReason reason);

// Asset basename for the recorded clip, without extension or language prefix.
const char* voice_alert_clip_name(AlertReason reason);

// True while an announcement is still playing, for the web banner.
bool voice_alert_is_active(uint32_t now_ms);

// Announcements made since boot, across every channel. Reported on the status page,
// and the only record there is now that nothing is drawn on a panel.
uint32_t voice_alert_count();

// Announcements of one specific reason since boot. The totals per reason are what
// turn "the device alerted 40 times" into something that can be acted on: 40
// microsleeps and 40 no-driver announcements describe completely different drives.
uint32_t voice_alert_count_for(AlertReason reason);

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
