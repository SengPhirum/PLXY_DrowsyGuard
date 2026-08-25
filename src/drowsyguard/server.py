"""FastAPI app serving the live development dashboard.

Local research tool: binds to 127.0.0.1 by default and has no authentication,
so only pass --host 0.0.0.0 on a network you trust.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from .live import LiveEngine

STATIC = Path(__file__).resolve().parent / 'static'
BOUNDARY = 'drowsyguardframe'


def create_app(engine: LiveEngine) -> FastAPI:
    app = FastAPI(title='DrowsyGuard Live', docs_url=None, redoc_url=None)

    @app.on_event('startup')
    def _startup():
        engine.start()

    @app.on_event('shutdown')
    def _shutdown():
        engine.stop()

    @app.get('/')
    def index():
        return FileResponse(STATIC / 'index.html')

    @app.get('/app-icon.webp', include_in_schema=False)
    def app_icon():
        return FileResponse(STATIC / 'app-icon.webp', media_type='image/webp')

    @app.get('/favicon.ico', include_in_schema=False)
    def favicon():
        return FileResponse(
            STATIC / 'favicon.ico', media_type='image/vnd.microsoft.icon',
            headers={'Cache-Control': 'public, max-age=86400'},
        )

    def _mjpeg(getter, fps=20.0):
        interval = 1.0 / fps
        while True:
            jpeg = getter()
            if jpeg:
                yield (b'--' + BOUNDARY.encode() + b'\r\n'
                       b'Content-Type: image/jpeg\r\n'
                       b'Content-Length: ' + str(len(jpeg)).encode() + b'\r\n\r\n'
                       + jpeg + b'\r\n')
            time.sleep(interval)

    @app.get('/stream')
    def stream():
        return StreamingResponse(_mjpeg(engine.frame_jpeg),
                                 media_type=f'multipart/x-mixed-replace; boundary={BOUNDARY}')

    @app.get('/input-stream')
    def input_stream():
        return StreamingResponse(_mjpeg(engine.input_jpeg, fps=10.0),
                                 media_type=f'multipart/x-mixed-replace; boundary={BOUNDARY}')

    @app.get('/eye-stream')
    def eye_stream():
        return StreamingResponse(_mjpeg(engine.eye_jpeg, fps=10.0),
                                 media_type=f'multipart/x-mixed-replace; boundary={BOUNDARY}')

    @app.get('/snapshot.jpg')
    def snapshot_jpg():
        jpeg = engine.frame_jpeg()
        if not jpeg:
            return Response(status_code=503)
        return Response(jpeg, media_type='image/jpeg')

    @app.get('/state')
    def state():
        return JSONResponse(engine.snapshot())

    @app.post('/config')
    def config(payload: dict):
        return JSONResponse(engine.configure(
            trigger=payload.get('trigger'),
            required=payload.get('required'),
            cooldown=payload.get('cooldown'),
            zoom=payload.get('zoom'),
            face_detect=payload.get('face_detect'),
            perclos_window=payload.get('perclos_window'),
            eye_closed_threshold=payload.get('eye_closed_threshold'),
        ))

    @app.post('/reset')
    def reset():
        engine.reset()
        return JSONResponse({'ok': True})

    return app


def serve(source=0, checkpoint=None, config_path=None, host='127.0.0.1', port=8000,
          trigger=None, required=None, cooldown=None, zoom=1.0,
          face_detect=True, face_margin=0.0, face_model=None,
          mode='eye', eye_model=None, perclos_window=90, eye_closed_threshold=0.5):
    import uvicorn
    import yaml
    from .risk import DEFAULT_COOLDOWN, DEFAULT_REQUIRED, DEFAULT_TRIGGER

    image_size = 64
    if config_path and Path(config_path).exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            image_size = yaml.safe_load(f).get('image_size', 64)

    engine = LiveEngine(
        source=source, checkpoint=checkpoint, image_size=image_size,
        trigger=DEFAULT_TRIGGER if trigger is None else trigger,
        required=DEFAULT_REQUIRED if required is None else required,
        cooldown=DEFAULT_COOLDOWN if cooldown is None else cooldown,
        zoom=zoom, face_detect=face_detect, face_margin=face_margin, face_model=face_model,
        mode=mode, eye_model=eye_model, perclos_window=perclos_window,
        eye_closed_threshold=eye_closed_threshold,
    )
    for err in (engine._tracker_error, engine._eye_error):
        if err:
            print(f'WARNING: {err}')
    # The installed console-script wrapper can throttle webcam capture on Windows;
    # `python -m drowsyguard.cli live` runs the identical code without that.
    if sys.platform == 'win32' and not Path(sys.argv[0]).suffix.lower() == '.py' \
            and 'drowsyguard' in Path(sys.argv[0]).name.lower():
        print('NOTE: if capture is slow, run `python -m drowsyguard.cli live` instead.')
    if engine.mode == 'eye':
        print(f'mode=eye  open-closed-eye-0001 + PERCLOS over {perclos_window} frames '
              '(risk = fraction of recent frames with eyes closed)')
    elif engine.model is not None and not engine.model.trained:
        print('WARNING: no checkpoint loaded - using randomly initialized weights.')
        print('         Probabilities are meaningless until you train and pass --checkpoint.')
    print(f'DrowsyGuard live dashboard: http://{host}:{port}')
    uvicorn.run(create_app(engine), host=host, port=port, log_level='warning')
