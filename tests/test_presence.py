"""The "no driver" safety alert: when it fires, when it must not, and parity.

Three separate claims are being tested, and they fail in different ways:

1. **It fires.** A monitoring system that has silently stopped monitoring is worse
   than no monitoring system, because the driver believes they are covered. So an
   empty seat, once the tracking hold has already expired and the absence has
   persisted, has to produce exactly one announcement.

2. **It does not fire for the wrong reason.** Two cases, both of which would be
   actively harmful. A driver who glances at a mirror is not absent - that is what
   the debounce is for. And a camera that has stopped delivering frames is not an
   empty seat: announcing "no driver detected" at a driver who is sitting right
   there teaches them the device is broken, and it is - just not about them.

3. **The device and the dashboard agree.** `src/drowsyguard/presence.py` and
   `firmware/esp32s3/main/presence.cpp` are driven through identical timelines and
   must produce identical states, timers and alert edges. Timing logic is the
   easiest kind to transcribe subtly wrong: an accumulate-then-compare that becomes
   a compare-then-accumulate shifts every threshold by one frame.

The firmware half is skipped without a host C++ compiler; the Python half always
runs, so a checkout with no toolchain still exercises the logic.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from drowsyguard.presence import (PipelineHealth, PresenceConfig, PresenceMonitor,
                                  PresenceState)

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / 'firmware/esp32s3/main'

# 1/16 s, not the device's nominal 1/15.
#
# Both are ~15 fps and either would exercise the logic, but 0.0625 is exactly
# representable in binary and 1/15 is not - and the difference decides whether the
# parity comparison below can be exact. The firmware accumulates its timers in float32
# and Python in double, so with an inexact step the two totals drift apart in the last
# bits and cross a threshold on *different frames*: at 1/15 the run-up to a 5 s warm-up
# ends at 4.999998 on one side and 5.000000 on the other, and the states differ for
# exactly one step. That is a floating-point artefact, not a behavioural difference,
# and letting it into every assertion would mean every assertion needed slack - which
# would also hide a real one-frame disagreement. An exact step removes the artefact
# entirely; the inexact case gets one test of its own, below, with the slack it needs.
DT = 1.0 / 16.0

HARNESS = r'''
#include <cstdio>
#include "presence.h"

int main() {
    char op[16];
    PresenceMonitor mon;
    while (scanf("%15s", op) == 1) {
        if (op[0] == 'q') break;
        if (op[0] == 'r') {                 // reset
            mon.reset();
            printf("ok\n");
        } else if (op[0] == 'c') {          // cfg alert clear repeat warmup enabled
            PresenceConfig cfg;
            int enabled = 1;
            if (scanf("%f %f %f %f %d", &cfg.alert_after_s, &cfg.clear_s,
                      &cfg.repeat_s, &cfg.warmup_s, &enabled) != 5) return 1;
            cfg.enabled = enabled != 0;
            mon.configure(cfg);
            printf("ok\n");
        } else if (op[0] == 'u') {          // u present health dt
            int present = 0, health = 0;
            float dt = 0.0f;
            if (scanf("%d %d %f", &present, &health, &dt) != 3) return 1;
            const PresenceResult r = mon.update(present != 0,
                                                static_cast<PipelineHealth>(health), dt);
            printf("%s %d %.6f %.6f %u %s\n", presence_state_name(r.state),
                   r.alert ? 1 : 0, r.absent_s, r.present_s,
                   static_cast<unsigned>(r.alerts), presence_health_name(r.health));
        } else {
            return 1;
        }
        fflush(stdout);
    }
    return 0;
}
'''


def _compiler():
    for cc in ('g++', 'c++', 'clang++'):
        if shutil.which(cc):
            return [cc]
    try:
        import ziglang  # noqa: F401
    except ImportError:
        return None
    return [sys.executable, '-m', 'ziglang', 'c++']


@pytest.fixture(scope='module')
def rows(tmp_path_factory):
    cc = _compiler()
    if cc is None:
        pytest.skip('no host C++ compiler')
    d = tmp_path_factory.mktemp('presence')
    src = d / 'h.cpp'
    src.write_text(HARNESS, encoding='utf-8')
    exe = d / ('h.exe' if sys.platform == 'win32' else 'h')
    proc = subprocess.run(
        cc + ['-O2', '-std=c++17', f'-I{FIRMWARE}', str(src),
              str(FIRMWARE / 'presence.cpp'), '-o', str(exe)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail('presence.cpp does not compile on the host:\n' + proc.stderr[-4000:])

    def call(script):
        out = subprocess.run([str(exe)], input=script, capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        return [line.split() for line in out.stdout.strip().splitlines()]

    return call


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def run_py(steps, config=None):
    """steps: [(present, PipelineHealth, dt), ...] -> list of result tuples."""
    mon = PresenceMonitor(config)
    out = []
    for present, health, dt in steps:
        r = mon.update(present, health, dt)
        out.append((r.state.value, r.alert, round(r.absent_s, 6),
                    round(r.present_s, 6), r.alerts, r.health.value))
    return out


HEALTH_CODE = {PipelineHealth.OK: 0, PipelineHealth.MODEL_FAULT: 1,
               PipelineHealth.CAMERA_FAULT: 2}


def run_cpp(rows, steps, config=None):
    script = ['reset\n']
    if config is not None:
        script.append(f'cfg {config.alert_after_s} {config.clear_s} {config.repeat_s} '
                      f'{config.warmup_s} {1 if config.enabled else 0}\n')
    for present, health, dt in steps:
        script.append(f'u {1 if present else 0} {HEALTH_CODE[health]} {dt}\n')
    got = rows(''.join(script) + 'q\n')
    got = got[1:] if config is None else got[2:]      # drop the "ok" lines
    return [(r[0], r[1] == '1', round(float(r[2]), 6), round(float(r[3]), 6),
             int(r[4]), r[5]) for r in got]


def seconds(n_s, present, health=PipelineHealth.OK, dt=DT):
    """n_s seconds of one condition, as whole frames."""
    return [(present, health, dt)] * max(1, int(round(n_s / dt)))


def warm(dt=DT):
    """Enough healthy, occupied frames to get past the warm-up."""
    return seconds(PresenceConfig().warmup_s + 1.0, True, dt=dt)


# --------------------------------------------------------------------------- #
# it fires
# --------------------------------------------------------------------------- #

def test_an_empty_seat_produces_exactly_one_announcement():
    cfg = PresenceConfig()
    steps = warm() + seconds(10.0, False)
    out = run_py(steps, cfg)
    alerts = [i for i, r in enumerate(out) if r[1]]
    assert len(alerts) == 1, 'one alert per absence episode, not one per frame'
    assert out[-1][0] == PresenceState.NO_DRIVER.value
    assert out[-1][4] == 1


def test_the_announcement_waits_the_configured_time():
    cfg = PresenceConfig()
    out = run_py(warm() + seconds(10.0, False), cfg)
    fired_at = next(i for i, r in enumerate(out) if r[1])
    absent_when_fired = out[fired_at][2]
    assert absent_when_fired >= cfg.alert_after_s
    # And not much more than it: a threshold that overshoots by a second is a
    # different threshold.
    assert absent_when_fired < cfg.alert_after_s + 2 * DT


def test_the_threshold_is_configurable():
    slow = PresenceConfig(alert_after_s=8.0)
    out = run_py(warm() + seconds(20.0, False), slow)
    fired_at = next(i for i, r in enumerate(out) if r[1])
    assert out[fired_at][2] >= 8.0
    assert not any(r[1] for r in out[:fired_at])


def test_the_alert_can_be_turned_off_entirely():
    off = PresenceConfig(enabled=False)
    out = run_py(warm() + seconds(20.0, False), off)
    assert not any(r[1] for r in out)
    # The state still reports what is happening; only the announcement is withheld.
    assert out[-1][0] == PresenceState.NO_DRIVER.value


def test_repeats_are_off_by_default_and_can_be_enabled():
    out = run_py(warm() + seconds(120.0, False), PresenceConfig())
    assert sum(1 for r in out if r[1]) == 1

    repeating = PresenceConfig(repeat_s=20.0)
    out = run_py(warm() + seconds(120.0, False), repeating)
    assert sum(1 for r in out if r[1]) >= 5


def test_a_driver_returning_re_arms_the_alert():
    """One alert per *episode*, not per power cycle. A device that says its piece
    once and then never again on a long trip has the failure mode that matters."""
    cfg = PresenceConfig()
    steps = (warm() + seconds(6.0, False)
             + seconds(3.0, True) + seconds(6.0, False))
    out = run_py(steps, cfg)
    assert sum(1 for r in out if r[1]) == 2


# --------------------------------------------------------------------------- #
# it does not fire for the wrong reason
# --------------------------------------------------------------------------- #

def test_a_glance_away_is_not_an_absence():
    """Shorter than alert_after_s. This is the ordinary case and it must be silent."""
    cfg = PresenceConfig()
    out = run_py(warm() + seconds(2.0, False) + seconds(3.0, True), cfg)
    assert not any(r[1] for r in out)
    assert out[-1][0] == PresenceState.PRESENT.value


def test_a_camera_fault_is_never_reported_as_an_empty_seat():
    """The distinction the whole module exists for. Nothing about the cabin is known
    while the camera is down, so nothing about the cabin may be announced."""
    out = run_py(warm() + seconds(60.0, False, PipelineHealth.CAMERA_FAULT),
                 PresenceConfig())
    assert not any(r[1] for r in out)
    assert out[-1][0] == PresenceState.FAULT.value
    assert out[-1][5] == 'camera-fault'


def test_a_missing_model_is_never_reported_as_an_empty_seat():
    out = run_py(seconds(60.0, False, PipelineHealth.MODEL_FAULT), PresenceConfig())
    assert not any(r[1] for r in out)
    assert out[-1][0] == PresenceState.FAULT.value
    assert out[-1][5] == 'model-fault'


def test_a_fault_discards_the_absence_rather_than_freezing_it():
    """When the camera comes back the cabin may hold a completely different
    situation. Resuming a countdown that started before the fault would announce a
    conclusion drawn from evidence that no longer applies."""
    cfg = PresenceConfig()
    steps = (warm()
             + seconds(2.5, False)                                  # nearly there
             + seconds(1.0, False, PipelineHealth.CAMERA_FAULT)     # camera dies
             + seconds(1.0, False))                                 # and comes back
    out = run_py(steps, cfg)
    assert not any(r[1] for r in out), 'the pre-fault countdown must not carry over'
    assert out[-1][2] < cfg.alert_after_s


def test_nothing_is_announced_during_warm_up():
    """The camera, the models and the auto-exposure all settle over the first couple
    of seconds, and a face found - or missed - in that window is luck."""
    cfg = PresenceConfig()
    out = run_py(seconds(cfg.warmup_s - 1.0, False), cfg)
    assert not any(r[1] for r in out)
    assert {r[0] for r in out} == {PresenceState.WARMUP.value}
    # The timer still runs, so the page can show what is happening.
    assert out[-1][2] > 0


def test_a_single_flickering_detection_cannot_cancel_a_real_absence():
    """The quiet failure. Without the presence-side debounce, one spurious detection
    every couple of seconds resets the countdown forever and the alert never fires -
    and nothing anywhere reports that it is not firing."""
    cfg = PresenceConfig()
    steps = list(warm())
    for _ in range(20):
        steps += seconds(2.0, False)
        steps += [(True, PipelineHealth.OK, DT)]     # one frame of "someone"
    out = run_py(steps, cfg)
    assert any(r[1] for r in out), 'a flicker must not silence the alert'


def test_a_real_return_does_cancel_the_absence():
    """The counterpart: the debounce must not be so strong that a driver who comes
    back is still reported missing."""
    cfg = PresenceConfig()
    steps = warm() + seconds(2.0, False) + seconds(cfg.clear_s + 0.5, True)
    out = run_py(steps, cfg)
    assert out[-1][0] == PresenceState.PRESENT.value
    assert out[-1][2] == 0.0


def test_configuring_mid_episode_does_not_cancel_a_pending_alert():
    """A slider moved during a drive is a tuning action, not a new episode. Clearing
    a three-second-old absence because of it would silently cancel an alert that was
    about to be correct."""
    mon = PresenceMonitor(PresenceConfig())
    for step in warm() + seconds(2.5, False):
        mon.update(*step)
    mon.configure(PresenceConfig(alert_after_s=3.0))
    fired = False
    for step in seconds(2.0, False):
        fired = fired or mon.update(*step).alert
    assert fired


def test_reset_clears_everything():
    mon = PresenceMonitor(PresenceConfig())
    for step in warm() + seconds(10.0, False):
        mon.update(*step)
    mon.reset()
    r = mon.update(False, PipelineHealth.OK, DT)
    assert r.alerts == 0 and not r.alert and r.state is PresenceState.WARMUP


def test_a_zero_or_negative_dt_does_not_advance_the_clock():
    """A caller that has not measured a frame interval yet must not be able to jump
    the countdown with a garbage value."""
    mon = PresenceMonitor(PresenceConfig())
    for _ in range(1000):
        r = mon.update(False, PipelineHealth.OK, 0.0)
    assert r.absent_s == 0.0 and not r.alert
    for _ in range(1000):
        r = mon.update(False, PipelineHealth.OK, -5.0)
    assert r.absent_s == 0.0 and not r.alert


# --------------------------------------------------------------------------- #
# parity
# --------------------------------------------------------------------------- #

def test_the_constants_match_the_firmware():
    import re

    from drowsyguard import presence as py

    header = (FIRMWARE / 'presence.h').read_text(encoding='utf-8')
    for name in ('PRESENCE_ALERT_S', 'PRESENCE_CLEAR_S', 'PRESENCE_REPEAT_S',
                 'PRESENCE_WARMUP_S'):
        m = re.search(rf'constexpr\s+float\s+{name}\s*=\s*([-\d.]+)f?\s*;', header)
        assert m, f'{name} is missing from presence.h'
        assert float(m.group(1)) == pytest.approx(getattr(py, name), rel=1e-6)


def test_the_state_and_health_names_match_the_firmware():
    src = (FIRMWARE / 'presence.cpp').read_text(encoding='utf-8')
    for state in PresenceState:
        assert f'return "{state.value}"' in src, state
    for health in PipelineHealth:
        assert f'return "{health.value}"' in src, health


TIMELINES = {
    'empty seat': lambda: warm() + seconds(10.0, False),
    'glance away': lambda: warm() + seconds(2.0, False) + seconds(3.0, True),
    'driver leaves and returns twice': lambda: (
        warm() + seconds(6.0, False) + seconds(3.0, True)
        + seconds(6.0, False) + seconds(3.0, True)),
    'camera fault throughout': lambda: seconds(20.0, False, PipelineHealth.CAMERA_FAULT),
    'fault mid-countdown': lambda: (
        warm() + seconds(2.5, False)
        + seconds(1.0, False, PipelineHealth.CAMERA_FAULT) + seconds(5.0, False)),
    'model fault then recovery': lambda: (
        seconds(6.0, False, PipelineHealth.MODEL_FAULT) + seconds(10.0, False)),
    'flicker on an empty seat': lambda: (
        warm() + sum(([(True, PipelineHealth.OK, DT)] + seconds(2.0, False)
                      for _ in range(10)), [])),
    'never anyone at all': lambda: seconds(30.0, False),
}


@pytest.mark.parametrize('name', sorted(TIMELINES))
def test_the_firmware_agrees_step_by_step(rows, name):
    steps = TIMELINES[name]()
    cfg = PresenceConfig()
    py, cpp = run_py(steps, cfg), run_cpp(rows, steps, cfg)
    assert len(py) == len(cpp) == len(steps)
    for i, (a, b) in enumerate(zip(py, cpp)):
        assert a[0] == b[0] and a[1] == b[1] and a[4] == b[4] and a[5] == b[5], (
            f'{name}: step {i} differs\n  python: {a}\n  firmware: {b}')
        # Still a tolerance, small: the accumulated total is the same sequence of
        # exact additions, but it is stored as float32 on one side and double on the
        # other, so the printed values differ in the last digit or two.
        assert a[2] == pytest.approx(b[2], abs=1e-4), f'{name}: absent_s at step {i}'
        assert a[3] == pytest.approx(b[3], abs=1e-4), f'{name}: present_s at step {i}'


def _transitions(timeline):
    """(state, first index) for each run. What "the same behaviour" actually means
    when the two sides may be a frame apart."""
    out = []
    for i, row in enumerate(timeline):
        if not out or out[-1][0] != row[0]:
            out.append((row[0], i))
    return out


def test_the_firmware_agrees_within_one_frame_at_an_inexact_step(rows):
    """The real device runs at whatever the camera gives it, which is never a power
    of two. This is the same comparison with the slack that a float32-versus-double
    accumulation genuinely requires - one frame - so a *behavioural* difference of two
    frames or more would still fail.
    """
    dt = 1.0 / 15.0
    cfg = PresenceConfig()
    steps = (seconds(cfg.warmup_s + 1.0, True, dt=dt) + seconds(10.0, False, dt=dt)
             + seconds(3.0, True, dt=dt) + seconds(10.0, False, dt=dt))
    py, cpp = run_py(steps, cfg), run_cpp(rows, steps, cfg)

    tp, tc = _transitions(py), _transitions(cpp)
    assert [s for s, _ in tp] == [s for s, _ in tc], (tp, tc)
    for (state, ip), (_, ic) in zip(tp, tc):
        assert abs(ip - ic) <= 1, f'{state} starts {abs(ip - ic)} frames apart'

    ap = [i for i, r in enumerate(py) if r[1]]
    ac = [i for i, r in enumerate(cpp) if r[1]]
    assert len(ap) == len(ac) == 2
    for i, j in zip(ap, ac):
        assert abs(i - j) <= 1


def test_the_firmware_agrees_on_a_non_default_configuration(rows):
    cfg = PresenceConfig(alert_after_s=1.5, clear_s=0.25, repeat_s=4.0, warmup_s=1.0)
    steps = warm() + seconds(20.0, False) + seconds(2.0, True) + seconds(8.0, False)
    py, cpp = run_py(steps, cfg), run_cpp(rows, steps, cfg)
    assert [(a[0], a[1], a[4]) for a in py] == [(b[0], b[1], b[4]) for b in cpp]
    assert sum(1 for r in py if r[1]) >= 5, 'the timeline must exercise repeats'
