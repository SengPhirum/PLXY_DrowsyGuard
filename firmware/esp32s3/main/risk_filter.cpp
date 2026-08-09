#include "risk_filter.h"
bool RiskFilter::update(float p) {
    if (cooldown_left_ > 0) { --cooldown_left_; return false; }
    if (p >= trigger_) ++streak_; else if (streak_ > 0) --streak_;
    if (streak_ >= required_) { streak_=0; cooldown_left_=cooldown_; return true; }
    return false;
}
