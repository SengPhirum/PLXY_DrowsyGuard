from pathlib import Path
import json, subprocess, sys, yaml, torch
from .model import TinyDrowsyNet


def sidecar_path(onnx_path):
    return Path(onnx_path).with_suffix('.preprocess.json')


def load_preprocess(onnx_path):
    """Preprocessing recorded beside an .onnx, or None when absent."""
    p = sidecar_path(onnx_path)
    if not p.exists():
        return None
    with open(p, 'r', encoding='utf-8') as f:
        return json.load(f)


def export_onnx(config_path, checkpoint):
    with open(config_path, 'r', encoding='utf-8') as f: cfg = yaml.safe_load(f)
    state = torch.load(checkpoint, map_location='cpu')
    model = TinyDrowsyNet(); model.load_state_dict(state['model']); model.eval()
    dummy = torch.zeros(1, 1, cfg['image_size'], cfg['image_size'])
    out = Path(cfg['onnx_output']); out.parent.mkdir(parents=True, exist_ok=True)
    # torch>=2.9 defaults to the dynamo exporter, which needs the optional
    # `onnxscript` package. Prefer it when available, else fall back to the legacy
    # TorchScript exporter so export works on a plain install.
    kwargs = dict(input_names=['input'], output_names=['logits'], opset_version=13)
    try:
        import onnxscript  # noqa: F401
        torch.onnx.export(model, dummy, out, **kwargs)
    except ImportError:
        torch.onnx.export(model, dummy, out, dynamo=False, **kwargs)
    # An .onnx carries weights but not preprocessing. Record it alongside, or a
    # model trained on standardized input gets served raw input and quietly fails.
    saved_cfg = state.get('cfg') or {}
    meta = {'image_size': cfg['image_size'],
            'normalize': bool(saved_cfg.get('normalize', cfg.get('normalize', False))),
            'class_names': cfg.get('class_names', ['alert', 'drowsy']),
            'checkpoint': str(checkpoint)}
    with open(sidecar_path(out), 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2)
    print(out)
    print(sidecar_path(out), meta)


def quantize_espdl(onnx_path, calib_dir, output_path):
    script = Path(__file__).resolve().parents[2] / 'scripts' / 'quantize_espdl.py'
    cmd = [sys.executable, str(script), '--onnx', onnx_path, '--calib', calib_dir, '--output', output_path]
    raise SystemExit(subprocess.call(cmd))
