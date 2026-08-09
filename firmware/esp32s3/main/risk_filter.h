#pragma once
#include <stddef.h>

class RiskFilter {
public:
    RiskFilter(float trigger=0.72f, int required=8, int cooldown=60)
      : trigger_(trigger), required_(required), cooldown_(cooldown) {}
    bool update(float p);
private:
    float trigger_; int required_; int cooldown_;
    int streak_=0; int cooldown_left_=0;
};
