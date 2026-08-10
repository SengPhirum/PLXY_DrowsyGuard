"""Face tracker behaviour: detection, box framing, hold-then-lose, and toggling."""
import numpy as np
import pytest

from drowsyguard.facedetect import FaceTracker, default_model_path

cv2 = pytest.importorskip('cv2')

MODEL = default_model_path()
pytestmark = pytest.mark.skipif(
    not MODEL.exists(),
    reason='YuNet model absent; run `python -m drowsyguard.cli fetch-models`')


def _frame_with_face(face_img, size=640):
    """Composite a face onto a larger blank frame."""
    frame = np.full((size, size, 3), 80, np.uint8)
    fh = size // 3
    face = cv2.resize(face_img, (fh, fh))
    off = (size - fh) // 2
    frame[off:off + fh, off:off + fh] = face
    return frame


@pytest.fixture(scope='module')
def face_img():
    """A real face; the repo keeps none, so synthesize detection input from data/."""
    import glob
    files = glob.glob('data/processed/**/*.png', recursive=True)
    if not files:
        pytest.skip('no prepared dataset images available for a real face')
    return cv2.imread(sorted(files)[0])


def test_blank_frame_yields_no_box():
    tracker = FaceTracker(MODEL)
    blank = np.full((480, 640, 3), 120, np.uint8)
    r = tracker.update(blank)
    assert r.box is None and not r.found and not r.held


def test_detects_face_and_box_is_square_inside_frame(face_img):
    tracker = FaceTracker(MODEL)
    frame = _frame_with_face(face_img)
    r = tracker.update(frame)
    assert r.found and r.box is not None
    x, y, side = r.box
    assert side > 0
    assert 0 <= x and 0 <= y
    assert x + side <= frame.shape[1] and y + side <= frame.shape[0]
    assert len(r.landmarks) == 5


def test_holds_box_then_reports_lost(face_img):
    tracker = FaceTracker(MODEL, hold_frames=3)
    frame = _frame_with_face(face_img)
    assert tracker.update(frame).found

    blank = np.full_like(frame, 120)
    states = [tracker.update(blank) for _ in range(5)]
    assert [s.held for s in states] == [True, True, True, False, False]
    # While held the crop stays put; afterwards there is no box at all.
    assert states[0].box is not None
    assert states[-1].box is None


def test_margin_enlarges_the_crop(face_img):
    frame = _frame_with_face(face_img)
    tight = FaceTracker(MODEL, margin=0.0, smooth=0.0).update(frame)
    wide = FaceTracker(MODEL, margin=0.6, smooth=0.0).update(frame)
    assert tight.found and wide.found
    assert wide.box[2] > tight.box[2]


def test_reset_clears_hold(face_img):
    tracker = FaceTracker(MODEL, hold_frames=10)
    tracker.update(_frame_with_face(face_img))
    tracker.reset()
    r = tracker.update(np.full((480, 640, 3), 120, np.uint8))
    assert r.box is None and not r.held
