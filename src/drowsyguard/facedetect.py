"""Face detection and gated short-term tracking for the live dashboard.

Uses OpenCV's YuNet detector. OpenCV 5 removed `CascadeClassifier`, so Haar is not
an option; the YuNet ONNX file is fetched once by `drowsyguard fetch-models`.

Framing note: measured over 400 DDD images, the detected face box is ~1.02x the
image side, i.e. DDD crops are extremely tight. So the default margin is 0 - the
detected box *is* the training framing. Raising the margin moves the live input out
of the training distribution.

**What changed, and why it is not just tidying.** This used to take the
highest-scoring box YuNet returned and believe it. That is fine when the only thing
in frame is a face and wrong in every other case: a hand, a headrest, a phone or a
patch of low-light noise all produce boxes, and a single believed frame is enough to
start feeding a baseline and a PERCLOS window with measurements of an object that is
not a person. Every detection now goes through :mod:`drowsyguard.facegate`, which is
a transcription of the firmware's own gate - confidence, box size and shape, the
five landmarks' geometry, and then agreement across consecutive detections. The
dashboard is where thresholds get tuned, so the dashboard has to apply the same ones
the device will.

The raw detector is injectable (`detect_fn`). That is what makes the gate testable:
the interesting sequences - a hand for two seconds, an empty frame, occlusion, a
passenger appearing mid-track, a face that halves in size between frames - can be
written down exactly instead of being acted out in front of a webcam and hoped for.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .facegate import Box, FaceReject, FaceTrack, check, pick

MODEL_FILENAME = 'face_detection_yunet_2023mar.onnx'
MODEL_URL = ('https://github.com/opencv/opencv_zoo/raw/main/models/'
             'face_detection_yunet/face_detection_yunet_2023mar.onnx')
DEFAULT_MODEL_DIR = Path('models/detectors')


@dataclass
class FaceResult:
    box: tuple | None      # (x, y, side) square crop in frame coords, or None
    found: bool            # a detection was accepted on this frame
    held: bool             # no fresh detection; reusing the last accepted box
    score: float
    landmarks: list        # [(x, y), ...] five points, empty when not present
    # A *confirmed* driver, which is not the same as `found`: `found` is about this
    # frame, `present` is about whether the gate believes there is a person there at
    # all. Everything downstream that measures a driver should key off this one.
    present: bool = False
    # Why the best candidate was refused, as the firmware's own reject token. Empty
    # when a detection was accepted or when there was nothing to refuse.
    reject: str = ''
    # Candidates the static gate threw out this frame. Distinguishes "nothing in
    # frame" from "something in frame that is not a face", which look identical from
    # `found=False` alone and have completely different causes.
    rejected: int = 0


def default_model_path(root=None):
    base = Path(root) if root else Path.cwd()
    return base / DEFAULT_MODEL_DIR / MODEL_FILENAME


def fetch_model(dest=None, url=MODEL_URL):
    """Download the YuNet model. Returns the path written."""
    import urllib.request

    path = Path(dest) if dest else default_model_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, path)
    return path


@dataclass
class RawDetection:
    """One candidate straight from the detector, before any judgement."""

    box: Box
    landmarks: list = field(default_factory=list)
    score: float = 0.0


class FaceTracker:
    """Detect a face per frame, gate it, smooth the crop, and hold it briefly.

    Holding matters for drowsiness work: a detector often drops the face exactly
    when the eyes close or the head nods, which is the moment of interest. Without
    a hold the crop would snap back to the whole frame at that instant. What the
    hold must NOT do is keep a face alive indefinitely, which is why FaceTrack counts
    it in detection attempts and gives up at FACE_HOLD_DETECTIONS.

    `detect_fn(frame) -> [RawDetection, ...]` replaces YuNet, for tests. When it is
    given, `model_path` is not touched and OpenCV is never imported.
    """

    def __init__(self, model_path=None, margin=0.0, smooth=0.6, hold_frames=None,
                 score_threshold=0.6, detect_every=1, detect_fn=None):
        self.margin = float(margin)
        self.smooth = min(max(float(smooth), 0.0), 0.95)
        self.detect_every = max(1, int(detect_every))
        self.model_path = str(model_path) if model_path is not None else None
        # hold_frames is accepted for backwards compatibility and deliberately
        # ignored: the hold is FACE_HOLD_DETECTIONS in facegate, shared with the
        # firmware, and a second knob here would let the two silently disagree.
        self.hold_frames = hold_frames

        self._detect_fn = detect_fn
        self._cv2 = None
        self._det = None
        if detect_fn is None:
            import cv2

            self._cv2 = cv2
            self._det = cv2.FaceDetectorYN.create(self.model_path, '', (320, 320),
                                                  float(score_threshold), 0.3, 5000)
        self._size = None
        self._state = None       # smoothed (cx, cy, side) of the square crop
        self._frame_no = 0
        self._track = FaceTrack()
        self._last_score = 0.0

    def reset(self):
        self._state = None
        self._track.reset()
        self._last_score = 0.0

    # -- raw detection -----------------------------------------------------
    def _detect(self, frame):
        if self._detect_fn is not None:
            return list(self._detect_fn(frame))

        h, w = frame.shape[:2]
        if self._size != (w, h):
            self._det.setInputSize((w, h))
            self._size = (w, h)
        _, faces = self._det.detect(frame)
        out = []
        if faces is None:
            return out
        for f in faces:
            x, y, bw, bh = (float(v) for v in f[:4])
            pts = [(float(f[4 + 2 * i]), float(f[5 + 2 * i])) for i in range(5)]
            out.append(RawDetection(Box(x, y, bw, bh, True), pts, float(f[-1])))
        return out

    # -- one frame ---------------------------------------------------------
    def update(self, frame):
        h, w = frame.shape[:2]
        self._frame_no += 1

        # Between detection intervals nothing new is known, so the track is read
        # rather than advanced. Advancing it would make the hold depend on
        # detect_every, i.e. speeding the loop up would shorten how long a face is
        # held for - a coupling nobody would expect from a performance knob.
        if self._frame_no % self.detect_every != 0:
            return self._result(self._track.peek(), w, h, fresh=False, rejected=0)

        cands = self._detect(frame)
        chosen = -1
        rejected = 0
        # Why the candidates were refused, when they all were. Without this a hand in
        # front of the lens and an empty cabin produce identical output, and they are
        # the two cases an operator most needs to tell apart: one is a person doing
        # something, the other is a device seeing nothing.
        hint = ''
        if cands:
            triples = [(c.box, c.landmarks, c.score) for c in cands]
            chosen = pick(triples, self._track.box, w, h)
            for box, lms, score in triples:
                why = check(lms, box, score, w, h)[0]
                if why is not FaceReject.NONE:
                    rejected += 1
                    if not hint:
                        # The first failing candidate, matching what the firmware's
                        # ModelDetectStats reports, so the two logs read the same.
                        hint = why.value

        if chosen >= 0:
            c = cands[chosen]
            tr = self._track.update(True, c.box, c.landmarks, c.score, w, h)
            if tr.fresh:
                self._last_score = c.score
        else:
            tr = self._track.update(False, Box(), [], 0.0, w, h)

        if tr.lost:
            self._state = None
            self._last_score = 0.0
        return self._result(tr, w, h, fresh=tr.fresh, rejected=rejected, hint=hint)

    def _result(self, tr, w, h, fresh, rejected, hint=''):
        # The track's own reason wins when it has one - it is the more specific
        # answer, because it knows about continuity and the static check does not.
        reject = tr.reject.value if tr.reject is not FaceReject.NONE else hint
        if not tr.present:
            # `found` still reports whether a detection was accepted, even here. A
            # track can accept a candidate and remain unconfirmed - that is exactly
            # what the first detection of a new driver looks like - and collapsing
            # the two would make acquisition invisible from outside.
            return FaceResult(None, bool(fresh), False, 0.0, [], present=False,
                              reject=reject, rejected=rejected)
        if fresh:
            self._smooth_to(tr.box)
        box = self._square(w, h) if self._state is not None else None
        return FaceResult(box, bool(fresh), not fresh, self._last_score,
                          list(tr.landmarks), present=True,
                          reject=reject, rejected=rejected)

    def _smooth_to(self, box: Box):
        cx, cy = box.cx, box.cy
        side = max(box.w, box.h) * (1.0 + self.margin)
        if self._state is None:
            self._state = (cx, cy, side)
        else:
            a = self.smooth
            pcx, pcy, pside = self._state
            self._state = (a * pcx + (1 - a) * cx,
                           a * pcy + (1 - a) * cy,
                           a * pside + (1 - a) * side)

    def _square(self, w, h):
        cx, cy, side = self._state
        side = max(16.0, min(side, float(min(w, h))))
        x0 = int(round(min(max(cx - side / 2.0, 0.0), w - side)))
        y0 = int(round(min(max(cy - side / 2.0, 0.0), h - side)))
        return (x0, y0, int(round(side)))
