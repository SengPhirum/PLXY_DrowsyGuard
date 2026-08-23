#pragma once
#include <stddef.h>

// Requires the fused score to stay over the trigger for `required` consecutive
// frames, then holds off for `cooldown` frames.
//
// Both are in FRAMES, and that is a trap worth naming: they were chosen as durations
// - 8 frames is about half a second at 15 fps - so anything that changes the frame
// rate changes the alarm's sensitivity without touching a threshold. Making the
// capture loop faster is exactly such a change. main.cpp therefore re-derives both
// from the measured rate through retune(), so the intended half second stays half a
// second.
//
// The defaults here mirror src/drowsyguard/risk.py and are checked against it by
// tests/test_firmware_parity.py, which parses this constructor signature - so the
// numbers below are load-bearing text, not just a default. main.cpp passes its own
// trigger (RISK_TRIGGER) anyway.
class RiskFilter {
public:
    RiskFilter(float trigger=0.72f, int required=8, int cooldown=60)
      : trigger_(trigger), required_(required), cooldown_(cooldown) {}
    bool update(float p);
    // How many consecutive frames are currently over the trigger. Reported on the
    // status page: "risk 0.61, 5 of 8 frames" is diagnosable, a bare score is not.
    int streak() const { return streak_; }
    int required() const { return required_; }
    int cooldown() const { return cooldown_; }

    // Re-derive the frame counts from a new frame rate. Deliberately does not touch
    // the streak: a raised threshold should extend the confirmation in progress, not
    // discard the evidence already gathered for it.
    void retune(int required, int cooldown) {
        required_ = required > 1 ? required : 1;
        cooldown_ = cooldown > 0 ? cooldown : 0;
    }
private:
    float trigger_; int required_; int cooldown_;
    int streak_=0; int cooldown_left_=0;
};
