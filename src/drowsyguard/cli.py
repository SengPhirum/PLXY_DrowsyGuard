import argparse, importlib.util, platform, sys
from .data import prepare_dataset
from .train import train_model, evaluate_checkpoint
from .export import export_onnx, quantize_espdl


def doctor():
    print('python', sys.version.split()[0]); print('platform', platform.platform())
    for m in ['torch','onnx','onnxruntime','yaml','PIL']:
        print(f'{m}:', 'OK' if importlib.util.find_spec(m) else 'MISSING')
    print('esp_ppq:', 'OK' if importlib.util.find_spec('esp_ppq') else 'OPTIONAL/MISSING (needed only for .espdl quantization)')


def main():
    p = argparse.ArgumentParser(prog='drowsyguard')
    s = p.add_subparsers(dest='cmd', required=True)
    s.add_parser('doctor')
    a=s.add_parser('prepare'); a.add_argument('--input',required=True); a.add_argument('--output',required=True); a.add_argument('--train',type=float,default=.70); a.add_argument('--val',type=float,default=.15); a.add_argument('--seed',type=int,default=42)
    a=s.add_parser('train'); a.add_argument('--config',default='configs/train.yaml')
    a=s.add_parser('evaluate'); a.add_argument('--config',default='configs/train.yaml'); a.add_argument('--checkpoint',default='models/best.pt')
    a=s.add_parser('export-onnx'); a.add_argument('--config',default='configs/train.yaml'); a.add_argument('--checkpoint',default='models/best.pt')
    a=s.add_parser('quantize-espdl'); a.add_argument('--onnx',required=True); a.add_argument('--calib',required=True); a.add_argument('--output',required=True)
    args=p.parse_args()
    if args.cmd=='doctor': doctor()
    elif args.cmd=='prepare': print(prepare_dataset(args.input,args.output,args.train,args.val,args.seed))
    elif args.cmd=='train': train_model(args.config)
    elif args.cmd=='evaluate': evaluate_checkpoint(args.config,args.checkpoint)
    elif args.cmd=='export-onnx': export_onnx(args.config,args.checkpoint)
    elif args.cmd=='quantize-espdl': quantize_espdl(args.onnx,args.calib,args.output)

if __name__ == '__main__': main()
