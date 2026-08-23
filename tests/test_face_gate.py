"""The detection gate: which box is the driver, and where to look for them next.

`firmware/esp32s3/main/face_gate.cpp` decides three things the previous version got
wrong or did not do at all:

  * whether a detection is a face, rather than whatever the coarse stage scored 0.11
    on - the stage threshold has to be 0.10 on this camera (see model_adapter.cpp),
    so weak candidates genuinely do arrive;
  * which of several faces is the driver, given that "largest box wins" hands the
    alarm to a passenger who leans forward;
  * where to crop for the next detection, and how to translate the result back.

That last one is why this test exists in this form. An origin applied once, twice or
not at all all produce boxes that look plausible on a preview; the only symptom of
getting it wrong is every eye crop landing a few pixels off, which reads as a weak
eye model rather than as a coordinate bug. So the round trip is checked numerically
against a reference implementation, on the real compiled file.

Skipped when there is no host compiler; it is a correctness gate, not a reason to
fail an unrelated checkout.
"""
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / 'firmware/esp32s3/main'

# Mirrors of the constants in face_gate.h. Deliberately duplicated rather than
# parsed: if one moves, this file's expectations should be re-read by a human, not
# silently re-derived.
ROI_PAD = 0.60
ROI_MAX_FRAC = 0.85
ROI_MIN_SIDE = 96
TRACK_MIN_IOU = 0.15

HARNESS = r'''
#include <cstdio>
#include <vector>
#include "face_gate.h"

static bool read_lm(Landmarks *lm) {
    for (int i = 0; i < 5; ++i) {
        if (scanf("%f %f", &lm->x[i], &lm->y[i]) != 2) return false;
    }
    lm->valid = true;
    return true;
}

int main() {
    char op[16];
    while (scanf("%15s", op) == 1) {
        if (op[0] == 'q') break;
        if (op[0] == 'r') {                     // roi bx by bw bh fw fh
            FaceBox b; int fw, fh;
            if (scanf("%d %d %d %d %d %d", &b.x, &b.y, &b.w, &b.h, &fw, &fh) != 6) return 1;
            b.valid = true;
            const FaceBox r = face_gate_roi(b, fw, fh);
            printf("%d %d %d %d %d\n", r.valid ? 1 : 0, r.x, r.y, r.w, r.h);
        } else if (op[0] == 'i') {              // iou ax ay aw ah bx by bw bh
            FaceBox a, b;
            if (scanf("%d %d %d %d %d %d %d %d", &a.x, &a.y, &a.w, &a.h,
                      &b.x, &b.y, &b.w, &b.h) != 8) return 1;
            a.valid = b.valid = true;
            printf("%.9g\n", face_gate_iou(a, b));
        } else if (op[0] == 'p' && op[1] == 'l') {   // plaus bx by bw bh + 5 points
            FaceBox b; Landmarks lm;
            if (scanf("%d %d %d %d", &b.x, &b.y, &b.w, &b.h) != 4) return 1;
            b.valid = true;
            if (!read_lm(&lm)) return 1;
            FaceGeometry g{};
            const FaceReject why = face_gate_check(lm, b, &g);
            printf("%d %s %.4f %.4f %.4f\n", why == FaceReject::None ? 1 : 0,
                   face_gate_reject_name(why), g.roll, g.jaw_drop, g.nose_frac);
        } else if (op[0] == 'e') {              // enforce?
            printf("%d\n", FACE_GATE_ENFORCE);
        } else if (op[0] == 'o') {              // orient + 5 points
            Landmarks lm;
            if (!read_lm(&lm)) return 1;
            const Landmarks o = behavior_orient_landmarks(lm);
            const FaceGeometry g = behavior_face_geometry(o);
            for (int i = 0; i < 5; ++i) printf("%.4f %.4f ", o.x[i], o.y[i]);
            printf("%.4f %.4f %.4f %.4f\n", g.roll, g.jaw_drop, g.nose_frac, g.mouth_ratio);
        } else if (op[0] == 'm') {              // map rvalid rx ry rw rh + box + pts
            FaceBox roi, b; Landmarks lm;
            int rvalid = 0;
            if (scanf("%d %d %d %d %d", &rvalid, &roi.x, &roi.y, &roi.w, &roi.h) != 5) return 1;
            if (scanf("%d %d %d %d", &b.x, &b.y, &b.w, &b.h) != 4) return 1;
            roi.valid = rvalid != 0;
            b.valid = true;
            if (!read_lm(&lm)) return 1;
            face_gate_map_out(roi, &b, &lm);
            printf("%d %d %d %d", b.x, b.y, b.w, b.h);
            for (int i = 0; i < 5; ++i) printf(" %.9g %.9g", lm.x[i], lm.y[i]);
            printf("\n");
        } else if (op[0] == 'p' && op[1] == 'i') {   // pick n tvalid tx ty tw th ...
            int n = 0, tvalid = 0;
            FaceBox track;
            if (scanf("%d %d %d %d %d %d", &n, &tvalid, &track.x, &track.y,
                      &track.w, &track.h) != 6) return 1;
            track.valid = tvalid != 0;
            std::vector<FaceBox> boxes(n);
            std::vector<Landmarks> lms(n);
            for (int i = 0; i < n; ++i) {
                if (scanf("%d %d %d %d", &boxes[i].x, &boxes[i].y,
                          &boxes[i].w, &boxes[i].h) != 4) return 1;
                boxes[i].valid = true;
                if (!read_lm(&lms[i])) return 1;
            }
            printf("%d\n", face_gate_pick(boxes.data(), lms.data(), n, track));
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


def _build(cc, d, name, extra):
    src = d / f'{name}.cpp'
    src.write_text(HARNESS, encoding='utf-8')
    exe = d / (f'{name}.exe' if sys.platform == 'win32' else name)
    proc = subprocess.run(
        cc + ['-O2', '-std=c++17', f'-I{FIRMWARE}', *extra, str(src),
              str(FIRMWARE / 'face_gate.cpp'), str(FIRMWARE / 'behavior.cpp'),
              '-o', str(exe)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail(f'face_gate.cpp does not compile on the host ({name}):\n'
                    + proc.stderr[-4000:])

    def call(script):
        out = subprocess.run([str(exe)], input=script, capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        return [line.split() for line in out.stdout.strip().splitlines()]

    return call


@pytest.fixture(scope='module')
def gate(tmp_path_factory):
    """The firmware's own configuration, whatever face_gate.h currently says."""
    cc = _compiler()
    if cc is None:
        pytest.skip('no host C++ compiler')
    return _build(cc, tmp_path_factory.mktemp('gate'), 'g', [])


@pytest.fixture(scope='module')
def gate_advisory(tmp_path_factory):
    """The same code with FACE_GATE_ENFORCE=0.

    That is the documented fallback if these limits ever start rejecting real faces
    again, as they did once. Building it here keeps it a tested path rather than a
    branch nobody has run.
    """
    cc = _compiler()
    if cc is None:
        pytest.skip('no host C++ compiler')
    return _build(cc, tmp_path_factory.mktemp('gate_off'), 'ga',
                  ['-DFACE_GATE_ENFORCE=0'])


# --------------------------------------------------------------------------- #
# a synthetic frontal face, in the same shape tests/test_behavior.py uses
# --------------------------------------------------------------------------- #

def face_points(cx, cy, eye_dist=60.0, jaw=1.05, nose_frac=0.55, mouth_w=0.60):
    half = eye_dist / 2.0
    my = jaw * eye_dist
    mx = eye_dist * mouth_w / 2.0
    # canonical order: right eye, left eye, nose, right mouth, left mouth
    return [(cx - half, cy), (cx + half, cy), (cx, cy + nose_frac * my),
            (cx - mx, cy + my), (cx + mx, cy + my)]


def box_around(points, pad=0.25):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    return (int(min(xs) - w * pad), int(min(ys) - h * pad),
            int(w * (1 + 2 * pad)), int(h * (1 + 2 * pad)))


def flat(points):
    return ' '.join(f'{v:.6f}' for p in points for v in p)


# --------------------------------------------------------------------------- #
# ROI
# --------------------------------------------------------------------------- #

def roi_reference(bx, by, bw, bh, fw, fh):
    """Independent implementation of face_gate_roi, from the header's description."""
    side = int(max(bw, bh) * (1.0 + 2.0 * ROI_PAD))
    side = max(side, ROI_MIN_SIDE)
    if side >= int(min(fw, fh) * ROI_MAX_FRAC):
        return None
    side &= ~1
    cx, cy = bx + bw // 2, by + bh // 2
    x, y = cx - side // 2, cy - side // 2
    x, y = max(x, 0), max(y, 0)
    if x + side > fw:
        x = fw - side
    if y + side > fh:
        y = fh - side
    return (x & ~1, y & ~1, side, side)


def test_roi_matches_an_independent_implementation():
    rng = random.Random(20260823)
    cases = []
    for _ in range(400):
        fw = fh = 240
        bw = rng.randint(8, 200)
        bh = rng.randint(8, 200)
        bx = rng.randint(-10, fw - 1)
        by = rng.randint(-10, fh - 1)
        cases.append((bx, by, bw, bh, fw, fh))
    script = ''.join(f'roi {c[0]} {c[1]} {c[2]} {c[3]} {c[4]} {c[5]}\n' for c in cases) + 'q\n'
    rows = _rows(script)
    assert len(rows) == len(cases)
    for c, row in zip(cases, rows):
        got = (int(row[1]), int(row[2]), int(row[3]), int(row[4])) if row[0] == '1' else None
        assert got == roi_reference(*c), c


def test_roi_stays_inside_the_frame_and_stays_square():
    rng = random.Random(7)
    cases = [(rng.randint(0, 200), rng.randint(0, 200), rng.randint(10, 90),
              rng.randint(10, 90), 240, 240) for _ in range(300)]
    script = ''.join(f'roi {c[0]} {c[1]} {c[2]} {c[3]} {c[4]} {c[5]}\n' for c in cases) + 'q\n'
    for c, row in zip(cases, _rows(script)):
        if row[0] != '1':
            continue
        x, y, w, h = (int(v) for v in row[1:])
        assert w == h, c                      # square, so the resize does not stretch
        assert w % 2 == 0 and x % 2 == 0 and y % 2 == 0, c
        assert 0 <= x and 0 <= y, c
        assert x + w <= 240 and y + h <= 240, c


def test_a_box_that_already_fills_the_frame_is_not_cropped():
    rows = _rows('roi 20 20 200 200 240 240\nq\n')
    assert rows[0][0] == '0', 'cropping gains nothing here and risks losing the face'


def test_a_tiny_box_still_gets_a_usable_roi():
    rows = _rows('roi 100 100 12 12 240 240\nq\n')
    assert rows[0][0] == '1'
    assert int(rows[0][3]) >= ROI_MIN_SIDE, 'a 27 px crop would upsample noise'


# --------------------------------------------------------------------------- #
# the round trip that this file exists for
# --------------------------------------------------------------------------- #

def test_a_detection_inside_a_roi_maps_back_to_where_it_really_is():
    """Detect the same face two ways and require the same answer.

    The face is placed in the frame, the ROI is computed from a previous box, the
    face's true coordinates are converted into ROI-local ones by hand, and the
    result is mapped back out. Anything other than an exact round trip is an origin
    applied the wrong number of times.
    """
    rng = random.Random(99)
    for _ in range(200):
        cx, cy = rng.randint(70, 170), rng.randint(70, 170)
        pts = face_points(cx, cy, eye_dist=rng.uniform(30, 70))
        bx, by, bw, bh = box_around(pts)

        roi = roi_reference(bx, by, bw, bh, 240, 240)
        if roi is None:
            continue
        rx, ry, _, _ = roi

        # What the detector would report, having been handed the crop.
        local_box = (bx - rx, by - ry, bw, bh)
        local_pts = [(p[0] - rx, p[1] - ry) for p in pts]

        script = (f'map 1 {rx} {ry} {roi[2]} {roi[3]} '
                  f'{local_box[0]} {local_box[1]} {local_box[2]} {local_box[3]} '
                  f'{flat(local_pts)}\nq\n')
        row = _rows(script)[0]
        assert [int(v) for v in row[:4]] == [bx, by, bw, bh]
        back = [float(v) for v in row[4:]]
        for i, p in enumerate(pts):
            assert abs(back[2 * i] - p[0]) < 1e-3
            assert abs(back[2 * i + 1] - p[1]) < 1e-3


def test_mapping_out_of_an_unset_roi_is_a_no_op():
    """A full-frame detection must not be shifted by a stale ROI origin.

    This is the other half of the round trip and it is not symmetric with it: the
    ROI is unset on every frame where the track is cold, so this path runs
    constantly, and adding an origin here would displace every landmark by the
    position of a crop that was never taken."""
    pts = face_points(120, 120)
    bx, by, bw, bh = box_around(pts)
    # A non-zero origin, deliberately: if validity were ignored these numbers are
    # exactly what would leak into the result.
    row = _rows(f'map 0 40 60 96 96 {bx} {by} {bw} {bh} {flat(pts)}\nq\n')[0]
    assert [int(v) for v in row[:4]] == [bx, by, bw, bh]
    back = [float(v) for v in row[4:]]
    for i, pt in enumerate(pts):
        assert abs(back[2 * i] - pt[0]) < 1e-3
        assert abs(back[2 * i + 1] - pt[1]) < 1e-3


# --------------------------------------------------------------------------- #
# plausibility
# --------------------------------------------------------------------------- #

def _plaus(points, box=None):
    box = box or box_around(points)
    rows = _rows(f'plaus {box[0]} {box[1]} {box[2]} {box[3]} {flat(points)}\nq\n')
    return rows[0][0] == '1'


def _why(points, box=None):
    """Which check failed, so a test can assert the diagnosis and not just the verdict.

    The reason is what makes a rejection actionable: the first version of this gate
    reported a bare count, and on hardware that read "gate dropped 2" while rejecting
    every real candidate - indistinguishable from an empty frame."""
    box = box or box_around(points)
    rows = _rows(f'plaus {box[0]} {box[1]} {box[2]} {box[3]} {flat(points)}\nq\n')
    return rows[0][1]


def test_a_frontal_face_is_plausible():
    assert _plaus(face_points(120, 120))


def test_a_permuted_landmark_set_is_rejected():
    """ESP-DL's keypoint order differs from this project's, and getting the reorder
    wrong is a live hazard - behavior.h warns about it. A swap puts the mouth above
    the eyes, which is a sign check away from being caught."""
    pts = face_points(120, 120)
    swapped = [pts[3], pts[4], pts[2], pts[0], pts[1]]      # eyes and mouth exchanged
    assert not _plaus(swapped, box_around(pts))


def test_landmarks_that_belong_to_something_else_are_rejected():
    """Two objects reported as one detection. Every measurement from it is nonsense,
    and the old code would have measured them anyway."""
    pts = face_points(120, 120)
    box = (10, 10, 40, 40)                                   # nowhere near the points
    assert not _plaus(pts, box)


def test_a_collapsed_landmark_set_is_rejected():
    """What a low-confidence detection on a headrest tends to look like: five points
    on top of each other, so there is no interocular distance to normalise by."""
    assert not _plaus([(120.0, 120.0)] * 5, (100, 100, 40, 40))


def test_an_extreme_roll_is_rejected():
    """Every geometric cue is measured in a frame de-rotated by exactly this angle,
    so past a point they stop meaning anything."""
    import math
    pts = face_points(120, 120)
    a = math.radians(70.0)
    ca, sa = math.cos(a), math.sin(a)
    rot = [(120 + ca * (x - 120) - sa * (y - 120),
            120 + sa * (x - 120) + ca * (y - 120)) for x, y in pts]
    assert not _plaus(rot, box_around(rot))


def test_an_implausible_interocular_distance_is_rejected():
    wide = face_points(120, 120, eye_dist=150.0)             # eyes wider than the face
    assert not _plaus(wide, (100, 100, 60, 60))


# --------------------------------------------------------------------------- #
# picking the driver
# --------------------------------------------------------------------------- #

def _pick(candidates, track=None, rows=None):
    rows = rows or _rows
    tv = 1 if track else 0
    t = track or (0, 0, 0, 0)
    parts = [f'pick {len(candidates)} {tv} {t[0]} {t[1]} {t[2]} {t[3]}']
    for box, pts in candidates:
        parts.append(f'{box[0]} {box[1]} {box[2]} {box[3]} {flat(pts)}')
    return int(rows(' '.join(parts) + '\nq\n')[0][0])


def test_with_no_track_the_largest_face_wins():
    """The driver is the closest face to a dashboard camera, so size is the right
    opening guess."""
    near = face_points(80, 120, eye_dist=70.0)
    far = face_points(180, 100, eye_dist=30.0)
    cands = [(box_around(far), far), (box_around(near), near)]
    assert _pick(cands) == 1


def test_a_track_beats_a_bigger_face():
    """A passenger leaning forward is a bigger face than the driver. Under "largest
    wins" they take over the box, the landmarks, the eye crops and the alarm."""
    driver = face_points(80, 120, eye_dist=40.0)
    passenger = face_points(180, 120, eye_dist=80.0)
    cands = [(box_around(passenger), passenger), (box_around(driver), driver)]
    assert _pick(cands) == 0                                  # no track: the big one
    assert _pick(cands, track=box_around(driver)) == 1        # tracked: the driver


def test_the_firmware_build_enforces_plausibility(gate):
    """The shipped configuration, asserted so that turning it off is deliberate.

    It was off for one build, and for a good reason: the limits rejected 100% of real
    candidates on hardware. That turned out to be a mirrored frame reversing the eye
    pair rather than a bad limit - see behavior_orient_landmarks() - and with the
    orientation fixed the device reports every candidate as `ok`."""
    assert gate('enforce\nq\n')[0][0] == '1'


def test_an_implausible_candidate_loses_to_a_smaller_real_face():
    real = face_points(80, 120, eye_dist=40.0)
    junk = [(180.0, 120.0)] * 5                               # collapsed, not a face
    cands = [((140, 80, 120, 120), junk), (box_around(real), real)]
    assert _pick(cands) == 1


def test_no_plausible_candidate_means_no_detection():
    junk = [(120.0, 120.0)] * 5
    assert _pick([((100, 100, 60, 60), junk)]) == -1


def test_the_advisory_escape_hatch_still_prefers_the_largest_box(gate_advisory):
    """With FACE_GATE_ENFORCE=0 the gate reports and does not veto, which is the fallback
    if these limits ever start rejecting real faces again. Built and tested so it is a
    working escape hatch rather than an untested branch."""
    assert gate_advisory('enforce\nq\n')[0][0] == '0'
    real = face_points(80, 120, eye_dist=40.0)
    junk = [(180.0, 120.0)] * 5
    cands = [((140, 80, 120, 120), junk), (box_around(real), real)]
    assert _pick(cands, rows=gate_advisory) == 0


# --------------------------------------------------------------------------- #
# IoU
# --------------------------------------------------------------------------- #

def test_iou_basics():
    rows = _rows('iou 0 0 10 10 0 0 10 10\n'
                 'iou 0 0 10 10 20 20 10 10\n'
                 'iou 0 0 10 10 5 0 10 10\n'
                 'q\n')
    assert float(rows[0][0]) == pytest.approx(1.0)
    assert float(rows[1][0]) == pytest.approx(0.0)
    assert float(rows[2][0]) == pytest.approx(50 / 150)


# The fixture is module-scoped and every test needs it, so it is bound to a module
# global once rather than threaded through every helper.
_rows = None


@pytest.fixture(autouse=True, scope='module')
def _bind(gate):
    global _rows
    _rows = gate
    yield
    _rows = None


# --------------------------------------------------------------------------- #
# the reason, not just the verdict
# --------------------------------------------------------------------------- #

def test_each_rejection_names_the_check_that_failed():
    """A count of rejections is not actionable. This is the mapping that turns
    "gate dropped 2" into something that can be argued with."""
    import math

    good = face_points(120, 120)
    assert _why(good) == 'ok'

    assert _why([(120.0, 120.0)] * 5, (100, 100, 40, 40)) == 'degenerate-eye-distance'
    assert _why(good, (10, 10, 40, 40)) == 'landmarks-outside-box'

    swapped = [good[3], good[4], good[2], good[0], good[1]]
    assert _why(swapped, box_around(good)) == 'mouth-not-below-eyes'

    # A box that still contains the landmarks, so the earlier outside-box check does
    # not claim this first - the checks are ordered and the first failure is the one
    # reported, which is the whole point of reporting one.
    wide = face_points(120, 120, eye_dist=150.0)
    assert _why(wide, (60, 140, 120, 120)) == 'eye-distance-too-large'

    a = math.radians(70.0)
    ca, sa = math.cos(a), math.sin(a)
    tilted = [(120 + ca * (x - 120) - sa * (y - 120),
               120 + sa * (x - 120) + ca * (y - 120)) for x, y in good]
    assert _why(tilted, box_around(tilted)) == 'roll-too-steep'


# --------------------------------------------------------------------------- #
# the mirrored frame
# --------------------------------------------------------------------------- #

def _orient(points):
    row = _rows(f'orient {flat(points)}\nq\n')[0]
    pts = [(float(row[2 * i]), float(row[2 * i + 1])) for i in range(5)]
    roll, jaw, nose_frac, mouth_ratio = (float(v) for v in row[10:14])
    return pts, roll, jaw, nose_frac, mouth_ratio


def mirror(points, axis=120.0):
    """Flip horizontally, which is what this board's frame actually is: the sensor is
    mounted upside down, so the firmware applies a vertical flip and the result is
    upright but mirrored."""
    return [(2 * axis - x, y) for x, y in points]


def test_a_mirrored_face_measures_the_same_as_an_unmirrored_one():
    """The bug this fixes was live on hardware: roll -170.9, jaw -1.64, on detections
    scoring 1.00. Every magnitude right, every sign wrong, because a reversed eye pair
    puts roll at 180 degrees and face_geometry() then de-rotates by 180 degrees.

    The consequence was not cosmetic. jaw_drop *falls* as the mouth opens once its
    sign is flipped, and mouth_open requires a rise - so the yawn cue could not fire
    at all."""
    upright = face_points(120, 120, eye_dist=60.0, jaw=1.05, mouth_w=0.60)
    _, roll_a, jaw_a, nose_a, width_a = _orient(upright)
    _, roll_b, jaw_b, nose_b, width_b = _orient(mirror(upright))

    assert abs(roll_a) < 1e-3 and abs(roll_b) < 1e-3
    assert jaw_a == pytest.approx(1.05, abs=1e-3)
    assert jaw_b == pytest.approx(jaw_a, abs=1e-3)
    assert nose_b == pytest.approx(nose_a, abs=1e-3)
    assert width_b == pytest.approx(width_a, abs=1e-3)


def test_a_mirrored_open_mouth_still_raises_the_jaw_drop():
    """The direction is the whole point: a cue that moves the wrong way is worse than
    one that is merely offset, because every threshold is a one-sided comparison."""
    shut = mirror(face_points(120, 120, jaw=1.05))
    open_ = mirror(face_points(120, 120, jaw=1.30))
    _, _, jaw_shut, _, _ = _orient(shut)
    _, _, jaw_open, _, _ = _orient(open_)
    assert jaw_open > jaw_shut
    assert jaw_shut > 0, 'a negative jaw drop is the signature of the mirror bug'


def test_orienting_an_already_correct_face_changes_nothing():
    upright = face_points(120, 120)
    pts, _, _, _, _ = _orient(upright)
    for got, want in zip(pts, upright):
        assert got[0] == pytest.approx(want[0], abs=1e-3)
        assert got[1] == pytest.approx(want[1], abs=1e-3)
