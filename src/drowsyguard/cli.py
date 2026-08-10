import argparse, importlib.util, platform, sys
from .data import prepare_dataset
from .train import train_model, evaluate_checkpoint
from .export import export_onnx, quantize_espdl


def doctor():
    print('python', sys.version.split()[0]); print('platform', platform.platform())
    for m in ['torch','onnx','onnxruntime','yaml','PIL']:
        print(f'{m}:', 'OK' if importlib.util.find_spec(m) else 'MISSING')
    print('esp_ppq:', 'OK' if importlib.util.find_spec('esp_ppq') else 'OPTIONAL/MISSING (needed only for .espdl quantization)')
    live = [m for m in ('cv2', 'fastapi', 'uvicorn') if not importlib.util.find_spec(m)]
    print('live UI:', 'OK' if not live else f'MISSING {live} (pip install -e ".[live]" for `drowsyguard live`)')


def main():
    p = argparse.ArgumentParser(prog='drowsyguard')
    s = p.add_subparsers(dest='cmd', required=True)
    s.add_parser('doctor')
    a=s.add_parser('prepare'); a.add_argument('--input',required=True); a.add_argument('--output',required=True); a.add_argument('--train',type=float,default=.70); a.add_argument('--val',type=float,default=.15); a.add_argument('--seed',type=int,default=42)
    a.add_argument('--stride',type=int,default=1,help='keep every Nth frame when a class dir contains videos')
    a.add_argument('--link',action='store_true',help='hardlink instead of copy where possible')
    a.add_argument('--overwrite',action='store_true',help='replace an existing split (required to re-split)')
    a=s.add_parser('import-ddd', help='convert the flat DDD dataset into the subject-based raw layout')
    a.add_argument('--input',required=True); a.add_argument('--output',default='data/raw')
    a.add_argument('--copy',action='store_true',help='copy files instead of hardlinking')
    a=s.add_parser('train'); a.add_argument('--config',default='configs/train.yaml')
    a=s.add_parser('evaluate'); a.add_argument('--config',default='configs/train.yaml'); a.add_argument('--checkpoint',default='models/best.pt')
    a.add_argument('--per-subject',action='store_true',help='break accuracy down by driver')
    a.add_argument('--split',choices=['train','val','test'],default=None)
    a=s.add_parser('export-onnx'); a.add_argument('--config',default='configs/train.yaml'); a.add_argument('--checkpoint',default='models/best.pt')
    a=s.add_parser('quantize-espdl'); a.add_argument('--onnx',required=True); a.add_argument('--calib',required=True); a.add_argument('--output',required=True)
    a=s.add_parser('fetch-models', help='download the YuNet face detector used by the dashboard')
    a.add_argument('--output', default=None)
    a=s.add_parser('camera-test', help='benchmark webcam capture backends')
    a.add_argument('--index', type=int, default=0); a.add_argument('--frames', type=int, default=20)
    a=s.add_parser('live', help='browser dashboard for real-time webcam testing')
    a.add_argument('--source', default='0', help='webcam index (0, 1, ...) or path to a video file')
    a.add_argument('--mode', choices=['eye','face'], default='eye',
                   help="'eye': measure eyelid closure + PERCLOS (default, no checkpoint needed); "
                        "'face': whole-face drowsiness CNN, needs --checkpoint")
    a.add_argument('--perclos-window', type=int, default=90, help='frames in the PERCLOS window (~3s at 30fps)')
    a.add_argument('--eye-closed-threshold', type=float, default=0.5)
    a.add_argument('--eye-model', default=None)
    a.add_argument('--checkpoint', default=None, help='face mode only: models/best.pt or a .onnx model')
    a.add_argument('--config', default='configs/train.yaml')
    a.add_argument('--host', default='127.0.0.1'); a.add_argument('--port', type=int, default=8000)
    a.add_argument('--trigger', type=float, default=None); a.add_argument('--required', type=int, default=None)
    a.add_argument('--cooldown', type=int, default=None)
    a.add_argument('--zoom', type=float, default=1.0,
                   help='centre-crop fraction used only when no face is detected')
    a.add_argument('--no-face-detect', action='store_true', help='disable face detection and tracking')
    a.add_argument('--face-margin', type=float, default=0.0,
                   help='expand the detected face box by this fraction (0 matches DDD framing)')
    a.add_argument('--face-model', default=None)
    args=p.parse_args()
    if args.cmd=='doctor': doctor()
    elif args.cmd=='prepare':
        r=prepare_dataset(args.input,args.output,args.train,args.val,args.seed,args.stride,args.link,args.overwrite)
        for split, names in r['splits'].items():
            c=r['counts'][split]
            print(f"{split}: {len(names)} subjects  alert={c['alert']} drowsy={c['drowsy']}  {sorted(names)}")
        if r['videos_decoded']: print(f"videos decoded: {r['videos_decoded']} (stride={args.stride})")
    elif args.cmd=='import-ddd':
        from .ingest import import_ddd, summarize_import
        print(summarize_import(import_ddd(args.input, args.output, link=not args.copy)))
    elif args.cmd=='train': train_model(args.config)
    elif args.cmd=='evaluate': evaluate_checkpoint(args.config,args.checkpoint,args.per_subject,args.split)
    elif args.cmd=='export-onnx': export_onnx(args.config,args.checkpoint)
    elif args.cmd=='quantize-espdl': quantize_espdl(args.onnx,args.calib,args.output)
    elif args.cmd=='fetch-models':
        from .facedetect import fetch_model
        from .eyestate import fetch_eye_model
        print(f'downloaded {fetch_model()}  (YuNet face detector)')
        print(f'downloaded {fetch_eye_model()}  (open-closed-eye-0001, Intel, Apache-2.0)')
    elif args.cmd=='camera-test':
        from .live import benchmark_backends
        print(f'sys.argv[0]={sys.argv[0]}')
        for r in benchmark_backends(args.index, args.frames):
            print(f"  {r['backend']:6s} {r['thread']:7s} {r['fps']:7.1f} fps")
    elif args.cmd=='live':
        from .server import serve
        serve(source=args.source, checkpoint=args.checkpoint, config_path=args.config,
              host=args.host, port=args.port, trigger=args.trigger,
              required=args.required, cooldown=args.cooldown, zoom=args.zoom,
              face_detect=not args.no_face_detect, face_margin=args.face_margin,
              face_model=args.face_model, mode=args.mode, eye_model=args.eye_model,
              perclos_window=args.perclos_window,
              eye_closed_threshold=args.eye_closed_threshold)

if __name__ == '__main__': main()
