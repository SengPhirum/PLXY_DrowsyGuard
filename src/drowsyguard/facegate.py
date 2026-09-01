"""Desktop mirror of ``firmware/esp32s3/main/face_gate.{h,cpp}``.

Which detection to trust, and when to believe a driver is really there. The firmware
file is the reference implementation and carries the full reasoning for every
constant and every check; this module exists so the dashboard applies *the same*
decisions, because thresholds tuned on a laptop and enforced only on a laptop are
worth nothing.

``tests/test_facegate_parity.py`` compiles the C++ and drives both through the same
sequences, asserting the same verdict, the same reject reason and the same presence
decision at every step. That is a stronger guarantee than matching constants, and it
is the reason this file is a transcription rather than a reinterpretation: where a
Python idiom would read better but behave even slightly differently at a boundary,
the C++ shape wins.

Two kinds of check, and keeping them apart is the design:

* :func:`check` is *static* - one candidate, on its own. Confidence, box size and
  shape, then the five landmarks: eyes level-ish, mouth below them, nose between
  them and inside the eye pair, mouth narrower than the head. It is cheap, it is
  order-dependent (the first failure is the one reported), and it cannot see time.

* :class:`FaceTrack` is *temporal*. It requires consecutive detections to agree
  before calling a driver present, refuses a candidate that has moved further than a
  head can move between detections, holds the last box briefly when the detector
  misses, and - the part that is easy to get wrong - never lets a new track inherit
  the old one's confirmation. The last property is what stops a track sliding off
  the driver and onto a passenger.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from .behavior import FaceGeometry, face_geometry

# --- confidence -------------------------------------------------------------
FACE_MIN_SCORE = 0.55

# --- plausibility limits ----------------------------------------------------
FACE_EYE_DIST_MIN_FRAC = 0.15
FACE_EYE_DIST_MAX_FRAC = 0.95
FACE_MAX_ROLL_DEG = 45.0
FACE_JAW_MIN = 0.35
FACE_JAW_MAX = 2.60
FACE_NOSE_FRAC_MIN = 0.15
FACE_NOSE_FRAC_MAX = 1.15
FACE_YAW_MAX = 0.75
FACE_MOUTH_MIN = 0.25
FACE_MOUTH_MAX = 1.60

# --- box size and shape -----------------------------------------------------
FACE_MIN_SIDE_FRAC = 0.10
FACE_MAX_SIDE_FRAC = 0.95
FACE_ASPECT_MIN = 0.55
FACE_ASPECT_MAX = 1.80
FACE_KP_MARGIN_FRAC = 0.30

# --- temporal consistency ---------------------------------------------------
FACE_CONFIRM_DETECTIONS = 2
FACE_HOLD_DETECTIONS = 5
FACE_JUMP_MAX_FRAC = 1.20
FACE_SCALE_MAX_RATIO = 1.80
FACE_REACQUIRE_AFTER = 2

# --- region of interest -----------------------------------------------------
FACE_TRACK_MIN_IOU = 0.15


class FaceReject(Enum):
    """Why a candidate was not believed.

    The string values are the firmware's ``face_gate_reject_name()`` output verbatim,
    so a rejection reason read off the device's status page and one printed by the
    dashboard are the same token and can be grepped together.
    """

    NONE = 'ok'
    NO_LANDMARKS = 'no-landmarks'
    LOW_SCORE = 'score-too-low'
    TOO_SMALL = 'face-too-small'
    TOO_LARGE = 'face-too-large'
    ASPECT = 'box-not-head-shaped'
    OUTSIDE_BOX = 'landmarks-outside-box'
    DEGENERATE = 'degenerate-eye-distance'
    EYE_DIST_SMALL = 'eye-distance-too-small'
    EYE_DIST_LARGE = 'eye-distance-too-large'
    ROLL = 'roll-too-steep'
    JAW_SMALL = 'mouth-not-below-eyes'
    JAW_LARGE = 'mouth-too-far-below-eyes'
    NOSE_HIGH = 'nose-above-eye-line'
    NOSE_LOW = 'nose-at-or-below-mouth'
    YAW = 'nose-outside-eye-pair'
    MOUTH_NARROW = 'mouth-too-narrow'
    MOUTH_WIDE = 'mouth-too-wide'
    DISCONTINUOUS = 'moved-too-far'


@dataclass(frozen=True)
class Box:
    """A face box in frame pixels, as (x, y, w, h). Not square; that is the point.

    ``FaceTracker`` downstream turns the accepted box into a square crop, but the
    gate has to see the detector's own rectangle - squaring it first would destroy
    the aspect-ratio check, which is one of the two things that reject a hand.
    """

    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    valid: bool = False

    @property
    def cx(self):
        return self.x + self.w / 2.0

    @property
    def cy(self):
        return self.y + self.h / 2.0

    @property
    def side(self):
        """Mean of the two sides. What continuity is measured in units of."""
        return 0.5 * (self.w + self.h)

    def as_square(self):
        """(x0, y0, side) square crop, the shape the eye-patch code expects."""
        side = max(self.w, self.h)
        return (int(round(self.cx - side / 2.0)),
                int(round(self.cy - side / 2.0)),
                int(round(side)))


def iou(a: Box, b: Box) -> float:
    if not a.valid or not b.valid or a.w <= 0 or a.h <= 0 or b.w <= 0 or b.h <= 0:
        return 0.0
    x0, y0 = max(a.x, b.x), max(a.y, b.y)
    x1, y1 = min(a.x + a.w, b.x + b.w), min(a.y + a.h, b.y + b.h)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


def check(landmarks, box: Box, score: float, frame_w=0, frame_h=0):
    """Static plausibility. Returns ``(FaceReject, FaceGeometry)``.

    The geometry is returned alongside the verdict rather than recomputed by the
    caller, because a rejection you cannot see the numbers behind is not actionable -
    that lesson cost this project a debugging session in which the gate rejected
    100% of real faces and reported only a count.

    ``frame_w``/``frame_h`` of 0 skip the two frame-relative size checks, which is
    the honest response to a caller that has no frame rather than inventing one.
    """
    empty = FaceGeometry()
    if not landmarks or len(landmarks) < 5 or not box.valid or box.w <= 0 or box.h <= 0:
        return FaceReject.NO_LANDMARKS, empty

    # NaN-safe by construction: `not (score >= t)` is true for NaN, where
    # `score < t` would be false and let it through.
    if not (score >= FACE_MIN_SCORE):
        return FaceReject.LOW_SCORE, empty

    if frame_w > 0 and frame_h > 0:
        shortest = float(min(frame_w, frame_h))
        side = max(box.w, box.h)
        if side < FACE_MIN_SIDE_FRAC * shortest:
            return FaceReject.TOO_SMALL, empty
        if side > FACE_MAX_SIDE_FRAC * shortest:
            return FaceReject.TOO_LARGE, empty

    aspect = box.w / box.h
    if aspect < FACE_ASPECT_MIN or aspect > FACE_ASPECT_MAX:
        return FaceReject.ASPECT, empty

    mx, my = box.w * FACE_KP_MARGIN_FRAC, box.h * FACE_KP_MARGIN_FRAC
    x_lo, x_hi = box.x - mx, box.x + box.w + mx
    y_lo, y_hi = box.y - my, box.y + box.h + my
    for px, py in landmarks[:5]:
        if px < x_lo or px > x_hi or py < y_lo or py > y_hi:
            return FaceReject.OUTSIDE_BOX, empty

    g = face_geometry(landmarks)
    if not g.valid:
        return FaceReject.DEGENERATE, g

    if g.eye_dist < FACE_EYE_DIST_MIN_FRAC * box.w:
        return FaceReject.EYE_DIST_SMALL, g
    if g.eye_dist > FACE_EYE_DIST_MAX_FRAC * box.w:
        return FaceReject.EYE_DIST_LARGE, g
    if abs(g.roll) > FACE_MAX_ROLL_DEG:
        return FaceReject.ROLL, g
    if g.jaw_drop < FACE_JAW_MIN:
        return FaceReject.JAW_SMALL, g
    if g.jaw_drop > FACE_JAW_MAX:
        return FaceReject.JAW_LARGE, g
    if g.nose_frac < FACE_NOSE_FRAC_MIN:
        return FaceReject.NOSE_HIGH, g
    if g.nose_frac > FACE_NOSE_FRAC_MAX:
        return FaceReject.NOSE_LOW, g
    if abs(g.yaw) > FACE_YAW_MAX:
        return FaceReject.YAW, g
    if g.mouth_ratio < FACE_MOUTH_MIN:
        return FaceReject.MOUTH_NARROW, g
    if g.mouth_ratio > FACE_MOUTH_MAX:
        return FaceReject.MOUTH_WIDE, g
    return FaceReject.NONE, g


def plausible(landmarks, box: Box, score: float, frame_w=0, frame_h=0) -> bool:
    return check(landmarks, box, score, frame_w, frame_h)[0] is FaceReject.NONE


def continuous(prev: Box, cand: Box) -> bool:
    """Could `cand` be the same face as `prev`, one detection interval later?

    Scale-free in both terms, so it behaves identically for a driver sitting close
    and one sitting back. An invalid `prev` answers True - there is nothing to be
    discontinuous with, and answering False would need a special case at every call
    site to let a first detection through.
    """
    if not prev.valid or prev.w <= 0 or prev.h <= 0:
        return True
    if not cand.valid or cand.w <= 0 or cand.h <= 0:
        return False
    mean = 0.5 * (prev.side + cand.side)
    if mean <= 0:
        return False
    if math.hypot(cand.cx - prev.cx, cand.cy - prev.cy) > FACE_JUMP_MAX_FRAC * mean:
        return False
    ratio = (prev.side / cand.side) if prev.side > cand.side else (cand.side / prev.side)
    return ratio <= FACE_SCALE_MAX_RATIO


def pick(candidates, track: Box, frame_w=0, frame_h=0) -> int:
    """Which candidate is the driver. `candidates` is [(Box, landmarks, score), ...].

    Continuity first, size only as the opening guess: with a live track the right
    answer is the box that overlaps it, because a passenger leaning forward is a
    bigger face than the driver and "largest wins" hands them the alarm. Returns the
    index, or -1.
    """
    best, best_iou = -1, 0.0
    biggest, biggest_area = -1, 0.0
    for i, (box, lms, score) in enumerate(candidates):
        if not plausible(lms, box, score, frame_w, frame_h):
            continue
        area = box.w * box.h
        if area > biggest_area:
            biggest_area, biggest = area, i
        overlap = iou(box, track)
        if overlap > best_iou:
            best_iou, best = overlap, i
    if best >= 0 and best_iou >= FACE_TRACK_MIN_IOU:
        return best
    return biggest


@dataclass
class TrackResult:
    present: bool = False
    fresh: bool = False
    confirmed: bool = False
    acquired: bool = False
    lost: bool = False
    reject: FaceReject = FaceReject.NONE
    misses: int = 0
    agreed: int = 0
    box: Box = field(default_factory=Box)
    landmarks: list = field(default_factory=list)


class FaceTrack:
    """The driver, across time. Mirror of ``FaceTrack`` in face_gate.h."""

    def __init__(self):
        self.reset()

    def reset(self):
        self._box = Box()
        self._lm = []
        self._agreed = 0
        self._misses = 0
        self._confirmed = False

    @property
    def present(self) -> bool:
        return self._confirmed and self._misses <= FACE_HOLD_DETECTIONS

    @property
    def box(self) -> Box:
        return self._box

    @property
    def landmarks(self):
        return list(self._lm)

    def _snapshot(self) -> TrackResult:
        return TrackResult(present=self.present, confirmed=self._confirmed,
                           misses=self._misses, agreed=self._agreed,
                           box=self._box, landmarks=list(self._lm))

    def peek(self) -> TrackResult:
        """State without offering a detection.

        Frames between detection attempts still pass, but they are not evidence
        either way, so this never expires the hold: the hold is counted in attempts,
        which keeps it independent of how often the caller chooses to detect.
        """
        return self._snapshot()

    def update(self, have, box: Box, landmarks, score, frame_w=0, frame_h=0) -> TrackResult:
        """One detection *attempt*. `have` is False when the detector found nothing."""
        was_present = self.present

        why = FaceReject.NONE
        accept = False
        # Whether this candidate could be the same face as the tracked one. Computed
        # unconditionally, because it answers a question about the world; how much
        # weight to give it is a separate decision, taken twice below.
        same_face = False
        if have:
            why = check(landmarks, box, score, frame_w, frame_h)[0]
            if why is FaceReject.NONE:
                same_face = continuous(self._box, box)
                # While the track is warm, a candidate somewhere else is a different
                # object and is refused outright - that is what stops a passenger
                # taking over a live track. After FACE_REACQUIRE_AFTER misses the
                # driver may genuinely have moved, so it is allowed in.
                warm = self._box.valid and self._misses < FACE_REACQUIRE_AFTER
                if warm and not same_face:
                    why = FaceReject.DISCONTINUOUS
                else:
                    accept = True

        if accept:
            # A candidate that does not line up with what we had starts a NEW track
            # rather than inheriting the old one's confirmation. Presence is re-earned
            # across a discontinuity, never inherited.
            #
            # A face that reappears where it was, after any number of misses inside
            # the hold, DOES continue the track: ending it there would drop presence
            # every time the detector blinked, which is the moment the hold exists for.
            if self._box.valid and not same_face:
                self._agreed = 0
                self._confirmed = False
            if self._agreed < FACE_CONFIRM_DETECTIONS:
                self._agreed += 1
            self._box = box
            self._lm = list(landmarks[:5])
            self._misses = 0
            if self._agreed >= FACE_CONFIRM_DETECTIONS:
                self._confirmed = True
        else:
            self._misses += 1
            if not self._confirmed:
                # A pending track has nothing to hold: it was never believed, so one
                # miss ends it rather than leaving a half-formed track for an
                # unrelated candidate to resume.
                self._agreed = 0
                self._box = Box()
                self._lm = []
            elif self._misses > FACE_HOLD_DETECTIONS:
                self._confirmed = False
                self._agreed = 0
                self._box = Box()
                self._lm = []

        out = self._snapshot()
        out.fresh = accept
        out.reject = FaceReject.NONE if accept else why
        out.acquired = out.present and not was_present
        out.lost = was_present and not out.present
        return out
