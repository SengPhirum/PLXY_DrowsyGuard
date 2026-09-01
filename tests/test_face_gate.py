"""The detection gate: which box is the driver, and where to look for them next.

`firmware/esp32s3/main/face_gate.cpp` decides four things the previous version got
wrong or did not do at all:

  * whether a detection is a face, rather than whatever the coarse stage scored 0.11
    on - the stage threshold has to be 0.10 on this camera (see model_adapter.cpp),
    so weak candidates genuinely do arrive;
  * which of several faces is the driver, given that "largest box wins" hands the
    alarm to a passenger who leans forward;
  * whether a *sequence* of detections is a person - one lucky frame on a headrest
    passes every static check often enough to matter at five detections a second;
  * where to crop for the next detection, and how to translate the result back.

That last one is why this test exists in this form. An origin applied once, twice or
not at all all produce boxes that look plausible on a preview; the only symptom of
getting it wrong is every eye crop landing a few pixels off, which reads as a weak
eye model rather than as a coordinate bug. So the round trip is checked numerically
against a reference implementation, on the real compiled file.

**On the synthetic inputs.** The landmark sets below are constructed, not captured.
That is a real limitation and it is stated here rather than buried: these tests
establish that the gate's *arithmetic* does what the header claims, not that the
thresholds are right for a particular camera in a particular cabin. The numbers the
thresholds were set against are the on-hardware measurements recorded in face_gate.h.
What the constructed cases do give is exact control over the one thing that matters
for a decision layer - the geometry and the sequence - which no amount of recorded
video would provide, because a recording cannot be asked for "a hand, then two frames
of nothing, then the same hand 90 pixels to the left".

Skipped when there is no host compiler; it is a correctness gate, not a reason to
fail an unrelated checkout.
"""
import math
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
CONFIRM = 2
HOLD = 5
REACQUIRE_AFTER = 2

FRAME = 240
GOOD_SCORE = 0.95

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
    static FaceTrack track;
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
        } else if (op[0] == 'p' && op[1] == 'l') {   // plaus bx by bw bh score fw fh + 5 pts
            FaceBox b; Landmarks lm; float score; int fw, fh;
            if (scanf("%d %d %d %d %f %d %d", &b.x, &b.y, &b.w, &b.h,
                      &score, &fw, &fh) != 7) return 1;
            b.valid = true;
            if (!read_lm(&lm)) return 1;
            FaceGeometry g{};
            const FaceReject why = face_gate_check(lm, b, score, fw, fh, &g);
            printf("%d %s %.4f %.4f %.4f %.4f %.4f\n", why == FaceReject::None ? 1 : 0,
                   face_gate_reject_name(why), g.roll, g.jaw_drop, g.nose_frac,
                   g.yaw, g.mouth_ratio);
        } else if (op[0] == 'e') {              // enforce?
            printf("%d\n", FACE_GATE_ENFORCE);
        } else if (op[0] == 'o') {              // orient + 5 pts
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
        } else if (op[0] == 'c' && op[1] == 'o') {   // cont ax ay aw ah bx by bw bh
            FaceBox a, b;
            if (scanf("%d %d %d %d %d %d %d %d", &a.x, &a.y, &a.w, &a.h,
                      &b.x, &b.y, &b.w, &b.h) != 8) return 1;
            a.valid = a.w > 0 && a.h > 0;
            b.valid = b.w > 0 && b.h > 0;
            printf("%d\n", face_gate_continuous(a, b) ? 1 : 0);
        } else if (op[0] == 'p' && op[1] == 'i') {
            // pick n tvalid tx ty tw th fw fh, then per candidate: box score pts
            int n = 0, tvalid = 0, fw = 0, fh = 0;
            FaceBox track_box;
            if (scanf("%d %d %d %d %d %d %d %d", &n, &tvalid, &track_box.x, &track_box.y,
                      &track_box.w, &track_box.h, &fw, &fh) != 8) return 1;
            track_box.valid = tvalid != 0;
            std::vector<FaceBox> boxes(n);
            std::vector<Landmarks> lms(n);
            std::vector<float> scores(n);
            for (int i = 0; i < n; ++i) {
                if (scanf("%d %d %d %d %f", &boxes[i].x, &boxes[i].y,
                          &boxes[i].w, &boxes[i].h, &scores[i]) != 5) return 1;
                boxes[i].valid = true;
                if (!read_lm(&lms[i])) return 1;
            }
            printf("%d\n", face_gate_pick(boxes.data(), lms.data(), scores.data(), n,
                                          track_box, fw, fh));
        } else if (op[0] == 't' && op[1] == 'r') {   // treset
            track.reset();
            printf("ok\n");
        } else if (op[0] == 't' && op[1] == 'u') {
            // tupdate have bx by bw bh score fw fh + 5 pts (pts always read)
            int have = 0, fw = 0, fh = 0;
            FaceBox b; Landmarks lm; float score = 0.0f;
            if (scanf("%d %d %d %d %d %f %d %d", &have, &b.x, &b.y, &b.w, &b.h,
                      &score, &fw, &fh) != 8) return 1;
            b.valid = b.w > 0 && b.h > 0;
            if (!read_lm(&lm)) return 1;
            const FaceTrackResult r = track.update(have != 0, b, lm, score, fw, fh);
            printf("%d %d %d %d %d %s %d %d %d %d %d %d\n",
                   r.present ? 1 : 0, r.fresh ? 1 : 0, r.confirmed ? 1 : 0,
                   r.acquired ? 1 : 0, r.lost ? 1 : 0,
                   face_gate_reject_name(r.reject), r.misses, r.agreed,
                   r.box.x, r.box.y, r.box.w, r.box.h);
        } else if (op[0] == 't' && op[1] == 'p') {   // tpeek
            const FaceTrackResult r = track.peek();
            printf("%d %d %d %d %d %s %d %d %d %d %d %d\n",
                   r.present ? 1 : 0, r.fresh ? 1 : 0, r.confirmed ? 1 : 0,
                   r.acquired ? 1 : 0, r.lost ? 1 : 0,
                   face_gate_reject_name(r.reject), r.misses, r.agreed,
                   r.box.x, r.box.y, r.box.w, r.box.h);
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

def face_points(cx, cy, eye_dist=60.0, jaw=1.05, nose_frac=0.55, mouth_w=0.60,
                yaw=0.0):
    half = eye_dist / 2.0
    my = jaw * eye_dist
    mx = eye_dist * mouth_w / 2.0
    # canonical order: right eye, left eye, nose, right mouth, left mouth
    return [(cx - half, cy), (cx + half, cy),
            (cx + yaw * eye_dist, cy + nose_frac * my),
            (cx - mx, cy + my), (cx + mx, cy + my)]


def square_box(points, pad=0.25):
    """box_around(), forced square.

    Needed for the not-a-face cases: their landmarks are laterally extreme, so a
    tight box round them is oblong and the aspect check claims the rejection first.
    A square box is the harder test - it makes the *landmark* check do the work,
    which is what those cases are there to exercise."""
    x0, y0, w, h = box_around(points, pad)
    side = max(w, h)
    return (int(x0 + w / 2 - side / 2), int(y0 + h / 2 - side / 2), side, side)


def box_around(points, pad=0.25):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    return (int(min(xs) - w * pad), int(min(ys) - h * pad),
            int(w * (1 + 2 * pad)), int(h * (1 + 2 * pad)))


def flat(points):
    return ' '.join(f'{v:.6f}' for p in points for v in p)


# --------------------------------------------------------------------------- #
# things that are not faces
# --------------------------------------------------------------------------- #
#
# Four distinct false positives, because they fail for four different reasons and a
# gate that catches only one of them is not a gate. Each is named for the physical
# object a driver-facing camera actually sees.

def hand_points(cx=120.0, cy=120.0):
    """A raised hand: fingertips read as an eye pair, a knuckle as the nose.

    The tell is horizontal. Fitting five face keypoints onto a hand gets the vertical
    ordering roughly right often enough - there is always something below something
    else - but there is no reason for the "nose" to land between the "eyes", and on a
    hand it usually does not. This puts it well outside them.
    """
    return face_points(cx, cy, eye_dist=40.0, jaw=1.0, yaw=1.4)


def hand_box(cx=120, cy=120):
    """A hand's box: tall and narrow, because a hand is.

    Separate from the landmarks on purpose - this is the check that fires even when a
    detector's keypoints happen to land plausibly, which is the case the landmark
    checks cannot see.
    """
    return (cx - 18, cy - 55, 36, 110)


def headrest_points(cx=120.0, cy=120.0):
    """A headrest: the right overall shape with no facial structure inside it.

    Five keypoints regressed onto a smooth blob collapse toward its centre, so the
    mouth corners end up almost coincident - there is nothing in the image to pull
    them apart.
    """
    return face_points(cx, cy, eye_dist=45.0, jaw=1.0, mouth_w=0.05)


def phone_points(cx=120.0, cy=120.0):
    """A phone or a mirror: a bright rectangle with a face-like aspect and no face.

    Fails on the mouth being wider than the head, which is what happens when the
    corners are pushed to the rectangle's edges.
    """
    return face_points(cx, cy, eye_dist=30.0, jaw=1.0, mouth_w=2.2)


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
        fw = fh = FRAME
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
              rng.randint(10, 90), FRAME, FRAME) for _ in range(300)]
    script = ''.join(f'roi {c[0]} {c[1]} {c[2]} {c[3]} {c[4]} {c[5]}\n' for c in cases) + 'q\n'
    for c, row in zip(cases, _rows(script)):
        if row[0] != '1':
            continue
        x, y, w, h = (int(v) for v in row[1:])
        assert w == h, c                      # square, so the resize does not stretch
        assert w % 2 == 0 and x % 2 == 0 and y % 2 == 0, c
        assert 0 <= x and 0 <= y, c
        assert x + w <= FRAME and y + h <= FRAME, c


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

        roi = roi_reference(bx, by, bw, bh, FRAME, FRAME)
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

def _plaus(points, box=None, score=GOOD_SCORE, frame=FRAME):
    box = box or box_around(points)
    rows = _rows(f'plaus {box[0]} {box[1]} {box[2]} {box[3]} {score} '
                 f'{frame} {frame} {flat(points)}\nq\n')
    return rows[0][0] == '1'


def _why(points, box=None, score=GOOD_SCORE, frame=FRAME):
    """Which check failed, so a test can assert the diagnosis and not just the verdict.

    The reason is what makes a rejection actionable: the first version of this gate
    reported a bare count, and on hardware that read "gate dropped 2" while rejecting
    every real candidate - indistinguishable from an empty frame."""
    box = box or box_around(points)
    rows = _rows(f'plaus {box[0]} {box[1]} {box[2]} {box[3]} {score} '
                 f'{frame} {frame} {flat(points)}\nq\n')
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
    pts = face_points(120, 120)
    a = math.radians(70.0)
    ca, sa = math.cos(a), math.sin(a)
    rot = [(120 + ca * (x - 120) - sa * (y - 120),
            120 + sa * (x - 120) + ca * (y - 120)) for x, y in pts]
    assert not _plaus(rot, box_around(rot))


def test_an_implausible_interocular_distance_is_rejected():
    wide = face_points(120, 120, eye_dist=150.0)             # eyes wider than the face
    assert not _plaus(wide, (100, 100, 60, 60))


# --- the checks added to reject hands, objects and low light ----------------

def test_a_low_confidence_detection_is_rejected_however_good_its_geometry():
    """The single most common false positive is a real-looking box the detector
    itself barely believes. The coarse stage runs at 0.10 on this camera, so those
    genuinely arrive; a perfect synthetic face at 0.20 must still not be believed."""
    pts = face_points(120, 120)
    assert _plaus(pts, score=0.95)
    assert not _plaus(pts, score=0.20)
    assert _why(pts, score=0.20) == 'score-too-low'


def test_a_face_too_small_for_a_usable_eye_crop_is_rejected():
    """Below FACE_MIN_SIDE_FRAC the eye patch is smaller than eyestate.py's own 8 px
    floor, so any closure read off it is upsampled noise - the detection may even be
    real, and it is still not usable."""
    tiny = face_points(120, 120, eye_dist=8.0)
    assert not _plaus(tiny)
    assert _why(tiny) == 'face-too-small'


def test_something_filling_the_frame_is_rejected():
    """A hand or a jacket up against the lens. At driving distance a head does not
    occupy 95% of the short side."""
    pts = face_points(120, 120, eye_dist=110.0)
    box = (2, 2, 236, 236)
    assert not _plaus(pts, box)
    assert _why(pts, box) == 'face-too-large'


def test_a_box_that_is_not_head_shaped_is_rejected():
    """The check that fires on a hand even when its keypoints land plausibly. A
    raised hand's box is roughly 1:3; a head's is close to 1:1."""
    pts = face_points(120, 120, eye_dist=30.0)
    assert not _plaus(pts, hand_box())
    assert _why(pts, hand_box()) == 'box-not-head-shaped'


def test_a_nose_outside_the_eye_pair_is_rejected():
    """No head pose puts the nose beyond the eyes horizontally. A landmark set fitted
    to a hand does it readily, which is exactly why this check exists.

    Given a deliberately *square* box, so the aspect check cannot claim this first:
    the point is that the landmarks alone are enough."""
    pts = hand_points()
    box = square_box(pts)
    assert not _plaus(pts, box)
    assert _why(pts, box) == 'nose-outside-eye-pair'


def test_a_collapsed_mouth_is_rejected():
    """Five keypoints regressed onto a smooth blob - a headrest - pull toward its
    centre, and there is nothing in the image to separate the mouth corners."""
    pts = headrest_points()
    assert not _plaus(pts)
    assert _why(pts) == 'mouth-too-narrow'


def test_a_mouth_wider_than_the_head_is_rejected():
    pts = phone_points()
    box = square_box(pts)
    assert not _plaus(pts, box)
    assert _why(pts, box) == 'mouth-too-wide'


def test_a_moderately_turned_head_is_still_accepted():
    """The lateral checks must not cost recall on the pose where the eye crops are
    already marginal. A yaw of 0.35 is a substantial turn and has to pass."""
    assert _plaus(face_points(120, 120, yaw=0.35))


def test_a_nan_score_is_rejected_rather_than_slipping_through():
    """`score < threshold` is false for NaN and would let it past; the check is
    written as `not (score >= threshold)` for exactly this case."""
    pts = face_points(120, 120)
    assert not _plaus(pts, score='nan')
    assert _why(pts, score='nan') == 'score-too-low'


def test_frame_relative_checks_are_skipped_when_there_is_no_frame():
    """A caller with no frame context gets the frame-independent checks and nothing
    invented on its behalf. Same face, same box, no frame: size stops mattering."""
    tiny = face_points(120, 120, eye_dist=8.0)
    assert _why(tiny, frame=FRAME) == 'face-too-small'
    assert _why(tiny, frame=0) == 'ok'


# --------------------------------------------------------------------------- #
# picking the driver
# --------------------------------------------------------------------------- #

def _pick(candidates, track=None, rows=None, frame=FRAME):
    """candidates: [(box, points)] or [(box, points, score)]."""
    rows = rows or _rows
    tv = 1 if track else 0
    t = track or (0, 0, 0, 0)
    parts = [f'pick {len(candidates)} {tv} {t[0]} {t[1]} {t[2]} {t[3]} {frame} {frame}']
    for cand in candidates:
        box, pts = cand[0], cand[1]
        score = cand[2] if len(cand) > 2 else GOOD_SCORE
        parts.append(f'{box[0]} {box[1]} {box[2]} {box[3]} {score} {flat(pts)}')
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


def test_a_hand_loses_to_a_smaller_real_face():
    """The case the whole gate is for: a hand raised in front of the camera is a
    bigger, higher-scoring blob than a face further back."""
    real = face_points(70, 120, eye_dist=36.0)
    hand = hand_points(180, 120)
    cands = [(hand_box(180, 120), hand, 0.99), (box_around(real), real, 0.90)]
    assert _pick(cands) == 1


def test_no_plausible_candidate_means_no_detection():
    junk = [(120.0, 120.0)] * 5
    assert _pick([((100, 100, 60, 60), junk)]) == -1


def test_an_empty_frame_means_no_detection():
    assert _pick([]) == -1


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
# IoU and continuity
# --------------------------------------------------------------------------- #

def test_iou_basics():
    rows = _rows('iou 0 0 10 10 0 0 10 10\n'
                 'iou 0 0 10 10 20 20 10 10\n'
                 'iou 0 0 10 10 5 0 10 10\n'
                 'q\n')
    assert float(rows[0][0]) == pytest.approx(1.0)
    assert float(rows[1][0]) == pytest.approx(0.0)
    assert float(rows[2][0]) == pytest.approx(50 / 150)


def _cont(a, b):
    row = _rows(f'cont {a[0]} {a[1]} {a[2]} {a[3]} {b[0]} {b[1]} {b[2]} {b[3]}\nq\n')[0]
    return row[0] == '1'


def test_a_head_moving_at_a_human_speed_stays_continuous():
    """Half a box width between detections - a brisk turn at 5 detections a second -
    must not break the track, or the gate would reject the very movement it exists to
    follow through."""
    a = (100, 100, 60, 60)
    assert _cont(a, (130, 100, 60, 60))
    assert _cont(a, (100, 130, 60, 60))
    assert _cont(a, (128, 128, 60, 60))


def test_a_box_that_teleports_is_not_the_same_face():
    a = (100, 100, 60, 60)
    assert not _cont(a, (200, 100, 60, 60))


def test_a_face_cannot_double_in_size_between_detections():
    a = (100, 100, 60, 60)
    assert _cont(a, (100, 100, 90, 90))          # 1.5x, within tolerance
    assert not _cont(a, (100, 100, 130, 130))    # 2.2x is a different object


def test_continuity_against_an_unset_box_is_true():
    """There is nothing to be discontinuous with. Answering false would need a
    special case at every call site to let a first detection through."""
    assert _cont((0, 0, 0, 0), (100, 100, 60, 60))


# --------------------------------------------------------------------------- #
# the track, driven through whole sequences
# --------------------------------------------------------------------------- #

class Track:
    """Drives the compiled FaceTrack through a sequence of detection attempts.

    The harness runs as a fresh process per invocation, so the C++ object cannot be
    kept alive between calls. The sequence is therefore replayed from `treset` every
    time and only the newly appended steps are returned. Wasteful and completely
    deterministic, which is the right trade for a test: the alternative is a
    long-lived subprocess whose state can drift between assertions in ways that are
    very hard to see.
    """

    FIELDS = ('present', 'fresh', 'confirmed', 'acquired', 'lost', 'reject',
              'misses', 'agreed', 'x', 'y', 'w', 'h')

    def __init__(self, rows, frame=FRAME):
        self._rows = rows
        self.frame = frame
        self._steps = []

    def _parse(self, row):
        out = {}
        for name, value in zip(self.FIELDS, row):
            out[name] = value if name == 'reject' else int(value)
        for k in ('present', 'fresh', 'confirmed', 'acquired', 'lost'):
            out[k] = bool(out[k])
        return out

    def _run(self, new_lines):
        before = len(self._steps)
        self._steps.extend(new_lines)
        # +1 row for `treset`'s own "ok".
        rows = self._rows('treset\n' + ''.join(self._steps) + 'q\n')
        return [self._parse(r) for r in rows[1 + before:]]

    def peek(self, n=1):
        """n frames between detection attempts. Not evidence either way."""
        return self._run(['tpeek\n'] * n)

    def miss(self, n=1):
        """n detection attempts that found nothing at all - an empty frame."""
        zeros = flat([(0.0, 0.0)] * 5)
        return self._run([f'tupdate 0 0 0 0 0 0 {self.frame} {self.frame} {zeros}\n'] * n)

    def see(self, points, box=None, score=GOOD_SCORE, n=1):
        box = box or box_around(points)
        line = (f'tupdate 1 {box[0]} {box[1]} {box[2]} {box[3]} {score} '
                f'{self.frame} {self.frame} {flat(points)}\n')
        return self._run([line] * n)


@pytest.fixture
def track(gate):
    return Track(gate)


def test_one_good_detection_is_not_yet_a_driver(track):
    """A single frame is not evidence. This is the property the old integer-counter
    version could not express, and it is what a headrest that scores well once looks
    like."""
    r = track.see(face_points(120, 120))[0]
    assert r['fresh'] and not r['present'] and not r['confirmed']
    assert r['agreed'] == 1


def test_two_agreeing_detections_confirm_a_driver(track):
    steps = track.see(face_points(120, 120), n=2)
    assert not steps[0]['present']
    assert steps[1]['present'] and steps[1]['confirmed'] and steps[1]['acquired']


def test_a_hand_never_becomes_a_driver(track):
    """Two seconds of a hand at five detections a second. Not one of them may be
    accepted, and presence must stay false throughout - a single accepted frame is
    enough to start feeding a PERCLOS window with measurements of a hand."""
    steps = track.see(hand_points(), box=hand_box(), n=10)
    assert not any(s['fresh'] for s in steps)
    assert not any(s['present'] for s in steps)
    assert {s['reject'] for s in steps} == {'box-not-head-shaped'}


def test_an_empty_frame_never_becomes_a_driver(track):
    steps = track.miss(10)
    assert not any(s['present'] or s['fresh'] for s in steps)
    assert steps[-1]['misses'] == 10


def test_low_light_detections_are_refused_and_no_driver_is_reported(track):
    """What low light does to this detector is collapse its confidence, not distort
    its geometry: it still emits boxes, and they are still roughly face-shaped,
    because the coarse stage is running at a 0.10 threshold. The score is the only
    thing that separates them from a real face, which is why the gate checks it."""
    rng = random.Random(11)
    for _ in range(12):
        pts = face_points(120 + rng.uniform(-6, 6), 120 + rng.uniform(-6, 6),
                          eye_dist=rng.uniform(40, 60))
        r = track.see(pts, score=round(rng.uniform(0.12, 0.45), 3))[0]
        assert not r['fresh'] and not r['present']
        assert r['reject'] == 'score-too-low'


def test_a_driver_survives_a_brief_occlusion(track):
    """A hand across the face, a passing shadow, a detector that drops the face
    exactly when the eyes close - the moment of interest. Presence must hold."""
    pts = face_points(120, 120)
    track.see(pts, n=CONFIRM)
    held = track.miss(HOLD)
    assert all(s['present'] for s in held), 'the hold is what covers a closed eye'
    assert all(not s['fresh'] for s in held)
    back = track.see(pts)[0]
    assert back['present'] and back['fresh']


def test_the_hold_expires_and_the_track_is_given_up(track):
    pts = face_points(120, 120)
    track.see(pts, n=CONFIRM)
    steps = track.miss(HOLD + 1)
    assert steps[-2]['present']
    assert not steps[-1]['present']
    assert steps[-1]['lost'], 'the caller needs the edge to reset the detector search'


def test_a_driver_moving_their_head_keeps_the_same_track(track):
    """Continuity must not be so tight that ordinary movement breaks it. The head
    walks 20 px per detection and grows as the driver leans in."""
    track.see(face_points(90, 110, eye_dist=44.0), n=CONFIRM)
    steps = []
    for i in range(1, 7):
        steps += track.see(face_points(90 + 20 * i, 110 + 6 * i,
                                       eye_dist=44.0 + 3.0 * i))
    assert all(s['fresh'] and s['present'] for s in steps)
    assert not any(s['acquired'] or s['lost'] for s in steps)


def test_a_passenger_appearing_mid_track_does_not_inherit_the_driver(track):
    """The failure this exists to prevent: the track steps sideways onto a different
    person and keeps the driver's confirmation, so the alarm silently changes who it
    is about. The substitution must be refused outright while the track is warm."""
    track.see(face_points(70, 120, eye_dist=40.0), n=CONFIRM)
    r = track.see(face_points(200, 120, eye_dist=40.0))[0]
    assert not r['fresh']
    assert r['reject'] == 'moved-too-far'
    assert r['present'], 'the real driver is still held; the substitute was refused'


def test_a_face_in_a_new_place_after_a_long_gap_is_reacquired_but_re_earned(track):
    """The other half: a gate that only tightens eventually locks onto nothing. After
    the reacquisition window the driver may genuinely have moved, so the candidate is
    accepted - as a NEW track, which has to confirm again before anyone is present."""
    track.see(face_points(70, 120, eye_dist=40.0), n=CONFIRM)
    track.miss(REACQUIRE_AFTER)
    first = track.see(face_points(200, 120, eye_dist=40.0))[0]
    assert first['fresh'], 'the continuity requirement is dropped after the window'
    assert not first['present'], 'but presence is re-earned, never inherited'
    assert first['lost'], 'the old track ended on this update'
    second = track.see(face_points(200, 120, eye_dist=40.0))[0]
    assert second['present'] and second['acquired']


def test_a_pending_track_does_not_survive_a_single_miss(track):
    """A half-formed track was never believed, so there is nothing to hold. Leaving
    one around would let an unrelated candidate resume it and reach confirmation in
    one frame instead of two."""
    first = track.see(face_points(120, 120))[0]
    assert first['agreed'] == 1
    after = track.miss(1)[0]
    assert after['agreed'] == 0 and not after['present']
    resumed = track.see(face_points(120, 120))[0]
    assert not resumed['present'], 'confirmation restarts from scratch'


def test_peek_does_not_advance_the_hold(track):
    """Frames between detection attempts are not evidence either way. If peek counted
    as a miss, raising DETECT_EVERY would silently shorten how long a face is held."""
    track.see(face_points(120, 120), n=CONFIRM)
    steps = track.peek(20)
    assert all(s['present'] for s in steps), 'presence survives any number of peeks'
    assert steps[-1]['misses'] == 0, 'and the miss counter must not move'


def test_alternating_hand_and_nothing_never_produces_a_driver(track):
    """The pathological input for a naive confirmation counter: something plausible
    every other attempt. Nothing here is plausible, but the interleaving is what
    would defeat a counter that is not reset on a miss."""
    for _ in range(8):
        assert not track.see(hand_points(), box=hand_box())[0]['present']
        assert not track.miss(1)[0]['present']


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


def test_every_reject_value_has_a_name():
    """A missing case in face_gate_reject_name() prints "?" - a diagnosis that says
    nothing, on the one path that exists to say something."""
    cases = [
        (face_points(120, 120), None),
        ([(120.0, 120.0)] * 5, (100, 100, 40, 40)),
        (hand_points(), square_box(hand_points())),
        (headrest_points(), square_box(headrest_points())),
        (phone_points(), square_box(phone_points())),
        (face_points(120, 120), hand_box()),
        (face_points(120, 120, eye_dist=8.0), None),
    ]
    names = {_why(pts, box) for pts, box in cases}
    assert '?' not in names


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
