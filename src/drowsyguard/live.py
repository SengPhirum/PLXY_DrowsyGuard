"""Live capture + inference engine behind the development dashboard.

Runs the same preprocessing as training (`preprocess_gray`) and the same
decision logic as the firmware (`RiskFilter`), so thresholds tuned here
transfer to the ESP32-S3 without translation.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image

from .data import preprocess_gray
from .risk import DEFAULT_COOLDOWN, DEFAULT_REQUIRED, DEFAULT_TRIGGER, RiskFilter

CLASS_NAMES = ('alert', 'drowsy')

# A live webcam that delivers far below this is not merely a slow machine; on
# Windows it usually means the capture stack is degraded by the launch context.
SLOW_CAPTURE_FPS = 5.0
SLOW_CAPTURE_SAMPLE = 40
SLOW_CAPTURE_SECONDS = 5.0


def _softmax(x):
    e = np.exp(x - np.max(x))
    return e / e.sum()


def benchmark_backends(index=0, frames=20):
    """Measure capture rate per OpenCV backend, on this thread and a worker thread.

    Capture speed can depend on both the backend and the launch context, so the
    engine probes at startup instead of hard-coding a backend.
    """
    import threading

    import cv2

    results = []
    candidates = [('MSMF', getattr(cv2, 'CAP_MSMF', None)), ('DSHOW', getattr(cv2, 'CAP_DSHOW', None))]
    for name, flag in candidates:
        if flag is None:
            continue
        for where in ('main', 'worker'):
            out = {}

            def run():
                cap = cv2.VideoCapture(index, flag)
                try:
                    if not cap.isOpened():
                        out['fps'] = 0.0
                        return
                    for _ in range(5):
                        cap.read()
                    t = time.perf_counter()
                    good = sum(1 for _ in range(frames) if cap.read()[0])
                    dt = time.perf_counter() - t
                    out['fps'] = (good / dt) if dt > 0 and good else 0.0
                finally:
                    cap.release()

            if where == 'main':
                run()
            else:
                t = threading.Thread(target=run)
                t.start()
                t.join()
            results.append({'backend': name, 'thread': where, 'fps': round(out.get('fps', 0.0), 1)})
            time.sleep(0.4)
    return results


class ModelRunner:
    """Loads a .pt checkpoint or .onnx model, or falls back to untrained weights.

    `trained` is False for the fallback; the dashboard surfaces that prominently
    because an untrained net emits a near-constant probability that must not be
    mistaken for detection.
    """

    def __init__(self, checkpoint=None, image_size=64, normalize=False):
        self.image_size = image_size
        self.kind = 'untrained'
        self.trained = False
        self.source = 'randomly initialized TinyDrowsyNet'
        # Read from the checkpoint when possible: a model trained on standardized
        # input produces nonsense on raw input, and vice versa.
        self.normalize = bool(normalize)
        self._lock = threading.Lock()

        path = Path(checkpoint) if checkpoint else None
        if path and path.suffix.lower() == '.onnx' and path.exists():
            import onnxruntime
            self._session = onnxruntime.InferenceSession(str(path), providers=['CPUExecutionProvider'])
            self._input_name = self._session.get_inputs()[0].name
            self.kind, self.trained, self.source = 'onnx', True, str(path)
            from .export import load_preprocess
            meta = load_preprocess(path)
            if meta:
                self.normalize = bool(meta.get('normalize', self.normalize))
                self.image_size = int(meta.get('image_size', self.image_size))
            return

        import torch
        from .model import TinyDrowsyNet
        self._torch = torch
        self._model = TinyDrowsyNet()
        if path and path.exists():
            state = torch.load(path, map_location='cpu')
            self._model.load_state_dict(state['model'])
            self.kind, self.trained, self.source = 'torch', True, str(path)
            saved_cfg = state.get('cfg') or {}
            self.normalize = bool(saved_cfg.get('normalize', self.normalize))
        self._model.eval()

    def predict(self, arr):
        """arr: (H, W) float32 in [0,1]. Returns p(drowsy)."""
        batch = arr[None, None, :, :]
        with self._lock:
            if self.kind == 'onnx':
                logits = self._session.run(None, {self._input_name: batch})[0][0]
            else:
                with self._torch.no_grad():
                    logits = self._model(self._torch.from_numpy(batch)).numpy()[0]
        return float(_softmax(logits)[1])


class LiveEngine:
    """Background capture/inference loop with a thread-safe state snapshot."""

    def __init__(self, source=0, checkpoint=None, image_size=64, history=300,
                 trigger=DEFAULT_TRIGGER, required=DEFAULT_REQUIRED, cooldown=DEFAULT_COOLDOWN,
                 zoom=1.0, face_detect=True, face_margin=0.0, face_model=None,
                 mode='eye', eye_model=None, perclos_window=90, eye_closed_threshold=0.5):
        self.source = source
        self.image_size = image_size
        self.mode = mode
        # Fraction of the short edge fed to the model. Datasets of tight face crops
        # (DDD) need a smaller value than a full webcam frame, or the model sees a
        # face-in-a-room where it was trained on a face filling the frame.
        self.zoom = float(zoom)
        self.filter = RiskFilter(trigger, required, cooldown)

        # Eye mode needs no drowsiness checkpoint: risk comes from measured eyelid
        # closure via a pretrained eye-state model, not a learned face classifier.
        self._eye = None
        self._eye_error = None
        self._perclos = None
        self._behavior = None
        if mode == 'eye':
            from .eyestate import (EyeStateClassifier, PerclosTracker, default_eye_model_path)
            path = Path(eye_model) if eye_model else default_eye_model_path()
            if path.exists():
                from .behavior import BehaviorAnalyzer
                self._eye = EyeStateClassifier(path)
                self._perclos = PerclosTracker(perclos_window, eye_closed_threshold)
                self._behavior = BehaviorAnalyzer(eye_closed_threshold, fps=30.0)
            else:
                self._eye_error = (f'Eye-state model not found at {path}. '
                                   'Run `python -m drowsyguard.cli fetch-models`.')
                self.mode = 'face'

        self.model = ModelRunner(checkpoint, image_size) if self.mode == 'face' else None
        if self.model is not None:
            # An ONNX sidecar may specify a different input size than the config.
            self.image_size = self.model.image_size

        self.face_detect = bool(face_detect)
        self._tracker = None
        self._tracker_error = None
        if self.face_detect:
            from .facedetect import FaceTracker, default_model_path
            path = Path(face_model) if face_model else default_model_path()
            if path.exists():
                self._tracker = FaceTracker(path, margin=face_margin)
            else:
                self.face_detect = False
                self._tracker_error = (f'Face detector model not found at {path}. '
                                       'Run `python -m drowsyguard.cli fetch-models`. '
                                       'Falling back to a centre crop; set --zoom manually.')

        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self._history = deque(maxlen=history)
        self._alerts = deque(maxlen=50)
        self._behavior_log = deque(maxlen=60)
        self._frame_jpeg = None
        self._input_jpeg = None
        self._error = None
        self._frames = 0
        self._alert_count = 0
        self._p = 0.0
        self._fps = 0.0
        self._infer_ms = 0.0
        self._crop = None
        self._camera = {}
        self._capture_warning = None
        self._face = {'found': False, 'held': False, 'score': 0.0, 'source': 'centre crop'}
        self._eyes = {'left_closed': 0.0, 'right_closed': 0.0, 'closed': 0.0,
                      'perclos': 0.0, 'window': perclos_window, 'available': False}
        self._eye_jpeg = None

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='drowsyguard-live', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    # -- capture loop ------------------------------------------------------
    def _open_capture(self):
        import cv2
        src = self.source
        if isinstance(src, str) and src.isdigit():
            src = int(src)
        if not isinstance(src, int):
            return cv2.VideoCapture(src), src
        # Backend reliability varies by machine and launch context (MSMF can open
        # a device it cannot then grab from), so take the first that actually
        # delivers a frame rather than trusting isOpened().
        for flag in (getattr(cv2, 'CAP_MSMF', None), getattr(cv2, 'CAP_DSHOW', None), cv2.CAP_ANY):
            if flag is None:
                continue
            cap = cv2.VideoCapture(src, flag)
            if cap.isOpened():
                for _ in range(5):
                    if cap.read()[0]:
                        return cap, src
            cap.release()
        return None, src

    def _run(self):
        import cv2
        cap, src = self._open_capture()
        if not cap or not cap.isOpened():
            with self._lock:
                self._error = (f'Could not open video source {src!r}. '
                               'Check the camera index, close other apps using the webcam, '
                               'or pass --source <path-to-video>.')
            return
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        with self._lock:
            self._error = None
            self._camera = {
                'backend': cap.getBackendName(),
                'width': int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                'height': int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                'driver_fps': cap.get(cv2.CAP_PROP_FPS),
                'fourcc': ''.join(chr((fourcc >> (8 * i)) & 0xFF) for i in range(4)) if fourcc else '',
            }

        is_file = not isinstance(src, int)
        # Replay clips at their recorded rate, otherwise a file decodes hundreds of
        # frames per second and the frame-counted streak/cooldown look unrealistic.
        file_interval = 0.0
        if is_file:
            src_fps = cap.get(cv2.CAP_PROP_FPS)
            if src_fps and src_fps > 0:
                file_interval = 1.0 / src_fps
        last = time.perf_counter()
        started = last
        fps_ema = None
        checked_capture = False
        try:
            while not self._stop.is_set():
                frame_started = time.perf_counter()
                ok, frame = cap.read()
                if not ok:
                    if is_file:  # loop recorded clips for repeatable demos
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    with self._lock:
                        self._error = 'Camera stopped returning frames.'
                    break

                h, w = frame.shape[:2]
                with self._lock:
                    zoom = min(max(self.zoom, 0.05), 1.0)
                    use_face = self._tracker is not None and self.face_detect

                face = self._tracker.update(frame) if use_face else None
                landmarks = []
                if face is not None and face.box is not None:
                    x0, y0, side = face.box
                    landmarks = face.landmarks
                    face_state = {'found': face.found, 'held': face.held,
                                  'score': round(face.score, 3),
                                  'source': 'face (held)' if face.held else 'face'}
                else:
                    side = int(min(h, w) * zoom)
                    x0, y0 = (w - side) // 2, (h - side) // 2
                    face_state = {'found': False, 'held': False, 'score': 0.0,
                                  'source': 'centre crop' if use_face else 'centre crop (detector off)'}

                rgb = cv2.cvtColor(frame[y0:y0 + side, x0:x0 + side], cv2.COLOR_BGR2RGB)
                t0 = time.perf_counter()
                behavior_events = []
                if self._eye is not None:
                    p, arr, eye_state, behavior_events = self._eye_risk(
                        cv2, frame, (x0, y0, side), landmarks)
                else:
                    arr = preprocess_gray(Image.fromarray(rgb), self.image_size,
                                          normalize=self.model.normalize)
                    p = self.model.predict(arr)
                    eye_state = None
                infer_ms = (time.perf_counter() - t0) * 1000.0

                with self._lock:
                    fired = self.filter.update(p)
                    streak, cooldown_left = self.filter.streak, self.filter.cooldown_left
                    required, cooldown = self.filter.required, self.filter.cooldown
                    trigger = self.filter.trigger
                    if fired:
                        self._alert_count += 1
                        self._alerts.append({'index': self._frames, 'p': round(p, 4),
                                             'time': time.strftime('%H:%M:%S'), 'kind': 'ALERT'})
                    for ev in behavior_events:
                        self._behavior_log.append({'index': self._frames, 'kind': ev,
                                                   'time': time.strftime('%H:%M:%S')})
                    self._frames += 1
                    self._p = p
                    self._infer_ms = infer_ms
                    self._history.append(round(p, 4))
                    self._crop = [x0, y0, side, side]
                    self._face = face_state
                    if eye_state is not None:
                        self._eyes = eye_state

                now = time.perf_counter()
                dt = now - last
                last = now
                if dt > 0:
                    inst = 1.0 / dt
                    fps_ema = inst if fps_ema is None else (0.9 * fps_ema + 0.1 * inst)

                self._encode(cv2, frame, arr, p, trigger, streak, required,
                             cooldown_left, cooldown, (x0, y0, side), landmarks, face_state)
                with self._lock:
                    self._fps = float(fps_ema or 0.0)
                    elapsed = now - started
                    # Judge on the average once enough frames *or* enough wall time
                    # has passed: a badly throttled camera never reaches the frame count.
                    if (not is_file and not checked_capture
                            and (self._frames >= SLOW_CAPTURE_SAMPLE or elapsed >= SLOW_CAPTURE_SECONDS)):
                        checked_capture = True
                        self._capture_warning = self._slow_capture_warning(
                            self._frames / max(elapsed, 1e-6))

                if file_interval:
                    lag = file_interval - (time.perf_counter() - frame_started)
                    if lag > 0:
                        self._stop.wait(lag)
        finally:
            cap.release()

    def _eye_risk(self, cv2, frame, crop, landmarks):
        """Measure eyelid closure and return (risk, display_patch, state).

        Risk is PERCLOS - the fraction of recent frames with eyes closed - not the
        instantaneous probability, so a blink cannot trigger an alert while a
        sustained closure can. When no face is available the eyes are unknown, so
        PERCLOS is held rather than assumed open: guessing "awake" is the unsafe
        direction for this device.
        """
        import numpy as np

        from .eyestate import eye_patch_boxes

        h, w = frame.shape[:2]
        boxes = eye_patch_boxes(crop, landmarks)
        patches = []
        for bx, by, bside in boxes:
            x0c, y0c = max(0, bx), max(0, by)
            x1c, y1c = min(w, bx + bside), min(h, by + bside)
            if x1c - x0c >= 6 and y1c - y0c >= 6:
                patches.append(cv2.resize(frame[y0c:y1c, x0c:x1c], (32, 32)))

        if len(patches) < 2:
            state = dict(self._eyes)
            state.update({'available': False, 'perclos': self._perclos.value,
                          'window': self._perclos.window})
            grey = np.full((32, 64), 0.5, np.float32)
            return self._perclos.value, grey, state, []

        right_p = self._eye.p_closed(patches[0])
        left_p = self._eye.p_closed(patches[1])
        # Mean of both eyes: one eye can be occluded by head angle or glare.
        closed = (right_p + left_p) / 2.0
        perclos = self._perclos.update(closed)

        # Fuse eye closure with yawning, long blinks and head nodding; the returned
        # risk is the fused score, so behaviour beyond eye closure can raise an alert
        # and an involuntary sneeze can suppress one.
        from .behavior import face_geometry
        geometry = face_geometry(landmarks)
        behavior = self._behavior.update(closed, geometry, perclos)

        pair = np.hstack([cv2.cvtColor(p, cv2.COLOR_BGR2GRAY) for p in patches])
        state = {'left_closed': round(left_p, 3), 'right_closed': round(right_p, 3),
                 'closed': round(closed, 3), 'perclos': round(perclos, 3),
                 'window': self._perclos.window, 'available': True,
                 'score': behavior.score, 'mouth_open': behavior.mouth_open,
                 'head_down': behavior.head_down, 'suppressed': behavior.suppressed,
                 'closure_s': behavior.closure_s,
                 'baselines_ready': behavior.baselines_ready,
                 'blink_rate': behavior.blink_rate, 'long_blink_rate': behavior.long_blink_rate,
                 'yawn_rate': behavior.yawn_rate, 'nod_rate': behavior.nod_rate,
                 'sneeze_count': behavior.sneeze_count,
                 'roll': round(geometry.roll, 1) if geometry.valid else None}
        self._encode_eyes(cv2, patches)
        return behavior.score, (pair.astype(np.float32) / 255.0), state, behavior.events

    def _encode_eyes(self, cv2, patches):
        big = [cv2.resize(p, (96, 96), interpolation=cv2.INTER_NEAREST) for p in patches]
        import numpy as np
        sheet = np.hstack(big)
        ok, buf = cv2.imencode('.jpg', sheet)
        if ok:
            with self._lock:
                self._eye_jpeg = buf.tobytes()

    def eye_jpeg(self):
        with self._lock:
            return self._eye_jpeg

    @staticmethod
    def _slow_capture_warning(fps):
        if not fps or fps >= SLOW_CAPTURE_FPS:
            return None
        return (f'Camera is only delivering {fps:.1f} fps. On Windows this is usually the launcher, '
                'not your hardware: starting the dashboard through the installed `drowsyguard` '
                'console script can throttle capture. Run `python -m drowsyguard.cli live` instead, '
                'and compare with `python -m drowsyguard.cli camera-test`.')

    def _encode(self, cv2, frame, arr, p, trigger, streak, required, cooldown_left, cooldown,
                crop, landmarks=(), face_state=None):
        x0, y0, side = crop
        alerting = cooldown_left > 0
        colour = (60, 60, 235) if p >= trigger else (90, 190, 90)  # BGR
        # Dashed-looking corner brackets read as "tracking" rather than a static crop.
        held = bool(face_state and face_state.get('held'))
        box_colour = (0, 200, 255) if held else colour
        cv2.rectangle(frame, (x0, y0), (x0 + side, y0 + side), box_colour, 2)
        arm = max(12, side // 8)
        for px, py, dx, dy in ((x0, y0, 1, 1), (x0 + side, y0, -1, 1),
                               (x0, y0 + side, 1, -1), (x0 + side, y0 + side, -1, -1)):
            cv2.line(frame, (px, py), (px + dx * arm, py), box_colour, 4)
            cv2.line(frame, (px, py), (px, py + dy * arm), box_colour, 4)
        for lx, ly in landmarks:
            cv2.circle(frame, (int(lx), int(ly)), 2, (255, 220, 90), -1, cv2.LINE_AA)

        # Keep readouts off the face: above the box when there is room, else below.
        label = 'ALERT (cooldown)' if alerting else ('DROWSY' if p >= trigger else 'ALERT-OK')
        top_text = f'p(drowsy) {p:.3f}  {label}'
        if y0 >= 30:
            cv2.putText(frame, top_text, (x0, y0 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2, cv2.LINE_AA)
        else:
            cv2.putText(frame, top_text, (x0 + 6, y0 + side + 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2, cv2.LINE_AA)
        if face_state:
            fy = y0 + side + 20 if y0 >= 30 else max(y0 - 8, 14)
            cv2.putText(frame, face_state['source'], (x0, min(fy, frame.shape[0] - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, box_colour, 1, cv2.LINE_AA)

        ok, buf = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        frame_jpeg = buf.tobytes() if ok else None

        # Standardized input is roughly -3..3, so rescale for display rather than
        # clipping it to a black square.
        if self.model is not None and self.model.normalize:
            lo, hi = float(arr.min()), float(arr.max())
            shown = (arr - lo) / max(hi - lo, 1e-5)
        else:
            shown = arr
        small = (shown * 255.0).clip(0, 255).astype(np.uint8)
        big = cv2.resize(small, (192, 192), interpolation=cv2.INTER_NEAREST)
        ok2, buf2 = cv2.imencode('.jpg', big)
        input_jpeg = buf2.tobytes() if ok2 else None

        with self._lock:
            if frame_jpeg:
                self._frame_jpeg = frame_jpeg
            if input_jpeg:
                self._input_jpeg = input_jpeg

    # -- accessors ---------------------------------------------------------
    def frame_jpeg(self):
        with self._lock:
            return self._frame_jpeg

    def input_jpeg(self):
        with self._lock:
            return self._input_jpeg

    def configure(self, trigger=None, required=None, cooldown=None, zoom=None, face_detect=None,
                  perclos_window=None, eye_closed_threshold=None):
        with self._lock:
            if self._perclos is not None:
                if perclos_window is not None:
                    self._perclos.resize(perclos_window)
                if eye_closed_threshold is not None:
                    self._perclos.closed_threshold = min(max(float(eye_closed_threshold), 0.0), 1.0)
            if face_detect is not None and self._tracker is not None:
                self.face_detect = bool(face_detect)
                if not self.face_detect:
                    self._tracker.reset()
            if trigger is not None:
                self.filter.trigger = max(0.0, min(1.0, float(trigger)))
            if required is not None:
                self.filter.required = max(1, int(required))
            if cooldown is not None:
                self.filter.cooldown = max(0, int(cooldown))
            if zoom is not None:
                self.zoom = min(max(float(zoom), 0.05), 1.0)
            return self._config_locked()

    def reset(self):
        with self._lock:
            self.filter.reset()
            if self._perclos is not None:
                self._perclos.reset()
            if self._behavior is not None:
                self._behavior.reset()
            self._behavior_log.clear()
            self._alerts.clear()
            self._alert_count = 0
            self._history.clear()

    def _config_locked(self):
        return {'trigger': self.filter.trigger,
                'required': self.filter.required,
                'cooldown': self.filter.cooldown,
                'zoom': round(self.zoom, 2),
                'face_detect': bool(self._tracker is not None and self.face_detect),
                'perclos_window': self._perclos.window if self._perclos else None,
                'eye_closed_threshold': self._perclos.closed_threshold if self._perclos else None}

    def snapshot(self):
        with self._lock:
            return {
                'running': bool(self._thread and self._thread.is_alive()),
                'error': self._error,
                # Both can apply at once, and neither should silence the other.
                'warning': ' '.join(w for w in (self._tracker_error, self._eye_error,
                                                self._capture_warning) if w) or None,
                'p_drowsy': round(self._p, 4),
                'state': self._state_locked(),
                'streak': self.filter.streak,
                'cooldown_left': self.filter.cooldown_left,
                'config': self._config_locked(),
                'history': list(self._history),
                'alerts': list(self._alerts)[-10:][::-1],
                'behavior_events': list(self._behavior_log)[-12:][::-1],
                'alert_count': self._alert_count,
                'frames': self._frames,
                'fps': round(self._fps, 1),
                'infer_ms': round(self._infer_ms, 2),
                'mode': self.mode,
                'eyes': dict(self._eyes),
                'model': ({'kind': 'eye-state', 'trained': True,
                           'source': f'open-closed-eye-0001 (Intel, Apache-2.0) + PERCLOS/{self._perclos.window}',
                           'normalize': False}
                          if self._eye is not None else
                          {'kind': self.model.kind, 'trained': self.model.trained,
                           'source': self.model.source, 'normalize': self.model.normalize}),
                'image_size': self.image_size,
                'crop': self._crop,
                'camera': dict(self._camera),
                'face': dict(self._face),
                'face_detect': bool(self._tracker is not None and self.face_detect),
            }

    def _state_locked(self):
        if self.filter.cooldown_left > 0:
            return 'cooldown'
        if self._p >= self.filter.trigger:
            return 'drowsy'
        return 'ok'
