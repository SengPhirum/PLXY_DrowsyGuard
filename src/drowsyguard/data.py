from pathlib import Path
import os
import random
import shutil
from PIL import Image, ImageOps
from torch.utils.data import Dataset
import torch
import numpy as np

EXTS = {'.jpg', '.jpeg', '.png', '.bmp'}
VIDEO_EXTS = {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.m4v', '.mpg', '.mpeg'}
CLASSES = ('alert', 'drowsy')
SPLITS = ('train', 'val', 'test')


def iter_video_frames(path, stride=1, max_frames=None):
    """Yield PIL frames from a video, taking every `stride`-th frame.

    Consecutive video frames are near-duplicates, so a stride above 1 is usually
    wanted: it reduces redundancy without touching subject independence, which is
    enforced at the split level rather than here.
    """
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f'Could not open video {path}')
    try:
        index = taken = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if index % stride == 0:
                yield Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                taken += 1
                if max_frames and taken >= max_frames:
                    break
            index += 1
    finally:
        cap.release()


def place_file(src_path, dest_path, link=False):
    """Copy src to dest, or hardlink when asked and the filesystem allows it."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists():
        return False
    if link:
        try:
            os.link(src_path, dest_path)
            return True
        except OSError:
            pass  # different volume or unsupported filesystem; copy instead
    shutil.copy2(src_path, dest_path)
    return True


def subject_split(subjects, train=0.70, val=0.15, seed=42):
    subjects = sorted(subjects)
    rnd = random.Random(seed)
    rnd.shuffle(subjects)
    n = len(subjects)
    n_train = max(1, int(n * train)) if n >= 3 else max(1, n - 2)
    n_val = max(1, int(n * val)) if n >= 3 else (1 if n >= 2 else 0)
    train_s = subjects[:n_train]
    val_s = subjects[n_train:n_train+n_val]
    test_s = subjects[n_train+n_val:]
    if n >= 3 and not test_s:
        test_s = [val_s.pop()]
    return {'train': train_s, 'val': val_s, 'test': test_s}


def prepare_dataset(input_dir, output_dir, train=0.70, val=0.15, seed=42, stride=1, link=False,
                    overwrite=False):
    """Build subject-independent splits from `<input>/<subject>/<class>/`.

    Each class directory may hold images, videos, or both; videos are decoded to
    frames here so that every frame of a clip lands in exactly one split with its
    subject, which is what keeps the split subject-independent.
    """
    src = Path(input_dir)
    out = Path(output_dir)
    subjects = [p.name for p in src.iterdir() if p.is_dir()]
    if not subjects:
        raise ValueError(f'No subject directories found under {src}')

    # Writing a new split on top of an old one would leave the previous split's
    # files behind and put one subject in two splits - silent leakage.
    stale = [s for s in SPLITS if (out / s).exists() and any((out / s).rglob('*'))]
    if stale and not overwrite:
        raise ValueError(
            f'{out} already contains splits {stale}. Re-splitting on top of them would leak '
            'subjects across splits. Pass overwrite=True (CLI: --overwrite) to replace them.')
    if overwrite:
        for s in SPLITS:
            shutil.rmtree(out / s, ignore_errors=True)

    splits = subject_split(subjects, train, val, seed)
    counts = {s: {c: 0 for c in CLASSES} for s in splits}
    videos = 0
    for split, names in splits.items():
        for subject in names:
            for cls in CLASSES:
                cdir = src / subject / cls
                if not cdir.exists():
                    continue
                dest = out / split / cls
                for item in sorted(cdir.iterdir()):
                    ext = item.suffix.lower()
                    if ext in EXTS:
                        place_file(item, dest / f'{subject}__{item.name}', link)
                        counts[split][cls] += 1
                    elif ext in VIDEO_EXTS:
                        videos += 1
                        dest.mkdir(parents=True, exist_ok=True)
                        for k, frame in enumerate(iter_video_frames(item, stride)):
                            frame.save(dest / f'{subject}__{item.stem}__{k:06d}.png')
                            counts[split][cls] += 1
    return {'splits': splits, 'counts': counts, 'videos_decoded': videos}


def preprocess_gray(img, image_size=64, normalize=False):
    """Shared train/inference preprocessing.

    Used by FolderDataset, the live dashboard and (in spirit) the firmware, so what
    the model sees at development time matches what it was trained on. Any change
    here must be mirrored in the firmware capture path.

    `normalize` standardizes each image to zero mean / unit variance. This removes
    per-driver brightness and skin-tone offsets, which is the appearance cue the
    first DDD baseline latched onto. It costs one mean and one variance pass over
    4096 pixels on device, so it is affordable on an ESP32-S3 - but a model trained
    with it MUST be run with it.
    """
    img = img.convert('L')
    img = ImageOps.fit(img, (image_size, image_size))
    arr = np.asarray(img, dtype=np.float32) / 255.0
    if normalize:
        arr = (arr - arr.mean()) / max(float(arr.std()), 1e-5)
    return arr


def augment_gray(arr, rng):
    """Light photometric+geometric jitter for a 2-D float image, in place-safe form.

    Deliberately conservative: drowsiness is signalled by small eyelid changes, so
    heavy warping would destroy the label. Flip is safe because face symmetry does
    not change drowsiness.
    """
    if rng.random() < 0.5:
        arr = arr[:, ::-1]

    # Brightness / contrast jitter attacks per-driver appearance directly.
    if rng.random() < 0.8:
        gain = 1.0 + rng.uniform(-0.25, 0.25)
        bias = rng.uniform(-0.12, 0.12)
        arr = arr * gain + bias

    # Small affine: rotation, translation, scale.
    if rng.random() < 0.7:
        h, w = arr.shape
        angle = np.deg2rad(rng.uniform(-10, 10))
        scale = 1.0 + rng.uniform(-0.08, 0.08)
        tx, ty = rng.uniform(-0.06, 0.06) * w, rng.uniform(-0.06, 0.06) * h
        cos_a, sin_a = np.cos(angle) / scale, np.sin(angle) / scale
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
        xc, yc = (w - 1) / 2.0, (h - 1) / 2.0
        xs, ys = xs - xc - tx, ys - yc - ty
        src_x = cos_a * xs + sin_a * ys + xc
        src_y = -sin_a * xs + cos_a * ys + yc
        xi = np.clip(np.rint(src_x), 0, w - 1).astype(np.int32)
        yi = np.clip(np.rint(src_y), 0, h - 1).astype(np.int32)
        arr = arr[yi, xi]

    if rng.random() < 0.3:
        arr = arr + rng.normal(0.0, 0.03, arr.shape).astype(np.float32)

    # Small occlusion so no single patch can be relied upon.
    if rng.random() < 0.25:
        h, w = arr.shape
        eh, ew = rng.integers(h // 10, h // 5), rng.integers(w // 10, w // 5)
        ey, ex = rng.integers(0, h - eh), rng.integers(0, w - ew)
        arr = arr.copy()
        arr[ey:ey + eh, ex:ex + ew] = float(arr.mean())

    return np.ascontiguousarray(arr, dtype=np.float32)


class FolderDataset(Dataset):
    def __init__(self, root, image_size=64, augment=False, normalize=False, seed=0):
        self.samples = []
        self.image_size = image_size
        self.augment = augment
        self.normalize = normalize
        self._seed = seed
        self._rng = None
        root = Path(root)
        for label, cls in enumerate(CLASSES):
            cdir = root / cls
            if cdir.exists():
                for p in sorted(cdir.iterdir()):
                    if p.suffix.lower() in EXTS:
                        self.samples.append((p, label))
        if not self.samples:
            raise ValueError(f'No images found under {root}')

    def __len__(self): return len(self.samples)

    def __getitem__(self, idx):
        path, label = self.samples[idx]
        # Augment before normalizing, so the jitter is what normalization removes.
        arr = preprocess_gray(Image.open(path), self.image_size)
        if self.augment:
            if self._rng is None:
                # Per-worker stream: workers are forked/spawned after __init__.
                info = torch.utils.data.get_worker_info()
                self._rng = np.random.default_rng(self._seed + (info.id + 1 if info else 0))
            arr = augment_gray(arr, self._rng)
        if self.normalize:
            arr = (arr - arr.mean()) / max(float(arr.std()), 1e-5)
        x = torch.from_numpy(np.ascontiguousarray(arr)).unsqueeze(0)
        return x, torch.tensor(label, dtype=torch.long)
