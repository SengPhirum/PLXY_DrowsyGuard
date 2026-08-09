from pathlib import Path
import subprocess, sys, yaml, torch
from .model import TinyDrowsyNet


def export_onnx(config_path, checkpoint):
    with open(config_path, 'r', encoding='utf-8') as f: cfg = yaml.safe_load(f)
    state = torch.load(checkpoint, map_location='cpu')
    model = TinyDrowsyNet(); model.load_state_dict(state['model']); model.eval()
    dummy = torch.zeros(1, 1, cfg['image_size'], cfg['image_size'])
    out = Path(cfg['onnx_output']); out.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(model, dummy, out, input_names=['input'], output_names=['logits'], opset_version=13)
    print(out)


def quantize_espdl(onnx_path, calib_dir, output_path):
    script = Path(__file__).resolve().parents[2] / 'scripts' / 'quantize_espdl.py'
    cmd = [sys.executable, str(script), '--onnx', onnx_path, '--calib', calib_dir, '--output', output_path]
    raise SystemExit(subprocess.call(cmd))
