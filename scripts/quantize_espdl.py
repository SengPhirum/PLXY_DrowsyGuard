"""Quantize ONNX to ESP-DL format using Espressif ESP-PPQ.

ESP-PPQ's public Python API can change between releases, so this script detects the
available entry point and gives a clear failure message rather than silently producing
an incompatible model. Pin the exact ESP-PPQ version in the hardware-validation phase.

**Not needed for the eye model, and the install line below does not work.** Checked
2026-08-23: esp-ppq is not published to PyPI (`pip index versions esp-ppq` finds
nothing), it lives in Espressif's git, and quantizing would also need a calibration
set this repo does not have. The eye model therefore skips ESP-DL entirely and runs
in float32 - see scripts/export_eye_model.py and firmware/esp32s3/main/eye_model.h.
This script is kept for whatever model comes next, not because anything needs it.
"""
import argparse, importlib


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--onnx',required=True); ap.add_argument('--calib',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    try:
        mod = importlib.import_module('esp_ppq')
    except ImportError:
        raise SystemExit('esp-ppq is not installed, and it is NOT on PyPI - install it '
                         'from Espressif git. For the eye model you do not need it at '
                         'all: see scripts/export_eye_model.py.')
    fn = getattr(mod, 'espdl_quantize_onnx', None)
    if fn is None:
        raise SystemExit('Installed esp-ppq does not expose espdl_quantize_onnx at top level. Follow the version-specific Espressif quantization example and update this adapter.')
    raise SystemExit('ESP-PPQ detected. Hardware export adapter must be pinned to the exact ESP-PPQ version selected for the thesis before calling its calibration API. See docs/DEPLOYMENT.md.')

if __name__=='__main__': main()
