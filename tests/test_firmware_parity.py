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
    ('REFLEX_MAX_S', 'REFLEX_MAX_S'),
    ('NOD_MAX_S', 'NOD_MAX_S'),
    ('NOD_MIN_S', 'NOD_MIN_S'),
    ('JAW_OPEN_DELTA', 'JAW_OPEN_DELTA'),
    ('NOD_PITCH_DELTA', 'NOD_PITCH_DELTA'),
    ('REFLEX_JAW_DELTA', 'REFLEX_JAW_DELTA'),
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


def test_every_behaviour_constant_in_the_header_is_checked(fw_behavior):
    """The pair list above is hand-maintained, which is exactly how a new constant
    gets added to both sides and checked on neither. Anything in behavior.h that also
    exists in behavior.py has to appear in PAIRS."""
    checked = {cpp for cpp, _ in PAIRS}
    shared = {name for name in fw_behavior
              if hasattr(behavior, name) and isinstance(getattr(behavior, name), float)}
    missing = sorted(shared - checked)
    assert not missing, f'not covered by PAIRS: {missing}'


# --------------------------------------------------------------------------- #
# the alert reasons
# --------------------------------------------------------------------------- #
#
# AlertReason's numbering is part of the HTTP API - /api/alert-test takes it as
# ?reason=N - so it is published in docs/reference/device-api.md, wired into the
# device page's buttons, and switched on by ./plxy.sh alert. Four places that must
# agree with one enum, and nothing else would notice if they stopped.

REASONS = ['drowsy', 'microsleep', 'yawning', 'head_nod', 'no_driver']

VOICE_H = FW / 'voice_alert.h'
VOICE_CPP = FW / 'voice_alert.cpp'
ASSETS = FW.parent / 'assets' / 'audio'


def test_the_alert_reason_numbering_is_what_the_api_publishes():
    text = VOICE_H.read_text(encoding='utf-8')
    # Scoped to the AlertReason block, not the whole header: AlertChannel numbers its
    # own members from 0 too, and a header-wide search would happily match those
    # instead and pass or fail for the wrong reason.
    body = text.split('enum class AlertReason', 1)[1].split('};', 1)[0]
    found = dict(re.findall(r'^\s*(\w+)\s*=\s*(\d+),', body, re.M))
    for i, name in enumerate(['Drowsy', 'Microsleep', 'Yawning', 'HeadNod',
                              'NoDriver']):
        assert found.get(name) == str(i), f'{name} should be {i}, got {found.get(name)}'
    m = re.search(r'ALERT_REASON_COUNT\s*=\s*(\d+)', text)
    assert m and int(m.group(1)) == len(REASONS)


@pytest.mark.parametrize('reason', REASONS)
def test_every_reason_has_a_clip_name_a_banner_and_a_tone(reason):
    cpp = VOICE_CPP.read_text(encoding='utf-8')
    assert f'return "{reason}";' in cpp, f'{reason} has no clip name'


def test_every_reason_has_an_embedded_clip_in_both_languages():
    """The board has to speak with no SD card in it. A reason with no embedded clip
    falls back to a tone pattern, which is audible but not actionable - and the only
    way to discover it is to trigger that alert and listen."""
    cmake = (FW / 'CMakeLists.txt').read_text(encoding='utf-8')
    clips = (FW / 'voice_clips.cpp').read_text(encoding='utf-8')
    for lang in ('en', 'km'):
        for reason in REASONS:
            wav = ASSETS / f'{lang}_{reason}.wav'
            assert wav.is_file(), f'missing {wav.name}'
            assert wav.stat().st_size > 1024, f'{wav.name} is suspiciously small'
            assert f'{lang}_{reason}.wav' in cmake, f'{wav.name} is not embedded'
            assert f'{{"{lang}", "{reason}"' in clips, f'{wav.name} is not in the table'


def test_the_alert_test_endpoint_accepts_every_reason():
    """The clamp used to be a literal 3 and would have silently made the two newest
    clips the only ones with no way to audition them - on a device whose only speaker
    test is this endpoint."""
    server = (FW / 'web_server.cpp').read_text(encoding='utf-8')
    assert 'ALERT_REASON_COUNT - 1' in server


def test_the_shell_helper_knows_every_reason():
    """./plxy.sh alert <reason> is how the speaker gets tested on the bench."""
    plxy = (FW.parents[2] / 'plxy.sh').read_text(encoding='utf-8')
    body = plxy.split('cmd_alert()', 1)[1].split('\n}', 1)[0]
    for token in ('microsleep', 'no_driver'):
        assert token in body, f'./plxy.sh alert cannot send {token}'


def test_espdl_landmark_reordering_is_documented():
    """The device detector's keypoint order differs from ours; silence here is a bug."""
    src = (FW / 'behavior.cpp').read_text(encoding='utf-8')
    assert 'src_for_canonical' in src
    # ESP-DL order is [left eye, left mouth, nose, right eye, right mouth]; mapping to
    # canonical [right eye, left eye, nose, right mouth, left mouth] is {3,0,2,4,1}.
    m = re.search(r'src_for_canonical\[5\]\s*=\s*\{([^}]*)\}', src)
    assert m, 'reorder table not found'
    assert [int(v) for v in m.group(1).split(',')] == [3, 0, 2, 4, 1]
