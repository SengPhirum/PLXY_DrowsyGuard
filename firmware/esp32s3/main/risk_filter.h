#pragma once
#include <stddef.h>

class RiskFilter {
public:
    RiskFilter(float trigger=0.72f, int required=8, int cooldown=60)
      : trigger_(trigger), required_(required), cooldown_(cooldown) {}
    bool update(float p);
    // How many consecutive frames are currently over the trigger. Reported on the
    // status page: "risk 0.61, 5 of 8 frames" is diagnosable, a bare score is not.
    int streak() const { return streak_; }
    int required() const { return required_; }
private:
    float trigger_; int required_; int cooldown_;
    int streak_=0; int cooldown_left_=0;
};
