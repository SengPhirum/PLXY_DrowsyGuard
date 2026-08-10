"""Multi-cue drowsiness behaviour analysis from eye state + face geometry.

Eye closure alone is not drowsiness. This module adds the behaviours that actually
accompany it - yawning, long/slow blinks, head nodding - and fuses them into one
risk score for the existing RiskFilter.

It also detects sneezes, and that needs explaining, because a sneeze is *not* a
drowsiness sign. A sneeze slams the eyes shut for roughly a second while the head
jerks, which looks exactly like a microsleep to an eye-closure detector. Detecting it
lets us suppress the false alert rather than count it as drowsiness.

Everything here is derived from YuNet's five landmarks plus the eye-state probability,
so it adds no model weight and stays affordable on an ESP32-S3. All geometric cues are
measured against a rolling per-driver baseline, so face shape and camera angle cancel
out instead of becoming signal - the same reason the whole-face CNN failed.

Landmark order: 0 right eye, 1 left eye, 2 nose, 3 right mouth corner, 4 left mouth.

Validated here: roll tracks applied rotation, jaw_drop rises as the jaw opens, and the
pitch proxy responds while staying roll-stable. Yaw is computed but NOT validated - a
real head-turn test is still needed. Event thresholds are literature-informed defaults,
not tuned on labelled sneeze/yawn/nod video, which this project does not yet have.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

# Durations in seconds; converted to frames using the measured fps.
BLINK_MAX_S = 0.40          # longer than a blink is not a blink
MICROSLEEP_MIN_S = 1.00     # sustained closure worth alarming about
YAWN_MIN_S = 1.20           # yawns are slow; a brief mouth movement is speech
SNEEZE_MAX_S = 1.20         # sneezes resolve quickly
NOD_MAX_S = 1.50            # down-and-back head motion

# Deviation from the rolling baseline, in baseline-relative units.
JAW_OPEN_DELTA = 0.10       # jaw_drop rise that counts as an open mouth
NOD_PITCH_DELTA = 0.06      # nose_frac drop that counts as the head pitching down
SNEEZE_JAW_DELTA = 0.13     # sneezes involve a pronounced mouth movement

RATE_WINDOW_S = 60.0        # window for per-minute event rates

# Fusion weights. PERCLOS dominates because it is the best-established measure;
# the others are corroborating evidence rather than independent alarms.
W_PERCLOS = 0.55
W_LONG_BLINK = 0.20
W_YAWN = 0.15
W_NOD = 0.10

# Rates (events/min) at which a cue is considered fully expressed.
YAWN_RATE_FULL = 4.0
NOD_RATE_FULL = 6.0
LONG_BLINK_RATE_FULL = 12.0


@dataclass
class FaceGeometry:
    valid: bool = False
    roll: float = 0.0          # degrees, eye-line tilt
    jaw_drop: float = 0.0      # eye-line to mouth distance / eye distance
    nose_frac: float = 0.0     # nose position between eye line and mouth (pitch proxy)
    yaw: float = 0.0           # nose horizontal offset / eye distance (UNVALIDATED)
    eye_dist: float = 0.0


def face_geometry(landmarks) -> FaceGeometry:
    """Derive roll/jaw/pitch/yaw from five landmarks, in an upright face frame."""
    if not landmarks or len(landmarks) < 5:
        return FaceGeometry()
    (rx, ry), (lx, ly), (nx, ny), (mrx, mry), (mlx, mly) = landmarks[:5]
    eye_dist = math.hypot(lx - rx, ly - ry)
    if eye_dist < 1e-3:
        return FaceGeometry()

    roll = math.degrees(math.atan2(ly - ry, lx - rx))
    eye_mid = ((rx + lx) / 2.0, (ry + ly) / 2.0)
    mouth_mid = ((mrx + mlx) / 2.0, (mry + mly) / 2.0)

    # Rotate about the eye midpoint so vertical measures do not move with head tilt.
    a = math.radians(-roll)
    ca, sa = math.cos(a), math.sin(a)

    def rot(p):
        dx, dy = p[0] - eye_mid[0], p[1] - eye_mid[1]
        return (ca * dx - sa * dy, sa * dx + ca * dy)

    nose_r = rot((nx, ny))
    mouth_r = rot(mouth_mid)
    jaw = mouth_r[1] / eye_dist
    return FaceGeometry(
        valid=True,
        roll=roll,
        jaw_drop=jaw,
        nose_frac=nose_r[1] / mouth_r[1] if abs(mouth_r[1]) > 1e-6 else 0.0,
        yaw=nose_r[0] / eye_dist,
        eye_dist=eye_dist,
    )


class Baseline:
    """Rolling median baseline, so per-driver anatomy cancels out of every cue."""

    def __init__(self, window=150, min_samples=25):
        self.window = int(window)
        self.min_samples = int(min_samples)
        self._buf = deque(maxlen=self.window)

    def update(self, value):
        self._buf.append(float(value))
        return self.deviation(value)

    def deviation(self, value):
        if len(self._buf) < self.min_samples:
            return 0.0          # not enough history to call anything abnormal
        return float(value) - self.value

    @property
    def value(self):
        if not self._buf:
            return 0.0
        s = sorted(self._buf)
        return s[len(s) // 2]

    @property
    def ready(self):
        return len(self._buf) >= self.min_samples

    def reset(self):
        self._buf.clear()


class EventRate:
    """Counts timestamped events inside a rolling window and reports events/min."""

    def __init__(self, window_s=RATE_WINDOW_S):
        self.window_s = float(window_s)
        self._times = deque()

    def add(self, now_s):
        self._times.append(now_s)

    def rate(self, now_s):
        while self._times and now_s - self._times[0] > self.window_s:
            self._times.popleft()
        if not self._times:
            return 0.0
        span = max(min(now_s, self.window_s), 1e-6)
        return len(self._times) * 60.0 / span

    def count(self):
        return len(self._times)

    def reset(self):
        self._times.clear()


@dataclass
class BehaviorState:
    score: float = 0.0
    perclos: float = 0.0
    eye_closed: float = 0.0
    mouth_open: bool = False
    head_down: bool = False
    events: list = field(default_factory=list)      # events fired on this frame
    blink_rate: float = 0.0
    long_blink_rate: float = 0.0
    yawn_rate: float = 0.0
    nod_rate: float = 0.0
    sneeze_count: int = 0
    suppressed: bool = False                        # sneeze suppression active
    closure_s: float = 0.0                          # current unbroken closure
    baselines_ready: bool = False


class BehaviorAnalyzer:
    """Turn per-frame eye probability + geometry into events and one risk score."""

    def __init__(self, closed_threshold=0.5, fps=30.0):
        self.closed_threshold = float(closed_threshold)
        self.fps = max(float(fps), 1.0)

        self._jaw = Baseline()
        self._pitch = Baseline()

        self._blinks = EventRate()
        self._long_blinks = EventRate()
        self._yawns = EventRate()
        self._nods = EventRate()

        self._t = 0.0
        self._closure_start = None
        self._mouth_start = None
        self._nod_start = None
        self._sneezes = 0
        self._suppress_until = -1.0
        self._yawn_fired = False
        self._nod_fired = False
        self._sneeze_fired = False

    def reset(self):
        self._jaw.reset(); self._pitch.reset()
        for r in (self._blinks, self._long_blinks, self._yawns, self._nods):
            r.reset()
        self._t = 0.0
        self._closure_start = self._mouth_start = self._nod_start = None
        self._sneezes = 0
        self._suppress_until = -1.0
        self._yawn_fired = self._nod_fired = self._sneeze_fired = False

    def update(self, p_closed, geometry: FaceGeometry, perclos, dt=None):
        self._t += (1.0 / self.fps) if dt is None else float(dt)
        now = self._t
        events = []

        closed = p_closed >= self.closed_threshold
        jaw_dev = self._jaw.update(geometry.jaw_drop) if geometry.valid else 0.0
        pitch_dev = self._pitch.update(geometry.nose_frac) if geometry.valid else 0.0

        mouth_open = geometry.valid and jaw_dev >= JAW_OPEN_DELTA
        head_down = geometry.valid and pitch_dev <= -NOD_PITCH_DELTA

        # --- mouth: sustained opening is a yawn; a brief one is speech ---
        if mouth_open:
            if self._mouth_start is None:
                self._mouth_start = now
                self._yawn_fired = False
            elif not self._yawn_fired and now - self._mouth_start >= YAWN_MIN_S:
                self._yawns.add(now)
                events.append('yawn')
                self._yawn_fired = True
        else:
            self._mouth_start = None
            self._yawn_fired = False

        # --- head: a down-and-back excursion is a nod ---
        if head_down:
            if self._nod_start is None:
                self._nod_start = now
                self._nod_fired = False
        else:
            if (self._nod_start is not None and not self._nod_fired
                    and now - self._nod_start <= NOD_MAX_S):
                self._nods.add(now)
                events.append('nod')
            self._nod_start = None
            self._nod_fired = False

        # --- eyes: classify the closure once it ends ---
        if closed:
            if self._closure_start is None:
                self._closure_start = now
                self._sneeze_fired = False
            closure_s = now - self._closure_start
            # A long closure that coincides with a big mouth movement is a sneeze,
            # not a microsleep. Decide while it is happening so the alert is
            # suppressed rather than retracted afterwards. Fire once per closure.
            if (not self._sneeze_fired and BLINK_MAX_S <= closure_s <= SNEEZE_MAX_S
                    and jaw_dev >= SNEEZE_JAW_DELTA):
                self._sneezes += 1
                self._suppress_until = now + SNEEZE_MAX_S
                events.append('sneeze')
                self._sneeze_fired = True
        else:
            if self._closure_start is not None:
                closure_s = now - self._closure_start
                sneezing = now < self._suppress_until
                if closure_s <= BLINK_MAX_S:
                    self._blinks.add(now)
                    events.append('blink')
                elif closure_s >= MICROSLEEP_MIN_S and not sneezing:
                    self._long_blinks.add(now)
                    events.append('microsleep')
                elif not sneezing:
                    self._long_blinks.add(now)
                    events.append('long_blink')
                self._closure_start = None
            closure_s = 0.0

        suppressed = now < self._suppress_until
        blink_rate = self._blinks.rate(now)
        long_rate = self._long_blinks.rate(now)
        yawn_rate = self._yawns.rate(now)
        nod_rate = self._nods.rate(now)

        def norm(rate, full):
            return min(max(rate / full, 0.0), 1.0)

        score = (W_PERCLOS * min(max(perclos, 0.0), 1.0)
                 + W_LONG_BLINK * norm(long_rate, LONG_BLINK_RATE_FULL)
                 + W_YAWN * norm(yawn_rate, YAWN_RATE_FULL)
                 + W_NOD * norm(nod_rate, NOD_RATE_FULL))
        if suppressed:
            # Do not let an involuntary event drive an alert.
            score = min(score, W_PERCLOS * min(max(perclos, 0.0), 1.0))

        return BehaviorState(
            score=round(min(score, 1.0), 4),
            perclos=round(perclos, 4),
            eye_closed=round(float(p_closed), 4),
            mouth_open=bool(mouth_open),
            head_down=bool(head_down),
            events=events,
            blink_rate=round(blink_rate, 2),
            long_blink_rate=round(long_rate, 2),
            yawn_rate=round(yawn_rate, 2),
            nod_rate=round(nod_rate, 2),
            sneeze_count=self._sneezes,
            suppressed=suppressed,
            closure_s=round(closure_s if closed else 0.0, 3),
            baselines_ready=self._jaw.ready and self._pitch.ready,
        )
