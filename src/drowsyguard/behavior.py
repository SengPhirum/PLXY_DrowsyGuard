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

Three properties of this module are worth stating up front, because each one fixes a
way the previous version was wrong rather than merely imprecise:

  * **A microsleep is announced while the eyes are still shut.** Closures used to be
    classified only on release, so the alarm for "the driver is asleep" waited for the
    driver to wake up. It now fires MICROSLEEP_MIN_S into the closure.
  * **Cues tolerate a brief dropout** (CUE_GAP_S). A single noisy frame used to split
    a 1.5 s closure into two 0.7 s long blinks and lose the microsleep entirely, and
    to split one head nod into two counted nods.
  * **Head-down requires two independent pitch channels to agree.** nose_frac divides
    by the eye-to-mouth distance, so opening the mouth lowers it even with the head
    perfectly still. Measured against the previous version with the head held still, a
    mouth open for 0.5 s or 1.0 s fired a nod and one open for 1.4 s fired a yawn *and*
    a nod - so speech and chewing were being reported as head nods.

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
NOD_MIN_S = 0.30            # shorter than this is landmark jitter, not a nod

# Deviation from the rolling baseline, in baseline-relative units.
JAW_OPEN_DELTA = 0.10       # opening index rise that counts as an open mouth
NOD_PITCH_DELTA = 0.06      # nose_frac drop that counts as the head pitching down
SNEEZE_JAW_DELTA = 0.13     # sneezes involve a pronounced mouth movement

# How long the mouth may already have been open when the eyes closed, for the event
# to still be a sneeze rather than a yawn with the eyes shut.
#
# SNEEZE_JAW_DELTA cannot separate those two on its own, and that is not hypothetical:
# a yawn opens the mouth wide and closes the eyes, so it clears the absolute threshold
# comfortably - and a yawn misread as a sneeze is worse than a missed sneeze, because
# the suppression window then silences a genuine drowsiness cue for SNEEZE_MAX_S.
#
# What differs is the *order* of the two movements, not their size. In a sneeze the
# mouth and the eyes go together, in one reflex; in a yawn the mouth has been open for
# a while by the time the eyes close - YAWN_MIN_S is 1.2 s of continuous opening, so
# any yawn worth the name has a long lead. Measuring the lead directly is cheaper and
# more robust than measuring how fast the mouth moved, and it does not depend on fps.
#
# The obvious alternative - require the opening index to *rise* during the closure -
# was tried and is wrong, for a reason worth keeping: EyeGate's median-of-3 delays the
# closure decision by two frames, so a mouth that opened simultaneously with the eyes
# has already finished opening by the time the closure is declared, and the measured
# rise is zero. It would have rejected precisely the sneezes it was meant to find.
# 0.5 s is comfortably longer than that lag and comfortably shorter than a yawn.
SNEEZE_MOUTH_LEAD_S = 0.50

# Minimum spacing between sneeze *alerts*, as opposed to sneeze detections. One
# sneeze is frequently two or three closures a second apart, each a real detection
# that belongs in the counter; announcing every one of them is noise that trains a
# driver to ignore the speaker. Detection is per closure, the announcement is
# edge-triggered with this cooldown.
SNEEZE_ALERT_COOLDOWN_S = 2.50

# Peak magnitudes an event must reach, as distinct from the threshold that starts it.
# Entering low keeps the measured *duration* honest; the peak gate is what rejects a
# slow drift that never becomes a real yawn or nod.
YAWN_PEAK_DELTA = 0.16
NOD_PEAK_DELTA = 0.10

# The second, mouth-independent pitch channel must agree before the head counts as
# down. nose_norm divides by eye distance, which the jaw cannot change, so unlike
# nose_frac it does not move when the mouth opens. Without it, any mouth movement
# shorter than NOD_MAX_S read as a head nod.
NOD_NORM_DELTA = 0.03

# Hysteresis half-width on the eye-closure decision: the gate closes at
# threshold + this and opens at threshold - this. See EyeGate.
CLOSED_HYSTERESIS = 0.10

# How long a cue may lapse before the event is treated as over. One number for all
# three cues because it is one phenomenon - a brief dropout inside an event that is
# still happening. At 15 fps a microsleep is 15 frames, and one frame reading "open"
# used to end it.
CUE_GAP_S = 0.20

# Weight of mouth *narrowing* relative to jaw drop in the opening index. Five
# landmarks give the mouth corners and nothing else - no lip gap, so no mouth aspect
# ratio - but the corners carry two signals: they drop AND they pull inward as the
# jaw opens wide. Using only the drop threw half the signal away.
MOUTH_NARROW_W = 0.60

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
    nose_norm: float = 0.0     # eye-line to nose distance / eye distance (pitch proxy)
    mouth_ratio: float = 0.0   # mouth corner separation / eye distance
    yaw: float = 0.0           # nose horizontal offset / eye distance (UNVALIDATED)
    eye_dist: float = 0.0


def orient_landmarks(landmarks):
    """Put the eye pair in image order, swapping the mouth corners with them.

    Canonical order expects the right eye at the smaller x, which holds for a frame the
    way a camera sees it and fails for a mirrored one - a selfie-mode webcam, or the
    ESP32 board, whose sensor is mounted upside down so the firmware applies a vertical
    flip and ends up with an upright but horizontally mirrored image.

    This is not cosmetic. A reversed eye pair puts roll at 180 degrees instead of 0,
    face_geometry() then de-rotates by 180 degrees, and every vertical cue changes
    sign: jaw_drop reads -1.2 instead of +1.2 and *falls* as the mouth opens, so
    mouth_open, which wants a rise, can never fire. Measured on the device before this
    existed: roll -170.9, jaw -1.64, on detections scoring 1.00.

    Ordering by position rather than by label is what makes it robust - correct for a
    mirrored frame, an unmirrored one, and any later change to the flip settings. The
    cost is that index 0 means "the image-left eye" rather than the driver's anatomical
    right eye, and nothing downstream cares: jaw drop, mouth width, both pitch channels
    and roll are all symmetric, and the eye crops are two crops either way.
    """
    if not landmarks or len(landmarks) < 5:
        return landmarks
    pts = list(landmarks[:5])
    if pts[0][0] <= pts[1][0]:
        return landmarks
    pts[0], pts[1] = pts[1], pts[0]
    pts[3], pts[4] = pts[4], pts[3]
    return pts + list(landmarks[5:])


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
    # Corner separation in the de-rotated frame, so head tilt does not shorten it.
    ml_r, mr_r = rot((mlx, mly)), rot((mrx, mry))
    mouth_w = math.hypot(ml_r[0] - mr_r[0], ml_r[1] - mr_r[1])
    jaw = mouth_r[1] / eye_dist
    return FaceGeometry(
        valid=True,
        roll=roll,
        jaw_drop=jaw,
        nose_frac=nose_r[1] / mouth_r[1] if abs(mouth_r[1]) > 1e-6 else 0.0,
        nose_norm=nose_r[1] / eye_dist,
        mouth_ratio=mouth_w / eye_dist,
        yaw=nose_r[0] / eye_dist,
        eye_dist=eye_dist,
    )


class EyeGate:
    """Turn a noisy per-frame closure probability into a stable open/closed decision.

    Two mechanisms doing two different jobs:

      median-of-3   removes an isolated frame that flipped. A median rather than an
                    EMA on purpose: an EMA smooths the *edges* of a closure and so
                    distorts its duration, and duration is exactly what
                    MICROSLEEP_MIN_S and BLINK_MAX_S measure. A 3-tap median costs
                    one frame of lag and moves neither edge further than that.
      hysteresis    stops a probability parked on the threshold from emitting a burst
                    of one-frame blinks, each counted as a real blink in the rate.

    Deliberately not applied to PerclosTracker. PERCLOS is a fraction over a window -
    already an averaging operation - so one flipped frame moves it by 1/window.
    Filtering its input would add lag and shift the numbers the risk trigger was
    tuned against, to fix a problem that estimator does not have.
    """

    def __init__(self, threshold=0.5, hysteresis=CLOSED_HYSTERESIS):
        self.enter = float(threshold) + float(hysteresis)
        self.exit = float(threshold) - float(hysteresis)
        self._hist = deque(maxlen=3)
        self._med = 0.0
        self._closed = False

    def update(self, p_closed):
        self._hist.appendleft(float(p_closed))
        h = list(self._hist)
        if len(h) == 1:
            self._med = h[0]
        elif len(h) == 2:
            self._med = min(h)          # conservative until there are three
        else:
            self._med = sorted(h)[1]

        if self._closed:
            if self._med < self.exit:
                self._closed = False
        elif self._med >= self.enter:
            self._closed = True
        return self._closed

    @property
    def smoothed(self):
        return self._med

    @property
    def closed(self):
        return self._closed

    def reset(self):
        self._hist.clear()
        self._med = 0.0
        self._closed = False


class Baseline:
    """Rolling median baseline, so per-driver anatomy cancels out of every cue.

    Note what is deliberately *not* done: the baseline is not frozen while an event
    is in progress. A yawn is at most ~3 s of a 10 s window and this is a median, so
    30% of the window shifting barely moves the 50th percentile. Freezing would buy
    nothing and would deadlock a cue that never falls back below its own threshold.
    """

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
    eye_closed: float = 0.0                         # probability as handed in
    eye_smooth: float = 0.0                         # after EyeGate's median filter
    closed: bool = False                            # the decision events are built on
    mouth_open: bool = False
    head_down: bool = False
    events: list = field(default_factory=list)      # events fired on this frame
    open_index: float = 0.0                         # jaw drop + narrowing, vs baseline
    pitch_dev: float = 0.0                          # nose_frac deviation; -ve is down
    blink_rate: float = 0.0
    long_blink_rate: float = 0.0
    yawn_rate: float = 0.0
    nod_rate: float = 0.0
    sneeze_count: int = 0
    sneeze_alerts: int = 0                          # of those, how many were announced
    sneeze_alert: bool = False                      # edge: announce one now
    suppressed: bool = False                        # sneeze suppression active
    stale: bool = False                             # geometry was held, not detected
    closure_s: float = 0.0                          # current unbroken closure
    baselines_ready: bool = False


class BehaviorAnalyzer:
    """Turn per-frame eye probability + geometry into events and one risk score."""

    def __init__(self, closed_threshold=0.5, fps=30.0):
        self.closed_threshold = float(closed_threshold)
        self.fps = max(float(fps), 1.0)

        self._gate = EyeGate(self.closed_threshold, CLOSED_HYSTERESIS)
        self._jaw = Baseline()
        self._pitch = Baseline()
        self._width = Baseline()
        self._nose = Baseline()

        self._blinks = EventRate()
        self._long_blinks = EventRate()
        self._yawns = EventRate()
        self._nods = EventRate()

        self._t = 0.0
        self._closure_start = None
        self._closure_lapse = None
        self._mouth_start = None
        self._mouth_lapse = None
        self._mouth_peak = 0.0
        self._nod_start = None
        self._nod_lapse = None
        self._nod_peak = 0.0
        self._sneezes = 0
        self._sneeze_alerts = 0
        self._suppress_until = -1.0
        self._sneeze_alert_until = -1.0
        # How long the mouth had already been open when the current closure began,
        # in seconds. Sampled once, at the closure's start, and compared against
        # SNEEZE_MOUTH_LEAD_S. Only meaningful while _closure_start is set.
        self._mouth_lead = 0.0
        self._yawn_fired = False
        self._micro_fired = False
        self._sneeze_fired = False

    def reset(self):
        self._gate.reset()
        for b in (self._jaw, self._pitch, self._width, self._nose):
            b.reset()
        for r in (self._blinks, self._long_blinks, self._yawns, self._nods):
            r.reset()
        self._t = 0.0
        self._closure_start = self._closure_lapse = None
        self._mouth_start = self._mouth_lapse = None
        self._mouth_peak = 0.0
        self._nod_start = self._nod_lapse = None
        self._nod_peak = 0.0
        self._sneezes = 0
        self._sneeze_alerts = 0
        self._suppress_until = -1.0
        self._sneeze_alert_until = -1.0
        self._mouth_lead = 0.0
        self._yawn_fired = self._micro_fired = self._sneeze_fired = False

    def update(self, p_closed, geometry: FaceGeometry, perclos, dt=None, fresh=True):
        """One frame.

        `fresh` is False when the caller is reusing the previous frame's landmarks
        because the detector missed. Time still advances and the eye path still runs -
        the eye crop comes from the current frame - but the geometric cues do not.
        Held landmarks are identical to the last real ones, so feeding them in would
        push duplicate samples into a 10 s baseline window and keep the mouth and nod
        timers running on evidence that no longer exists. A head pitching far enough
        down to lose the detector is precisely when that happened.
        """
        self._t += (1.0 / self.fps) if dt is None else float(dt)
        now = self._t
        events = []

        closed = self._gate.update(p_closed)
        use_geom = bool(geometry.valid and fresh)

        open_index = pitch_dev = nose_dev = 0.0
        if use_geom:
            jaw_dev = self._jaw.update(geometry.jaw_drop)
            width_dev = self._width.update(geometry.mouth_ratio)
            pitch_dev = self._pitch.update(geometry.nose_frac)
            nose_dev = self._nose.update(geometry.nose_norm)
            # Both halves of the mouth signal in one number: the corners drop
            # (jaw_dev up) and close in (width_dev down), so the terms add.
            open_index = jaw_dev - MOUTH_NARROW_W * width_dev

        mouth_open = use_geom and open_index >= JAW_OPEN_DELTA
        # Two channels, both required: nose_frac alone cannot tell a head that
        # dropped from a mouth that opened, because opening the mouth grows its
        # denominator.
        head_down = (use_geom and pitch_dev <= -NOD_PITCH_DELTA
                     and nose_dev <= -NOD_NORM_DELTA)

        if use_geom:
            # --- mouth: a sustained, pronounced opening is a yawn ---
            if mouth_open:
                self._mouth_lapse = None
                if self._mouth_start is None:
                    self._mouth_start = now
                    self._mouth_peak = 0.0
                    self._yawn_fired = False
                self._mouth_peak = max(self._mouth_peak, open_index)
                # Duration says "not speech"; the peak says "not a slow drift".
                # Re-checked every frame, so a yawn that widens late still fires.
                if (not self._yawn_fired and now - self._mouth_start >= YAWN_MIN_S
                        and self._mouth_peak >= YAWN_PEAK_DELTA):
                    self._yawns.add(now)
                    events.append('yawn')
                    self._yawn_fired = True
            elif self._mouth_start is not None:
                if self._mouth_lapse is None:
                    self._mouth_lapse = now
                if now - self._mouth_lapse >= CUE_GAP_S:
                    self._mouth_start = self._mouth_lapse = None
                    self._mouth_peak = 0.0
                    self._yawn_fired = False

            # --- head: a down-and-back excursion is a nod ---
            if head_down:
                self._nod_lapse = None
                if self._nod_start is None:
                    self._nod_start = now
                    self._nod_peak = 0.0
                self._nod_peak = max(self._nod_peak, -pitch_dev)
            elif self._nod_start is not None:
                if self._nod_lapse is None:
                    self._nod_lapse = now
                if now - self._nod_lapse >= CUE_GAP_S:
                    # Measured to where the head came back up, not to where the
                    # tolerance expired.
                    dur = self._nod_lapse - self._nod_start
                    if (NOD_MIN_S <= dur <= NOD_MAX_S
                            and self._nod_peak >= NOD_PEAK_DELTA):
                        self._nods.add(now)
                        events.append('nod')
                    self._nod_start = self._nod_lapse = None
                    self._nod_peak = 0.0

        # --- eyes ---
        closure_s = 0.0
        sneeze_alert = False
        if closed:
            self._closure_lapse = None
            if self._closure_start is None:
                self._closure_start = now
                # How long the mouth had already been open when the eyes shut. This
                # is what separates a sneeze from a yawn that also closes the eyes:
                # in a yawn the mouth has been wide for a second by now. Sampled
                # once, here, because by the time the sneeze window opens at
                # BLINK_MAX_S the mouth episode may already have ended.
                self._mouth_lead = (now - self._mouth_start
                                    if self._mouth_start is not None else 0.0)
                self._sneeze_fired = False
                self._micro_fired = False
            closure_s = now - self._closure_start

            # A long closure with the mouth flung open at the same moment is a
            # sneeze, not a microsleep. Decided while it is happening so the alert is
            # suppressed rather than retracted afterwards, and fired once per closure.
            # Three conditions, each rejecting a different impostor: the duration
            # window rejects blinks and real microsleeps, the absolute level rejects a
            # closed mouth, and the lead rejects a yawn.
            if (not self._sneeze_fired and BLINK_MAX_S <= closure_s <= SNEEZE_MAX_S
                    and open_index >= SNEEZE_JAW_DELTA
                    and self._mouth_lead <= SNEEZE_MOUTH_LEAD_S):
                self._sneezes += 1
                self._suppress_until = now + SNEEZE_MAX_S
                events.append('sneeze')
                self._sneeze_fired = True

                # The announcement is a separate decision from the detection. One
                # sneeze is often several closures in a row, each a real detection;
                # the cooldown turns that into one alert and leaves the counter honest.
                if now >= self._sneeze_alert_until:
                    self._sneeze_alert_until = now + SNEEZE_ALERT_COOLDOWN_S
                    self._sneeze_alerts += 1
                    sneeze_alert = True

            # Microsleep, announced WHILE THE EYES ARE STILL SHUT.
            #
            # Closures used to be classified only on release, so the alarm for "the
            # driver is asleep" was gated on the driver waking up: eyes shut for five
            # seconds produced five seconds of silence. It now fires at the threshold.
            # The wait is longer only when the mouth says this could still be a
            # sneeze - a sneeze resolves inside SNEEZE_MAX_S, so surviving that long
            # is itself the proof that it was not one.
            need = SNEEZE_MAX_S if open_index >= SNEEZE_JAW_DELTA else MICROSLEEP_MIN_S
            if not self._micro_fired and closure_s >= need and now >= self._suppress_until:
                self._long_blinks.add(now)
                events.append('microsleep')
                self._micro_fired = True
        elif self._closure_start is not None:
            if self._closure_lapse is None:
                self._closure_lapse = now
            if now - self._closure_lapse >= CUE_GAP_S:
                # Duration ends where the eyes reopened, not where the tolerance
                # expired - otherwise the tolerance would itself promote a 0.9 s
                # long blink into a 1.1 s microsleep.
                dur = self._closure_lapse - self._closure_start
                sneezing = self._closure_lapse < self._suppress_until
                if dur <= BLINK_MAX_S:
                    self._blinks.add(now)
                    events.append('blink')
                elif not self._micro_fired and not sneezing:
                    # Between a blink and a microsleep, or a microsleep that was
                    # suppressed while it happened. Counts toward the long-blink rate
                    # either way; _micro_fired stops it being counted twice.
                    self._long_blinks.add(now)
                    events.append('long_blink')
                self._closure_start = self._closure_lapse = None
            else:
                closure_s = self._closure_lapse - self._closure_start   # frozen

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
            eye_smooth=round(self._gate.smoothed, 4),
            closed=bool(closed),
            mouth_open=bool(mouth_open),
            head_down=bool(head_down),
            events=events,
            open_index=round(open_index, 4),
            pitch_dev=round(pitch_dev, 4),
            blink_rate=round(blink_rate, 2),
            long_blink_rate=round(long_rate, 2),
            yawn_rate=round(yawn_rate, 2),
            nod_rate=round(nod_rate, 2),
            sneeze_count=self._sneezes,
            sneeze_alerts=self._sneeze_alerts,
            sneeze_alert=sneeze_alert,
            suppressed=suppressed,
            stale=bool(geometry.valid and not fresh),
            closure_s=round(closure_s, 3),
            baselines_ready=(self._jaw.ready and self._pitch.ready
                             and self._width.ready and self._nose.ready),
        )
