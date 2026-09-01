#include "presence.h"

const char *presence_state_name(PresenceState s) {
    switch (s) {
        case PresenceState::Warmup: return "warmup";
        case PresenceState::Present: return "present";
        case PresenceState::Absent: return "absent";
        case PresenceState::NoDriver: return "no-driver";
        case PresenceState::Fault: return "fault";
        default: return "?";
    }
}

const char *presence_health_name(PipelineHealth h) {
    switch (h) {
        case PipelineHealth::Ok: return "ok";
        case PipelineHealth::ModelFault: return "model-fault";
        case PipelineHealth::CameraFault: return "camera-fault";
        default: return "?";
    }
}

void PresenceMonitor::configure(const PresenceConfig &cfg) {
    cfg_ = cfg;
    // Deliberately does not reset the timers. A configuration change mid-drive is a
    // tuning action, not a new episode, and clearing an absence that is already three
    // seconds old because someone moved a slider would silently cancel an alert that
    // was about to be correct.
}

void PresenceMonitor::reset() {
    state_ = PresenceState::Warmup;
    absent_s_ = 0.0f;
    present_s_ = 0.0f;
    healthy_s_ = 0.0f;
    since_alert_s_ = 0.0f;
    alerts_ = 0;
    announced_ = false;
}

PresenceResult PresenceMonitor::update(bool driver_present, PipelineHealth health,
                                       float dt_s) {
    const float dt = dt_s > 0.0f ? dt_s : 0.0f;

    PresenceResult out;
    out.health = health;
    out.alerts = alerts_;

    // --- the device cannot see -------------------------------------------------
    // Not an absence, and it must never be reported as one. Everything about the
    // absence episode is discarded rather than frozen: when the camera comes back
    // the cabin may hold a completely different situation, and resuming a countdown
    // that started before the fault would be announcing a conclusion drawn from
    // evidence that no longer applies.
    if (health != PipelineHealth::Ok) {
        state_ = PresenceState::Fault;
        absent_s_ = 0.0f;
        present_s_ = 0.0f;
        healthy_s_ = 0.0f;
        announced_ = false;
        out.state = state_;
        return out;
    }

    healthy_s_ += dt;

    if (driver_present) {
        present_s_ += dt;
        // Absence only clears once presence has held for clear_s. Until then the
        // absence timer keeps running, which is what stops a single flickering
        // detection on an empty seat from resetting the countdown forever.
        if (present_s_ >= cfg_.clear_s) {
            absent_s_ = 0.0f;
            announced_ = false;
            since_alert_s_ = 0.0f;
            state_ = (healthy_s_ >= cfg_.warmup_s) ? PresenceState::Present
                                                   : PresenceState::Warmup;
        } else if (state_ != PresenceState::NoDriver) {
            // Mid-debounce. Keep whatever the state already was rather than
            // announcing presence early; the only thing that has happened is that a
            // detection arrived.
            absent_s_ += dt;
        }
    } else {
        present_s_ = 0.0f;
        absent_s_ += dt;
        if (healthy_s_ < cfg_.warmup_s) {
            // Still settling. Absence is measured but not acted on, so the timer is
            // honest on the status page while the alert stays disarmed.
            state_ = PresenceState::Warmup;
        } else if (absent_s_ >= cfg_.alert_after_s) {
            state_ = PresenceState::NoDriver;
        } else {
            state_ = PresenceState::Absent;
        }
    }

    // --- the announcement ------------------------------------------------------
    if (state_ == PresenceState::NoDriver && cfg_.enabled) {
        since_alert_s_ += dt;
        if (!announced_) {
            announced_ = true;
            since_alert_s_ = 0.0f;
            ++alerts_;
            out.alert = true;
        } else if (cfg_.repeat_s > 0.0f && since_alert_s_ >= cfg_.repeat_s) {
            since_alert_s_ = 0.0f;
            ++alerts_;
            out.alert = true;
        }
    }

    out.state = state_;
    out.absent_s = absent_s_;
    out.present_s = present_s_;
    out.alerts = alerts_;
    return out;
}
