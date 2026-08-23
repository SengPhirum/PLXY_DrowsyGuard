"""The firmware mirrors the Python behaviour logic; these must not drift.

Thresholds are tuned on the desktop dashboard, so if the device uses different numbers
the tuning is meaningless. This parses the firmware headers rather than trusting a
comment, so a change on either side fails the build.
"""
import re
from pathlib import Path

import pytest

from drowsyguard import behavior, risk

FW = Path(__file__).resolve().parents[1] / 'firmware' / 'esp32s3' / 'main'
BEHAVIOR_H = FW / 'behavior.h'
RISK_H = FW / 'risk_filter.h'


def cpp_constants(path):
    """Pull `constexpr <type> NAME = <number>;` pairs out of a header."""
    text = path.read_text(encoding='utf-8')
    out = {}
    for m in re.finditer(r'constexpr\s+\w+\s+(\w+)\s*=\s*([-\d.]+)f?\s*;', text):
        out[m.group(1)] = float(m.group(2))
    return out


@pytest.fixture(scope='module')
def fw_behavior():
    assert BEHAVIOR_H.exists(), f'missing {BEHAVIOR_H}'
    return cpp_constants(BEHAVIOR_H)


PAIRS = [
    ('BLINK_MAX_S', 'BLINK_MAX_S'),
    ('MICROSLEEP_MIN_S', 'MICROSLEEP_MIN_S'),
    ('YAWN_MIN_S', 'YAWN_MIN_S'),
    ('SNEEZE_MAX_S', 'SNEEZE_MAX_S'),
    ('NOD_MAX_S', 'NOD_MAX_S'),
    ('NOD_MIN_S', 'NOD_MIN_S'),
    ('JAW_OPEN_DELTA', 'JAW_OPEN_DELTA'),
    ('NOD_PITCH_DELTA', 'NOD_PITCH_DELTA'),
    ('SNEEZE_JAW_DELTA', 'SNEEZE_JAW_DELTA'),
    ('YAWN_PEAK_DELTA', 'YAWN_PEAK_DELTA'),
    ('NOD_PEAK_DELTA', 'NOD_PEAK_DELTA'),
    ('NOD_NORM_DELTA', 'NOD_NORM_DELTA'),
    ('CLOSED_HYSTERESIS', 'CLOSED_HYSTERESIS'),
    ('CUE_GAP_S', 'CUE_GAP_S'),
    ('MOUTH_NARROW_W', 'MOUTH_NARROW_W'),
    ('RATE_WINDOW_S', 'RATE_WINDOW_S'),
    ('W_PERCLOS', 'W_PERCLOS'),
    ('W_LONG_BLINK', 'W_LONG_BLINK'),
    ('W_YAWN', 'W_YAWN'),
    ('W_NOD', 'W_NOD'),
    ('YAWN_RATE_FULL', 'YAWN_RATE_FULL'),
    ('NOD_RATE_FULL', 'NOD_RATE_FULL'),
    ('LONG_BLINK_RATE_FULL', 'LONG_BLINK_RATE_FULL'),
]


@pytest.mark.parametrize('cpp_name,py_name', PAIRS)
def test_behavior_constants_match_firmware(fw_behavior, cpp_name, py_name):
    assert cpp_name in fw_behavior, f'{cpp_name} missing from behavior.h'
    py_value = getattr(behavior, py_name)
    assert fw_behavior[cpp_name] == pytest.approx(py_value, rel=1e-6), (
        f'{cpp_name}={fw_behavior[cpp_name]} in firmware but {py_name}={py_value} in Python')


def test_fusion_weights_sum_to_one_in_both():
    py_sum = behavior.W_PERCLOS + behavior.W_LONG_BLINK + behavior.W_YAWN + behavior.W_NOD
    assert py_sum == pytest.approx(1.0)
    fw = cpp_constants(BEHAVIOR_H)
    fw_sum = fw['W_PERCLOS'] + fw['W_LONG_BLINK'] + fw['W_YAWN'] + fw['W_NOD']
    assert fw_sum == pytest.approx(1.0)


def test_risk_filter_defaults_match_firmware():
    """RiskFilter defaults live in a constructor signature, not a constexpr."""
    text = RISK_H.read_text(encoding='utf-8')
    m = re.search(r'RiskFilter\(float trigger=([\d.]+)f,\s*int required=(\d+),\s*int cooldown=(\d+)\)', text)
    assert m, 'could not parse RiskFilter defaults from risk_filter.h'
    trigger, required, cooldown = float(m.group(1)), int(m.group(2)), int(m.group(3))
    assert trigger == pytest.approx(risk.DEFAULT_TRIGGER)
    assert required == risk.DEFAULT_REQUIRED
    assert cooldown == risk.DEFAULT_COOLDOWN


def test_espdl_landmark_reordering_is_documented():
    """The device detector's keypoint order differs from ours; silence here is a bug."""
    src = (FW / 'behavior.cpp').read_text(encoding='utf-8')
    assert 'src_for_canonical' in src
    # ESP-DL order is [left eye, left mouth, nose, right eye, right mouth]; mapping to
    # canonical [right eye, left eye, nose, right mouth, left mouth] is {3,0,2,4,1}.
    m = re.search(r'src_for_canonical\[5\]\s*=\s*\{([^}]*)\}', src)
    assert m, 'reorder table not found'
    assert [int(v) for v in m.group(1).split(',')] == [3, 0, 2, 4, 1]
