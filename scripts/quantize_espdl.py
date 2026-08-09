"""Quantize ONNX to ESP-DL format using Espressif ESP-PPQ.

ESP-PPQ's public Python API can change between releases, so this script detects the
available entry point and gives a clear failure message rather than silently producing
an incompatible model. Pin the exact ESP-PPQ version in the hardware-validation phase.
"""
import argparse, importlib


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--onnx',required=True); ap.add_argument('--calib',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    try:
        mod = importlib.import_module('esp_ppq')
    except ImportError:
        raise SystemExit('esp-ppq is not installed. Install with: pip install esp-ppq')
    fn = getattr(mod, 'espdl_quantize_onnx', None)
    if fn is None:
        raise SystemExit('Installed esp-ppq does not expose espdl_quantize_onnx at top level. Follow the version-specific Espressif quantization example and update this adapter.')
    raise SystemExit('ESP-PPQ detected. Hardware export adapter must be pinned to the exact ESP-PPQ version selected for the thesis before calling its calibration API. See docs/DEPLOYMENT.md.')

if __name__=='__main__': main()
