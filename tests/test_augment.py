"""Augmentation and normalization must change appearance without breaking the tensor."""
import numpy as np

from drowsyguard.data import augment_gray, preprocess_gray
from PIL import Image


def _img(value=140, size=64):
    return Image.fromarray(np.full((size, size, 3), value, np.uint8))


def test_normalize_removes_brightness_offset():
    dark = preprocess_gray(_img(60), 64, normalize=False)
    bright = preprocess_gray(_img(200), 64, normalize=False)
    assert abs(dark.mean() - bright.mean()) > 0.4

    # Flat images have zero variance; use a gradient so std is meaningful.
    grad = np.tile(np.linspace(0, 255, 64, dtype=np.uint8), (64, 1))
    a = preprocess_gray(Image.fromarray(np.stack([grad] * 3, -1)), 64, normalize=True)
    b = preprocess_gray(Image.fromarray(np.stack([np.clip(grad // 2 + 40, 0, 255)] * 3, -1)),
                        64, normalize=True)
    assert abs(a.mean()) < 1e-4 and abs(b.mean()) < 1e-4
    assert abs(a.std() - 1.0) < 1e-3 and abs(b.std() - 1.0) < 1e-3
    # After standardization the two differently-exposed gradients agree closely.
    assert np.abs(a - b).mean() < 0.05


def test_augment_preserves_shape_and_dtype():
    rng = np.random.default_rng(0)
    grad = np.tile(np.linspace(0, 255, 64, dtype=np.uint8), (64, 1))
    base = preprocess_gray(Image.fromarray(np.stack([grad] * 3, -1)), 64)
    for _ in range(50):
        out = augment_gray(base, rng)
        assert out.shape == base.shape
        assert out.dtype == np.float32
        assert np.isfinite(out).all()
        assert out.flags['C_CONTIGUOUS']


def test_augment_actually_varies_output():
    rng = np.random.default_rng(1)
    grad = np.tile(np.linspace(0, 255, 64, dtype=np.uint8), (64, 1))
    base = preprocess_gray(Image.fromarray(np.stack([grad] * 3, -1)), 64)
    outs = [augment_gray(base, rng) for _ in range(20)]
    diffs = [float(np.abs(o - base).mean()) for o in outs]
    assert sum(d > 1e-6 for d in diffs) >= 18   # nearly all should differ
    assert not np.allclose(outs[0], outs[1])


def test_augment_does_not_mutate_input():
    rng = np.random.default_rng(2)
    grad = np.tile(np.linspace(0, 255, 64, dtype=np.uint8), (64, 1))
    base = preprocess_gray(Image.fromarray(np.stack([grad] * 3, -1)), 64)
    snapshot = base.copy()
    for _ in range(30):
        augment_gray(base, rng)
    assert np.array_equal(base, snapshot)
