"""Eye-state classification and PERCLOS, the drowsiness mechanism.

Instead of asking a CNN "does this face look drowsy" (which learns who the driver
is), this measures eyelid closure directly and integrates it over time. That is the
standard driver-monitoring approach, and it shrinks the on-device model: the input
is a 32x32 eye patch rather than a whole face.

Base model: `open-closed-eye-0001` from the OpenVINO Model Zoo (Intel, Apache-2.0),
11.3k parameters, 0.0014 GFLOPs, 95.84% reported accuracy - small enough for an
ESP32-S3 at INT8.

Three details about this model are wrong or missing in its published card and were
established empirically here:
  * input must be `(pixel - 127.0) / 255.0`; feeding raw 0-255 overflows it to NaN,
  * the output is already softmaxed, so do not apply softmax again,
  * the card says the output is `[open, closed]`, but index 0 tracks *closed*.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path

EYE_MODEL_FILENAME = 'open_closed_eye.onnx'
EYE_MODEL_URL = ('https://storage.openvinotoolkit.org/repositories/open_model_zoo/'
                 'public/2022.1/open-closed-eye-0001/open_closed_eye.onnx')
EYE_MODEL_SIZE = 46164

# Index of the "closed" class in the model output. See module docstring.
CLOSED_INDEX = 0

# Eye patch side as a fraction of the face box side. Chosen by AUC on the DDD
# validation subjects over 0.12-0.36; 0.20 was best and wider crops (brow, cheek)
# degraded it.
EYE_PATCH_SCALE = 0.20

# YuNet landmark order.
RIGHT_EYE, LEFT_EYE = 0, 1

DEFAULT_MODEL_DIR = Path('models/detectors')


def default_eye_model_path(root=None):
    base = Path(root) if root else Path.cwd()
    return base / DEFAULT_MODEL_DIR / EYE_MODEL_FILENAME


def fetch_eye_model(dest=None, url=EYE_MODEL_URL):
    import urllib.request

    path = Path(dest) if dest else default_eye_model_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, path)
    return path


def eye_patch_boxes(face_box, landmarks, scale=EYE_PATCH_SCALE):
    """Square crop boxes around each eye landmark, as (x0, y0, side) pairs."""
    if not landmarks or len(landmarks) < 2:
        return []
    # face_box is (x, y, side) from FaceTracker._square - already square, so the
    # patch size is a fraction of that one side.
    side = max(8, int(face_box[2] * scale))
    half = side // 2
    boxes = []
    for idx in (RIGHT_EYE, LEFT_EYE):
        ex, ey = landmarks[idx]
        boxes.append((int(ex) - half, int(ey) - half, side))
    return boxes


class EyeStateClassifier:
    def __init__(self, model_path):
        import onnxruntime

        self.model_path = str(model_path)
        self._session = onnxruntime.InferenceSession(
            self.model_path, providers=['CPUExecutionProvider'])
        self._input = self._session.get_inputs()[0].name

    def p_closed(self, patch_bgr):
        """patch_bgr: HxWx3 uint8. Returns P(eye closed)."""
        import cv2
        import numpy as np

        if patch_bgr.shape[0] != 32 or patch_bgr.shape[1] != 32:
            patch_bgr = cv2.resize(patch_bgr, (32, 32))
        x = ((patch_bgr.astype(np.float32) - 127.0) / 255.0).transpose(2, 0, 1)[None]
        probs = self._session.run(None, {self._input: x})[0].reshape(-1)
        return float(probs[CLOSED_INDEX])


class PerclosTracker:
    """PERCLOS: fraction of recent frames in which the eyes were closed.

    A single closed frame is a blink, not drowsiness; sustained closure is the
    signal. Keeping this as a windowed fraction (rather than a raw per-frame
    probability) is what makes the downstream threshold interpretable, and it is a
    rolling counter, so it is affordable on device.
    """

    def __init__(self, window=90, closed_threshold=0.5):
        self.window = int(window)
        self.closed_threshold = float(closed_threshold)
        self._flags = deque(maxlen=self.window)
        self._closed = 0

    def reset(self):
        self._flags.clear()
        self._closed = 0

    def update(self, p_closed):
        flag = 1 if p_closed >= self.closed_threshold else 0
        if len(self._flags) == self._flags.maxlen and self._flags:
            self._closed -= self._flags[0]
        self._flags.append(flag)
        self._closed += flag
        return self.value

    def resize(self, window):
        window = max(1, int(window))
        if window == self.window:
            return
        kept = list(self._flags)[-window:]
        self.window = window
        self._flags = deque(kept, maxlen=window)
        self._closed = sum(kept)

    @property
    def value(self):
        if not self._flags:
            return 0.0
        return self._closed / len(self._flags)

    @property
    def filled(self):
        return len(self._flags)
