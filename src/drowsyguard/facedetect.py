"""Face detection and short-term tracking for the live dashboard.

Uses OpenCV's YuNet detector. OpenCV 5 removed `CascadeClassifier`, so Haar is not
an option; the YuNet ONNX file is fetched once by `drowsyguard fetch-models`.

Framing note: measured over 400 DDD images, the detected face box is ~1.02x the
image side, i.e. DDD crops are extremely tight. So the default margin is 0 - the
detected box *is* the training framing. Raising the margin moves the live input out
of the training distribution.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MODEL_FILENAME = 'face_detection_yunet_2023mar.onnx'
MODEL_URL = ('https://github.com/opencv/opencv_zoo/raw/main/models/'
             'face_detection_yunet/face_detection_yunet_2023mar.onnx')
DEFAULT_MODEL_DIR = Path('models/detectors')

# Median face-box-to-image ratio across DDD; see module docstring.
DDD_BOX_RATIO = 1.02


@dataclass
class FaceResult:
    box: tuple | None      # (x, y, side) square crop in frame coords, or None
    found: bool            # a face was detected on this frame
    held: bool             # no detection; reusing the last box
    score: float
    landmarks: list        # [(x, y), ...] five points, empty when not detected


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


class FaceTracker:
    """Detect a face per frame, smooth the box, and hold it briefly when lost.

    Holding matters for drowsiness work: a detector often drops the face exactly
    when the eyes close or the head nods, which is the moment of interest. Without
    a hold the crop would snap back to the whole frame at that instant.
    """

    def __init__(self, model_path, margin=0.0, smooth=0.6, hold_frames=15,
                 score_threshold=0.6, detect_every=1):
        import cv2

        self._cv2 = cv2
        self.margin = float(margin)
        self.smooth = min(max(float(smooth), 0.0), 0.95)
        self.hold_frames = int(hold_frames)
        self.detect_every = max(1, int(detect_every))
        self.model_path = str(model_path)
        self._det = cv2.FaceDetectorYN.create(self.model_path, '', (320, 320),
                                              float(score_threshold), 0.3, 5000)
        self._size = None
        self._state = None       # smoothed (cx, cy, side)
        self._misses = 0
        self._frame_no = 0
        self._last_score = 0.0
        self._last_landmarks = []

    def reset(self):
        self._state = None
        self._misses = 0
        self._last_score = 0.0
        self._last_landmarks = []

    def update(self, frame):
        h, w = frame.shape[:2]
        if self._size != (w, h):
            self._det.setInputSize((w, h))
            self._size = (w, h)

        self._frame_no += 1
        detected = None
        if self._frame_no % self.detect_every == 0:
            _, faces = self._det.detect(frame)
            if faces is not None and len(faces):
                # Highest score wins; a driver-facing camera sees one face.
                best = max(faces, key=lambda f: float(f[-1]))
                detected = best

        if detected is not None:
            x, y, bw, bh = (float(v) for v in detected[:4])
            self._last_score = float(detected[-1])
            pts = [(float(detected[4 + 2 * i]), float(detected[5 + 2 * i])) for i in range(5)]
            self._last_landmarks = pts
            cx, cy = x + bw / 2.0, y + bh / 2.0
            side = max(bw, bh) * (1.0 + self.margin)
            if self._state is None:
                self._state = (cx, cy, side)
            else:
                a = self.smooth
                pcx, pcy, pside = self._state
                self._state = (a * pcx + (1 - a) * cx,
                               a * pcy + (1 - a) * cy,
                               a * pside + (1 - a) * side)
            self._misses = 0
            return FaceResult(self._square(w, h), True, False, self._last_score, pts)

        # No detection this frame: hold the previous box for a short while.
        if self._state is not None and self._misses < self.hold_frames:
            self._misses += 1
            return FaceResult(self._square(w, h), False, True,
                              self._last_score, self._last_landmarks)

        self._state = None
        self._last_landmarks = []
        return FaceResult(None, False, False, 0.0, [])

    def _square(self, w, h):
        cx, cy, side = self._state
        side = max(16.0, min(side, float(min(w, h))))
        x0 = int(round(min(max(cx - side / 2.0, 0.0), w - side)))
        y0 = int(round(min(max(cy - side / 2.0, 0.0), h - side)))
        return (x0, y0, int(round(side)))
