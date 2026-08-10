"""Python mirror of the ESP32 firmware risk filter.

Kept behaviourally identical to `firmware/esp32s3/main/risk_filter.cpp` so that
thresholds tuned in the live dashboard transfer directly to the device. If the
firmware logic changes, change it here too and update `tests/test_risk.py`.
"""

DEFAULT_TRIGGER = 0.72
DEFAULT_REQUIRED = 8
DEFAULT_COOLDOWN = 60


class RiskFilter:
    """Temporal risk accumulator: sustained high p(drowsy) fires one alert."""

    def __init__(self, trigger=DEFAULT_TRIGGER, required=DEFAULT_REQUIRED, cooldown=DEFAULT_COOLDOWN):
        self.trigger = float(trigger)
        self.required = int(required)
        self.cooldown = int(cooldown)
        self.streak = 0
        self.cooldown_left = 0

    def update(self, p) -> bool:
        """Feed one frame probability; returns True on an alert edge.

        Mirrors RiskFilter::update, including the early return that freezes the
        streak while a cooldown is counting down.
        """
        if self.cooldown_left > 0:
            self.cooldown_left -= 1
            return False
        if p >= self.trigger:
            self.streak += 1
        elif self.streak > 0:
            self.streak -= 1
        if self.streak >= self.required:
            self.streak = 0
            self.cooldown_left = self.cooldown
            return True
        return False

    def reset(self):
        self.streak = 0
        self.cooldown_left = 0
