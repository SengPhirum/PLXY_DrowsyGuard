"""Desktop mirror of ``firmware/esp32s3/main/presence.{h,cpp}``.

"Is anyone actually driving this?" - and, separately, "can this device still tell?"

The firmware header carries the full argument; the short version is that a
drowsiness detector which sees nothing has two completely different reasons for it
and conflating them is a safety defect:

* **nobody is there** - the device works, there is just no driver. Worth one alert,
  because a monitoring system that has silently stopped monitoring is worse than
  none: the driver believes they are covered.
* **the device is broken** - no frames, or no model. "No driver detected" would then
  be a false statement about the cabin dressed up as a statement about the driver,
  and the fix is somewhere else entirely.

Two debounces, in opposite directions. Absence must persist for ``alert_after_s``
before it is announced, so a glance at a mirror is not an alarm; presence must
persist for ``clear_s`` before the alert re-arms, so a single flickering detection
on an empty seat cannot cancel a real absence and restart the countdown forever.
That second failure mode is the quiet one, which is what makes it dangerous.

``tests/test_presence_parity.py`` runs this and the compiled C++ through the same
sequences and requires the same state, the same timer and the same alert edges.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

PRESENCE_ALERT_S = 3.0
PRESENCE_CLEAR_S = 0.5
PRESENCE_REPEAT_S = 0.0
PRESENCE_WARMUP_S = 5.0


class PipelineHealth(Enum):
    OK = 'ok'
    MODEL_FAULT = 'model-fault'
    CAMERA_FAULT = 'camera-fault'


class PresenceState(Enum):
    WARMUP = 'warmup'
    PRESENT = 'present'
    ABSENT = 'absent'
    NO_DRIVER = 'no-driver'
    FAULT = 'fault'


@dataclass
class PresenceConfig:
    alert_after_s: float = PRESENCE_ALERT_S
    clear_s: float = PRESENCE_CLEAR_S
    repeat_s: float = PRESENCE_REPEAT_S
    warmup_s: float = PRESENCE_WARMUP_S
    enabled: bool = True


@dataclass
class PresenceResult:
    state: PresenceState = PresenceState.WARMUP
    # True on exactly the update that announces a no-driver condition, and never
    # again for that episode unless repeat_s is non-zero. An edge, not a level, so
    # the caller needs no de-duplication of its own.
    alert: bool = False
    absent_s: float = 0.0
    present_s: float = 0.0
    alerts: int = 0
    health: PipelineHealth = PipelineHealth.OK


class PresenceMonitor:
    def __init__(self, config: PresenceConfig | None = None):
        self.config = config or PresenceConfig()
        self.reset()

    def reset(self):
        self._state = PresenceState.WARMUP
        self._absent_s = 0.0
        self._present_s = 0.0
        self._healthy_s = 0.0
        self._since_alert_s = 0.0
        self._alerts = 0
        self._announced = False

    def configure(self, config: PresenceConfig):
        """Replace the configuration without disturbing the timers.

        Deliberate: a configuration change mid-drive is a tuning action, not a new
        episode. Clearing an absence that is already three seconds old because
        someone moved a slider would silently cancel an alert about to be correct.
        """
        self.config = config

    def update(self, driver_present: bool, health: PipelineHealth,
               dt_s: float) -> PresenceResult:
        dt = dt_s if dt_s > 0 else 0.0
        cfg = self.config
        out = PresenceResult(health=health, alerts=self._alerts)

        if health is not PipelineHealth.OK:
            # Not an absence, and it must never be reported as one. The episode is
            # discarded rather than frozen: when the camera comes back the cabin may
            # hold a different situation entirely, and resuming a countdown started
            # before the fault would announce a conclusion drawn from stale evidence.
            self._state = PresenceState.FAULT
            self._absent_s = 0.0
            self._present_s = 0.0
            self._healthy_s = 0.0
            self._announced = False
            out.state = self._state
            return out

        self._healthy_s += dt

        if driver_present:
            self._present_s += dt
            if self._present_s >= cfg.clear_s:
                self._absent_s = 0.0
                self._announced = False
                self._since_alert_s = 0.0
                self._state = (PresenceState.PRESENT
                               if self._healthy_s >= cfg.warmup_s
                               else PresenceState.WARMUP)
            elif self._state is not PresenceState.NO_DRIVER:
                # Mid-debounce. All that has happened is that a detection arrived;
                # the absence keeps accruing until presence has actually held.
                self._absent_s += dt
        else:
            self._present_s = 0.0
            self._absent_s += dt
            if self._healthy_s < cfg.warmup_s:
                self._state = PresenceState.WARMUP
            elif self._absent_s >= cfg.alert_after_s:
                self._state = PresenceState.NO_DRIVER
            else:
                self._state = PresenceState.ABSENT

        if self._state is PresenceState.NO_DRIVER and cfg.enabled:
            self._since_alert_s += dt
            if not self._announced:
                self._announced = True
                self._since_alert_s = 0.0
                self._alerts += 1
                out.alert = True
            elif cfg.repeat_s > 0 and self._since_alert_s >= cfg.repeat_s:
                self._since_alert_s = 0.0
                self._alerts += 1
                out.alert = True

        out.state = self._state
        out.absent_s = self._absent_s
        out.present_s = self._present_s
        out.alerts = self._alerts
        return out
