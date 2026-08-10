"""PERCLOS windowing and eye-patch geometry, plus the model contract if present."""
import numpy as np
import pytest

from drowsyguard.eyestate import (CLOSED_INDEX, EyeStateClassifier, PerclosTracker,
                                  default_eye_model_path, eye_patch_boxes)


def test_perclos_is_fraction_of_closed_frames():
    p = PerclosTracker(window=10, closed_threshold=0.5)
    assert p.value == 0.0
    for _ in range(5):
        p.update(0.9)
    assert p.value == 1.0            # only closed frames so far
    for _ in range(5):
        p.update(0.1)
    assert p.value == 0.5            # 5 of 10


def test_perclos_window_evicts_old_frames():
    p = PerclosTracker(window=4, closed_threshold=0.5)
    for _ in range(4):
        p.update(1.0)
    assert p.value == 1.0
    for _ in range(4):
        p.update(0.0)
    assert p.value == 0.0            # the closed frames aged out


def test_single_blink_barely_moves_perclos():
    """The whole point of PERCLOS: a blink must not look like drowsiness."""
    p = PerclosTracker(window=90, closed_threshold=0.5)
    for _ in range(60):
        p.update(0.02)
    for _ in range(3):               # ~100 ms blink at 30 fps
        p.update(0.98)
    assert p.value < 0.05

    for _ in range(60):              # sustained closure
        p.update(0.98)
    assert p.value > 0.6


def test_threshold_controls_what_counts_as_closed():
    strict = PerclosTracker(window=10, closed_threshold=0.9)
    loose = PerclosTracker(window=10, closed_threshold=0.3)
    for _ in range(10):
        strict.update(0.5)
        loose.update(0.5)
    assert strict.value == 0.0
    assert loose.value == 1.0


def test_resize_keeps_recent_history():
    p = PerclosTracker(window=10, closed_threshold=0.5)
    for _ in range(10):
        p.update(1.0)
    p.resize(4)
    assert p.window == 4
    assert p.value == 1.0
    p.update(0.0)
    assert p.value == 0.75


def test_reset_clears():
    p = PerclosTracker(window=5)
    for _ in range(5):
        p.update(1.0)
    p.reset()
    assert p.value == 0.0 and p.filled == 0


def test_eye_patch_boxes_are_square_and_centred_on_landmarks():
    face_box = (100, 100, 200)          # x, y, side
    landmarks = [(150.0, 160.0), (210.0, 160.0), (180, 190), (160, 220), (200, 220)]
    boxes = eye_patch_boxes(face_box, landmarks, scale=0.20)
    assert len(boxes) == 2
    side = boxes[0][2]
    assert side == int(200 * 0.20)
    for (x0, y0, s), (ex, ey) in zip(boxes, landmarks[:2]):
        assert s == side
        assert abs((x0 + s / 2) - ex) <= 1
        assert abs((y0 + s / 2) - ey) <= 1


def test_eye_patch_boxes_needs_two_landmarks():
    assert eye_patch_boxes((0, 0, 100), []) == []
    assert eye_patch_boxes((0, 0, 100), [(1.0, 2.0)]) == []


MODEL = default_eye_model_path()


@pytest.mark.skipif(not MODEL.exists(), reason='run `python -m drowsyguard.cli fetch-models`')
def test_model_returns_probability_and_accepts_any_patch_size():
    clf = EyeStateClassifier(MODEL)
    for size in (32, 20, 64):
        patch = np.full((size, size, 3), 120, np.uint8)
        p = clf.p_closed(patch)
        assert 0.0 <= p <= 1.0, p
        assert np.isfinite(p)


@pytest.mark.skipif(not MODEL.exists(), reason='run `python -m drowsyguard.cli fetch-models`')
def test_preprocessing_does_not_produce_nan():
    """Raw 0-255 input overflows this network; the class must scale correctly."""
    clf = EyeStateClassifier(MODEL)
    rng = np.random.default_rng(0)
    for _ in range(20):
        patch = rng.integers(0, 256, (32, 32, 3), dtype=np.uint8)
        assert np.isfinite(clf.p_closed(patch))
    assert CLOSED_INDEX in (0, 1)
