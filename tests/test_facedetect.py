"""Face tracker behaviour: gating, confirmation, hold-then-lose, and reacquisition.

Two halves, and the split is deliberate.

**With an injected detector** (most of this file). `FaceTracker` takes a `detect_fn`,
so the sequences that matter can be written down instead of acted out: a hand for two
seconds, an empty frame, a driver occluded for exactly the length of the hold, a
passenger appearing mid-track, a face that halves in size between frames, twelve
frames of low-confidence detections. None of those can be produced reliably in front
of a webcam, and a test that depends on producing them is a test that gets deleted.

**With the real YuNet model** (the last section). Those tests need a face image, and
this repository ships none - a dataset of driver faces is not something to commit - so
they skip unless `data/processed/` has been prepared. They exist to check the wiring
between OpenCV's output format and the gate, which the injected path by construction
cannot.

The gating logic itself is tested exhaustively, and against the firmware, in
tests/test_face_gate.py and tests/test_facegate_parity.py. What is tested here is
`FaceTracker`: that it feeds the gate correctly, smooths only what it should, and
reports a crop the eye-patch code can use.
"""
import numpy as np
import pytest

from drowsyguard.facedetect import FaceTracker, RawDetection, default_model_path
from drowsyguard.facegate import (FACE_CONFIRM_DETECTIONS, FACE_HOLD_DETECTIONS,
                                  FACE_REACQUIRE_AFTER, Box)

FRAME_W = FRAME_H = 240
GOOD_SCORE = 0.95


def blank(w=FRAME_W, h=FRAME_H):
    return np.full((h, w, 3), 120, np.uint8)


def face_points(cx, cy, eye_dist=60.0, jaw=1.05, nose_frac=0.55, mouth_w=0.60, yaw=0.0):
    half = eye_dist / 2.0
    my = jaw * eye_dist
    mx = eye_dist * mouth_w / 2.0
    return [(cx - half, cy), (cx + half, cy),
            (cx + yaw * eye_dist, cy + nose_frac * my),
            (cx - mx, cy + my), (cx + mx, cy + my)]


def box_around(points, pad=0.25):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    return Box(min(xs) - w * pad, min(ys) - h * pad,
               w * (1 + 2 * pad), h * (1 + 2 * pad), True)


def face(cx=120, cy=120, eye_dist=60.0, score=GOOD_SCORE, **kw):
    pts = face_points(cx, cy, eye_dist=eye_dist, **kw)
    return RawDetection(box_around(pts), pts, score)


def hand(cx=120, cy=120):
    """A raised hand: a tall narrow box, and a "nose" outside the "eyes"."""
    pts = face_points(cx, cy, eye_dist=40.0, yaw=1.4)
    return RawDetection(Box(cx - 18, cy - 55, 36, 110, True), pts, 0.99)


class Scripted:
    """A detector that returns a fixed list of candidates per call."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def __call__(self, frame):
        out = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return out


def run(script, **kw):
    """Drive a tracker through one detector script and return every FaceResult."""
    tracker = FaceTracker(detect_fn=Scripted(script), **kw)
    return [tracker.update(blank()) for _ in script]


# --------------------------------------------------------------------------- #
# a real face
# --------------------------------------------------------------------------- #

def test_one_detection_is_not_yet_a_driver():
    """The property the old version could not express. A single believed frame is
    enough to push a measurement of a headrest into a PERCLOS window."""
    r = run([[face()]])[0]
    assert not r.present and r.box is None


def test_two_agreeing_detections_produce_a_usable_crop():
    results = run([[face()]] * FACE_CONFIRM_DETECTIONS)
    r = results[-1]
    assert r.present and r.found and r.box is not None
    x, y, side = r.box
    assert side > 0
    assert 0 <= x and 0 <= y
    assert x + side <= FRAME_W and y + side <= FRAME_H
    assert len(r.landmarks) == 5


def test_the_crop_is_square_and_centred_on_the_face():
    results = run([[face(cx=100, cy=130, eye_dist=50.0)]] * 4, smooth=0.0)
    x, y, side = results[-1].box
    assert x + side / 2 == pytest.approx(100, abs=3)
    assert y + side / 2 == pytest.approx(130 + 0.5 * 1.05 * 50.0, abs=8)


def test_margin_enlarges_the_crop():
    tight = run([[face()]] * 3, margin=0.0, smooth=0.0)[-1]
    wide = run([[face()]] * 3, margin=0.6, smooth=0.0)[-1]
    assert tight.present and wide.present
    assert wide.box[2] > tight.box[2]


# --------------------------------------------------------------------------- #
# things that are not a driver
# --------------------------------------------------------------------------- #

def test_a_blank_frame_yields_no_box():
    r = run([[]])[0]
    assert r.box is None and not r.found and not r.held and not r.present


def test_ten_empty_frames_never_produce_a_driver():
    for r in run([[]] * 10):
        assert not r.present and r.box is None


def test_a_hand_never_becomes_a_driver():
    results = run([[hand()]] * 10)
    assert not any(r.present for r in results)
    assert all(r.box is None for r in results)
    assert {r.reject for r in results} == {'box-not-head-shaped'}
    assert all(r.rejected == 1 for r in results), 'and it is counted as a rejection'


def test_low_light_detections_are_refused():
    """Low light collapses this detector's confidence rather than distorting its
    geometry - it still emits roughly face-shaped boxes, because the coarse stage runs
    at a deliberately loose threshold. The score is what separates them."""
    rng = np.random.default_rng(3)
    script = [[face(cx=120 + rng.uniform(-6, 6), cy=120 + rng.uniform(-6, 6),
                    score=float(rng.uniform(0.1, 0.5)))] for _ in range(12)]
    for r in run(script):
        assert not r.present
        assert r.reject == 'score-too-low'


def test_an_empty_frame_and_a_rejected_candidate_are_distinguishable():
    """`found=False` alone cannot tell "nothing there" from "something there that is
    not a face", and the two have completely different causes."""
    nothing = run([[]])[0]
    something = run([[hand()]])[0]
    assert nothing.rejected == 0 and nothing.reject == ''
    assert something.rejected == 1 and something.reject != ''


# --------------------------------------------------------------------------- #
# holding, losing, reacquiring
# --------------------------------------------------------------------------- #

def test_holds_the_crop_through_a_brief_loss_then_reports_lost():
    script = ([[face()]] * FACE_CONFIRM_DETECTIONS
              + [[]] * (FACE_HOLD_DETECTIONS + 1))
    results = run(script)
    held = results[FACE_CONFIRM_DETECTIONS:-1]
    assert all(r.held and r.present and r.box is not None for r in held), (
        'the hold is what covers the moment the eyes close')
    assert not results[-1].present and results[-1].box is None


def test_the_crop_does_not_move_while_it_is_held():
    script = [[face()]] * 3 + [[]] * 3
    results = run(script, smooth=0.0)
    boxes = {r.box for r in results[3:]}
    assert len(boxes) == 1, 'a held crop is the last real one, not a drifting guess'


def test_a_driver_returning_after_a_brief_loss_keeps_the_same_track():
    script = ([[face()]] * 3 + [[]] * 3 + [[face()]])
    results = run(script)
    assert results[-1].present and results[-1].found


def test_a_face_somewhere_else_does_not_take_over_a_live_track():
    script = [[face(cx=70)]] * 3 + [[face(cx=205)]]
    results = run(script)
    assert not results[-1].found
    assert results[-1].reject == 'moved-too-far'
    assert results[-1].present, 'the real driver is still held'


def test_a_face_somewhere_else_after_a_long_gap_is_reacquired_from_scratch():
    script = ([[face(cx=70)]] * 3 + [[]] * FACE_REACQUIRE_AFTER
              + [[face(cx=205)]] * (FACE_CONFIRM_DETECTIONS + 1))
    results = run(script)
    first_new = results[3 + FACE_REACQUIRE_AFTER]
    assert first_new.found and not first_new.present, 'presence is re-earned'
    assert results[-1].present


def test_reset_clears_the_track():
    tracker = FaceTracker(detect_fn=Scripted([[face()]]))
    for _ in range(4):
        tracker.update(blank())
    assert tracker.update(blank()).present
    tracker.reset()
    r = tracker.update(blank())
    assert not r.present and r.box is None


def test_detect_every_skips_the_detector_without_dropping_the_driver():
    """The hold is counted in detection attempts, so raising detect_every must not
    shorten how long a face survives - it is a performance knob, not a safety one."""
    tracker = FaceTracker(detect_fn=Scripted([[face()]]), detect_every=3)
    seen = [tracker.update(blank()) for _ in range(12)]
    assert tracker._detect_fn.calls == 4, 'the detector runs on every third frame'
    assert seen[-1].present
    # The frames between detections report the held crop rather than nothing.
    assert all(r.box is not None for r in seen[6:])


def test_a_passenger_who_stays_after_the_driver_leaves_is_acquired_as_a_new_driver():
    """The end of the same story. Refusing the substitution while the driver is held
    is correct; refusing it forever would mean a device that never recovers."""
    script = ([[face(cx=70)]] * 3
              + [[face(cx=205)]] * (FACE_HOLD_DETECTIONS + FACE_CONFIRM_DETECTIONS + 2))
    results = run(script)
    assert results[3].present, 'still the original driver, holding'
    assert results[-1].present and results[-1].found
    x, _, side = results[-1].box
    assert x + side / 2 > 150, 'and the crop has moved to the new occupant'


# --------------------------------------------------------------------------- #
# the real detector
# --------------------------------------------------------------------------- #

cv2 = pytest.importorskip('cv2')

MODEL = default_model_path()
realmodel = pytest.mark.skipif(
    not MODEL.exists(),
    reason='YuNet model absent; run `python -m drowsyguard.cli fetch-models`')


def _frame_with_face(face_img, size=640):
    """Composite a face onto a larger blank frame."""
    frame = np.full((size, size, 3), 80, np.uint8)
    fh = size // 3
    resized = cv2.resize(face_img, (fh, fh))
    off = (size - fh) // 2
    frame[off:off + fh, off:off + fh] = resized
    return frame


@pytest.fixture(scope='module')
def face_img():
    """A real face; the repo keeps none, so synthesize detection input from data/."""
    import glob
    files = glob.glob('data/processed/**/*.png', recursive=True)
    if not files:
        pytest.skip('no prepared dataset images available for a real face')
    return cv2.imread(sorted(files)[0])


@realmodel
def test_the_real_detector_produces_a_blank_answer_on_a_blank_frame():
    tracker = FaceTracker(MODEL)
    r = tracker.update(np.full((480, 640, 3), 120, np.uint8))
    assert r.box is None and not r.found and not r.present


@realmodel
def test_the_real_detector_reaches_confirmation_on_a_real_face(face_img):
    """The wiring test the injected path cannot do: OpenCV's (x, y, w, h, 10 keypoint
    floats, score) row has to arrive at the gate in canonical order and in frame
    coordinates, or every geometric check tests the wrong point."""
    tracker = FaceTracker(MODEL)
    frame = _frame_with_face(face_img)
    results = [tracker.update(frame) for _ in range(FACE_CONFIRM_DETECTIONS + 1)]
    assert results[-1].present, (
        f'the gate refused a real face: {results[-1].reject!r}')
    assert results[-1].reject == ''
    x, y, side = results[-1].box
    assert 0 <= x and 0 <= y
    assert x + side <= frame.shape[1] and y + side <= frame.shape[0]
    assert len(results[-1].landmarks) == 5


@realmodel
def test_a_real_face_is_held_then_lost(face_img):
    tracker = FaceTracker(MODEL)
    frame = _frame_with_face(face_img)
    for _ in range(FACE_CONFIRM_DETECTIONS + 1):
        tracker.update(frame)

    blank_frame = np.full_like(frame, 120)
    states = [tracker.update(blank_frame) for _ in range(FACE_HOLD_DETECTIONS + 2)]
    assert [s.present for s in states] == (
        [True] * FACE_HOLD_DETECTIONS + [False, False])
    assert states[0].box is not None
    assert states[-1].box is None
