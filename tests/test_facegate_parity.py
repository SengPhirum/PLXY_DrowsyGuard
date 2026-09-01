"""The desktop gate and the firmware gate must reach the same verdicts.

`tests/test_firmware_parity.py` checks that the *constants* match. That is necessary
and nowhere near sufficient: two implementations can share every threshold and still
disagree, because the decisions here are ordered (which check is reported first),
stateful (how many detections have agreed, how many have been missed) and full of
boundary cases (a candidate exactly at the reacquisition window, a face that
reappears in exactly the place it left). Every one of those is a place where a
transcription can drift without a single number changing.

So this drives both implementations - `src/drowsyguard/facegate.py` and the compiled
`firmware/esp32s3/main/face_gate.cpp` - through identical inputs and requires
identical outputs, verdict by verdict and step by step.

Why it matters concretely: the dashboard is where thresholds get tuned, and a
threshold tuned against a gate that behaves differently from the device's is worse
than no tuning at all, because it produces confident numbers about the wrong system.

Skipped without a host C++ compiler, like tests/test_face_gate.py.
"""
import random

import pytest

from drowsyguard import facegate

from test_face_gate import (  # noqa: E402  - shares the compiled harness
    FRAME, GOOD_SCORE, HARNESS, _build, _compiler, box_around, face_points, flat,
    hand_box, hand_points, headrest_points, phone_points, square_box)


@pytest.fixture(scope='module')
def rows(tmp_path_factory):
    cc = _compiler()
    if cc is None:
        pytest.skip('no host C++ compiler')
    return _build(cc, tmp_path_factory.mktemp('parity'), 'p', [])


def _cpp_why(rows, points, box, score, frame=FRAME):
    row = rows(f'plaus {box[0]} {box[1]} {box[2]} {box[3]} {score} '
               f'{frame} {frame} {flat(points)}\nq\n')[0]
    return row[1]


def _py_why(points, box, score, frame=FRAME):
    b = facegate.Box(box[0], box[1], box[2], box[3], True)
    return facegate.check(points, b, float(score), frame, frame)[0].value


# --------------------------------------------------------------------------- #
# the constants
# --------------------------------------------------------------------------- #

CONSTANTS = [
    'FACE_MIN_SCORE', 'FACE_EYE_DIST_MIN_FRAC', 'FACE_EYE_DIST_MAX_FRAC',
    'FACE_MAX_ROLL_DEG', 'FACE_JAW_MIN', 'FACE_JAW_MAX', 'FACE_NOSE_FRAC_MIN',
    'FACE_NOSE_FRAC_MAX', 'FACE_YAW_MAX', 'FACE_MOUTH_MIN', 'FACE_MOUTH_MAX',
    'FACE_MIN_SIDE_FRAC', 'FACE_MAX_SIDE_FRAC', 'FACE_ASPECT_MIN', 'FACE_ASPECT_MAX',
    'FACE_KP_MARGIN_FRAC', 'FACE_CONFIRM_DETECTIONS', 'FACE_HOLD_DETECTIONS',
    'FACE_JUMP_MAX_FRAC', 'FACE_SCALE_MAX_RATIO', 'FACE_REACQUIRE_AFTER',
    'FACE_TRACK_MIN_IOU',
]


@pytest.mark.parametrize('name', CONSTANTS)
def test_every_gate_constant_matches_the_firmware(name):
    """Parsed from the header rather than trusted from a comment."""
    import re
    from pathlib import Path

    header = (Path(__file__).resolve().parents[1]
              / 'firmware/esp32s3/main/face_gate.h').read_text(encoding='utf-8')
    m = re.search(rf'constexpr\s+\w+\s+{name}\s*=\s*([-\d.]+)f?\s*;', header)
    assert m, f'{name} is missing from face_gate.h'
    assert float(m.group(1)) == pytest.approx(getattr(facegate, name), rel=1e-6)


def test_every_reject_name_matches_the_firmware(rows):
    """The reject tokens are compared as strings by operators reading two different
    screens, so they have to be the same strings."""
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1]
           / 'firmware/esp32s3/main/face_gate.cpp').read_text(encoding='utf-8')
    body = src.split('face_gate_reject_name', 1)[1]
    cpp_names = set(re.findall(r'return "([a-z-]+)";', body))
    py_names = {r.value for r in facegate.FaceReject}
    assert py_names == cpp_names


# --------------------------------------------------------------------------- #
# the static check, over a randomised sweep
# --------------------------------------------------------------------------- #

def _sweep_cases(n=400, seed=20260901):
    """Candidates spread deliberately across every check's boundary.

    Uniform random faces would pass almost always and test almost nothing; the
    distribution here is chosen so that each of the eighteen reject reasons is
    reachable, which is what makes disagreement on *ordering* detectable.
    """
    rng = random.Random(seed)
    cases = []
    for _ in range(n):
        cx = rng.uniform(50, 190)
        cy = rng.uniform(50, 190)
        pts = face_points(cx, cy,
                          eye_dist=rng.uniform(5, 130),
                          jaw=rng.uniform(0.2, 3.0),
                          nose_frac=rng.uniform(0.0, 1.4),
                          mouth_w=rng.uniform(0.02, 2.4),
                          yaw=rng.uniform(-1.2, 1.2))
        style = rng.random()
        if style < 0.15:
            box = hand_box(int(cx), int(cy))            # oblong
        elif style < 0.30:
            box = (int(cx) - 5, int(cy) - 5, 10, 10)    # tiny
        elif style < 0.40:
            box = (1, 1, 238, 238)                      # fills the frame
        elif style < 0.50:
            box = square_box(pts)
        else:
            box = box_around(pts)
        if box[2] <= 0 or box[3] <= 0:
            continue
        cases.append((pts, box, round(rng.uniform(0.0, 1.0), 4)))
    # Plus the named not-a-face shapes, which are the cases that matter most.
    for maker in (hand_points, headrest_points, phone_points):
        pts = maker()
        cases.append((pts, square_box(pts), GOOD_SCORE))
        cases.append((pts, box_around(pts), GOOD_SCORE))
    cases.append((face_points(120, 120), box_around(face_points(120, 120)), GOOD_SCORE))
    return cases


def test_the_static_check_agrees_on_every_candidate(rows):
    cases = _sweep_cases()
    script = ''.join(
        f'plaus {b[0]} {b[1]} {b[2]} {b[3]} {s} {FRAME} {FRAME} {flat(p)}\n'
        for p, b, s in cases) + 'q\n'
    got = rows(script)
    assert len(got) == len(cases)
    disagreements = []
    for (pts, box, score), row in zip(cases, got):
        want = _py_why(pts, box, score)
        if row[1] != want:
            disagreements.append((box, score, row[1], want))
    assert not disagreements, (
        f'{len(disagreements)} of {len(cases)} candidates were judged differently; '
        f'first few: {disagreements[:5]}')


def test_the_sweep_actually_reaches_most_of_the_reject_reasons():
    """A parity test over inputs that all take the same branch proves nothing. This
    pins the coverage of the sweep itself so it cannot quietly narrow."""
    seen = {_py_why(p, b, s) for p, b, s in _sweep_cases()}
    assert 'ok' in seen
    assert len(seen) >= 10, f'the sweep only reaches {sorted(seen)}'


# --------------------------------------------------------------------------- #
# the track, step by step
# --------------------------------------------------------------------------- #

def _sequences():
    """Named sequences of detection attempts, each `(have, points, box, score)`.

    These are the scenarios the requirements name, written as inputs rather than
    acted out: a real face, hands only, empty frames, temporary occlusion, head
    movement, low light, and a passenger appearing mid-track.
    """
    face = face_points(120, 120)
    none = (False, [(0.0, 0.0)] * 5, (0, 0, 0, 0), 0.0)

    seqs = {}

    seqs['a real face, held steady'] = [
        (True, face, box_around(face), GOOD_SCORE) for _ in range(8)]

    hand = hand_points()
    seqs['a hand only'] = [(True, hand, hand_box(), 0.99) for _ in range(8)]

    seqs['empty frames'] = [none] * 8

    seqs['temporary occlusion'] = (
        [(True, face, box_around(face), GOOD_SCORE)] * 3
        + [none] * 4
        + [(True, face, box_around(face), GOOD_SCORE)] * 3)

    seqs['occlusion past the hold'] = (
        [(True, face, box_around(face), GOOD_SCORE)] * 3
        + [none] * 9
        + [(True, face, box_around(face), GOOD_SCORE)] * 3)

    moving = []
    for i in range(10):
        pts = face_points(70 + 14 * i, 100 + 5 * i, eye_dist=40.0 + 2.0 * i)
        moving.append((True, pts, box_around(pts), GOOD_SCORE))
    seqs['head movement'] = moving

    rng = random.Random(5)
    low = []
    for _ in range(10):
        pts = face_points(120 + rng.uniform(-8, 8), 120 + rng.uniform(-8, 8),
                          eye_dist=rng.uniform(35, 60))
        low.append((True, pts, box_around(pts), round(rng.uniform(0.05, 0.5), 3)))
    seqs['low light'] = low

    passenger = face_points(205, 120, eye_dist=44.0)
    seqs['a passenger appears mid-track'] = (
        [(True, face, box_around(face), GOOD_SCORE)] * 4
        + [(True, passenger, box_around(passenger), 0.99)] * 4
        + [(True, face, box_around(face), GOOD_SCORE)] * 2)

    seqs['flickering junk'] = []
    for _ in range(6):
        seqs['flickering junk'].append((True, hand, hand_box(), 0.99))
        seqs['flickering junk'].append(none)

    seqs['a face that halves in size'] = [
        (True, face, box_around(face), GOOD_SCORE)] * 3 + [
        (True, face_points(120, 120, eye_dist=18.0),
         box_around(face_points(120, 120, eye_dist=18.0)), GOOD_SCORE)] * 3

    return seqs


def _run_py(seq):
    track = facegate.FaceTrack()
    out = []
    for have, pts, box, score in seq:
        b = facegate.Box(box[0], box[1], box[2], box[3], box[2] > 0 and box[3] > 0)
        r = track.update(have, b, pts, float(score), FRAME, FRAME)
        out.append((r.present, r.fresh, r.confirmed, r.acquired, r.lost,
                    r.reject.value, r.misses, r.agreed))
    return out


def _run_cpp(rows, seq):
    script = ['treset\n']
    for have, pts, box, score in seq:
        script.append(f'tupdate {1 if have else 0} {box[0]} {box[1]} {box[2]} {box[3]} '
                      f'{score} {FRAME} {FRAME} {flat(pts)}\n')
    got = rows(''.join(script) + 'q\n')[1:]        # drop treset's "ok"
    out = []
    for row in got:
        out.append((row[0] == '1', row[1] == '1', row[2] == '1', row[3] == '1',
                    row[4] == '1', row[5], int(row[6]), int(row[7])))
    return out


@pytest.mark.parametrize('name', sorted(_sequences()))
def test_the_track_agrees_step_by_step(rows, name):
    seq = _sequences()[name]
    py, cpp = _run_py(seq), _run_cpp(rows, seq)
    assert len(py) == len(cpp) == len(seq)
    for i, (a, b) in enumerate(zip(py, cpp)):
        assert a == b, (f'{name}: step {i} differs\n'
                        f'  python: {a}\n  firmware: {b}')


def test_the_sequences_are_not_all_the_same_answer():
    """Guards the parity tests above from becoming vacuous. If every sequence ended
    with "no driver" they would all agree trivially and prove nothing about the
    interesting half of the state machine."""
    finals = {name: _run_py(seq)[-1][0] for name, seq in _sequences().items()}
    assert finals['a real face, held steady'] is True
    assert finals['temporary occlusion'] is True
    assert finals['head movement'] is True
    assert finals['a hand only'] is False
    assert finals['empty frames'] is False
    assert finals['low light'] is False
    assert finals['flickering junk'] is False


def test_a_randomised_sequence_agrees_step_by_step(rows):
    """A long walk over accepted, refused and missing detections, so the parity does
    not depend on the hand-written scenarios above happening to hit the right
    branches."""
    rng = random.Random(4242)
    seq = []
    cx, cy, eye = 120.0, 120.0, 45.0
    for _ in range(120):
        roll = rng.random()
        if roll < 0.20:
            seq.append((False, [(0.0, 0.0)] * 5, (0, 0, 0, 0), 0.0))
            continue
        if roll < 0.30:                     # a jump: a different object
            cx, cy = rng.uniform(40, 200), rng.uniform(40, 200)
        else:                               # ordinary movement
            cx = min(max(cx + rng.uniform(-14, 14), 40), 200)
            cy = min(max(cy + rng.uniform(-10, 10), 40), 200)
            eye = min(max(eye + rng.uniform(-4, 4), 20), 70)
        pts = face_points(cx, cy, eye_dist=eye)
        box = hand_box(int(cx), int(cy)) if roll > 0.92 else box_around(pts)
        score = round(rng.uniform(0.3, 1.0), 3)
        seq.append((True, pts, box, score))

    py, cpp = _run_py(seq), _run_cpp(rows, seq)
    for i, (a, b) in enumerate(zip(py, cpp)):
        assert a == b, f'step {i} differs\n  python: {a}\n  firmware: {b}'


def test_continuity_agrees(rows):
    rng = random.Random(31337)
    cases = []
    for _ in range(250):
        a = (rng.randint(0, 200), rng.randint(0, 200), rng.randint(10, 100),
             rng.randint(10, 100))
        b = (rng.randint(0, 200), rng.randint(0, 200), rng.randint(10, 100),
             rng.randint(10, 100))
        cases.append((a, b))
    script = ''.join(f'cont {a[0]} {a[1]} {a[2]} {a[3]} {b[0]} {b[1]} {b[2]} {b[3]}\n'
                     for a, b in cases) + 'q\n'
    got = rows(script)
    for (a, b), row in zip(cases, got):
        want = facegate.continuous(facegate.Box(*a, True), facegate.Box(*b, True))
        assert (row[0] == '1') is want, (a, b)


def test_pick_agrees(rows):
    """Candidate selection, including the track-preference rule that keeps a
    passenger from taking the driver's alarm."""
    rng = random.Random(808)
    for _ in range(60):
        n = rng.randint(1, 4)
        cands = []
        for _ in range(n):
            cx, cy = rng.uniform(40, 200), rng.uniform(40, 200)
            pts = face_points(cx, cy, eye_dist=rng.uniform(20, 80),
                              mouth_w=rng.uniform(0.1, 1.8))
            box = hand_box(int(cx), int(cy)) if rng.random() < 0.25 else box_around(pts)
            cands.append((pts, box, round(rng.uniform(0.2, 1.0), 3)))
        track = box_around(face_points(120, 120)) if rng.random() < 0.5 else None

        tv = 1 if track else 0
        t = track or (0, 0, 0, 0)
        parts = [f'pick {n} {tv} {t[0]} {t[1]} {t[2]} {t[3]} {FRAME} {FRAME}']
        for pts, box, score in cands:
            parts.append(f'{box[0]} {box[1]} {box[2]} {box[3]} {score} {flat(pts)}')
        cpp = int(rows(' '.join(parts) + '\nq\n')[0][0])

        py = facegate.pick(
            [(facegate.Box(*box, True), pts, score) for pts, box, score in cands],
            facegate.Box(*t, bool(track)), FRAME, FRAME)
        assert py == cpp, (cands, track, py, cpp)
