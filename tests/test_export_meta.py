"""An exported .onnx must carry its preprocessing, so inference cannot mismatch."""
import json

import pytest
import torch

from drowsyguard.export import export_onnx, load_preprocess, sidecar_path
from drowsyguard.model import TinyDrowsyNet

pytest.importorskip('onnx')


def _write(tmp_path, normalize):
    ckpt = tmp_path / 'ck.pt'
    cfg = {'image_size': 64, 'normalize': normalize, 'class_names': ['alert', 'drowsy'],
           'onnx_output': str(tmp_path / 'm.onnx')}
    torch.save({'model': TinyDrowsyNet().state_dict(), 'cfg': cfg}, ckpt)
    cfg_path = tmp_path / 'cfg.yaml'
    import yaml
    with open(cfg_path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(cfg, f)
    export_onnx(str(cfg_path), str(ckpt))
    return tmp_path / 'm.onnx'


@pytest.mark.parametrize('normalize', [True, False])
def test_sidecar_records_normalize(tmp_path, normalize):
    onnx_path = _write(tmp_path, normalize)
    assert onnx_path.exists()
    meta = load_preprocess(onnx_path)
    assert meta is not None
    assert meta['normalize'] is normalize
    assert meta['image_size'] == 64
    assert json.loads(sidecar_path(onnx_path).read_text())['normalize'] is normalize


def test_missing_sidecar_returns_none(tmp_path):
    assert load_preprocess(tmp_path / 'absent.onnx') is None


def test_model_runner_picks_up_sidecar(tmp_path):
    onnx_path = _write(tmp_path, True)
    from drowsyguard.live import ModelRunner
    runner = ModelRunner(str(onnx_path), image_size=64)
    assert runner.kind == 'onnx' and runner.trained
    # Without the sidecar this would default to False and serve raw input.
    assert runner.normalize is True


def test_torch_checkpoint_normalize_round_trips(tmp_path):
    ckpt = tmp_path / 'ck.pt'
    torch.save({'model': TinyDrowsyNet().state_dict(), 'cfg': {'normalize': True}}, ckpt)
    from drowsyguard.live import ModelRunner
    assert ModelRunner(str(ckpt)).normalize is True
