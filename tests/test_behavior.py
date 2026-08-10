"""Behaviour event logic, driven by synthetic traces with known ground truth."""
import math

from drowsyguard.behavior import (BehaviorAnalyzer, Baseline, EventRate, FaceGeometry,
                                  face_geometry)

FPS = 30.0


def upright_landmarks(jaw=1.05, nose_frac=0.55, roll_deg=0.0, eye_dist=60.0, cx=320, cy=200):
    """Synthesize landmarks with a chosen jaw drop / pitch / roll."""
    half = eye_dist / 2.0
    mouth_y = jaw * eye_dist
    nose_y = nose_frac * mouth_y
    pts = [(-half, 0.0), (half, 0.0), (0.0, nose_y), (-half * 0.6, mouth_y), (half * 0.6, mouth_y)]
    a = math.radians(roll_deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + ca * x - sa * y, cy + sa * x + ca * y) for x, y in pts]


def feed(an, frames, p_closed=0.0, jaw=1.05, nose_frac=0.55, perclos=0.0):
    out = []
    for _ in range(frames):
        g = face_geometry(upright_landmarks(jaw=jaw, nose_frac=nose_frac))
        out.append(an.update(p_closed, g, perclos))
    return out


# ---------- geometry ----------

def test_face_geometry_recovers_roll_and_jaw():
    g = face_geometry(upright_landmarks(jaw=1.05, roll_deg=0.0))
    assert g.valid
    assert abs(g.roll) < 1e-6
    assert abs(g.jaw_drop - 1.05) < 1e-6

    tilted = face_geometry(upright_landmarks(jaw=1.05, roll_deg=15.0))
    assert abs(tilted.roll - 15.0) < 1e-6
    # jaw_drop is measured in the de-rotated frame, so tilt must not change it.
    assert abs(tilted.jaw_drop - 1.05) < 1e-6

    wide = face_geometry(upright_landmarks(jaw=1.30))
    assert wide.jaw_drop > g.jaw_drop


def test_face_geometry_rejects_bad_input():
    assert not face_geometry([]).valid
    assert not face_geometry([(0, 0)] * 4).valid
    assert not face_geometry([(5, 5)] * 5).valid          # zero eye distance


# ---------- helpers ----------

def test_baseline_needs_history_then_reports_deviation():
    b = Baseline(window=50, min_samples=10)
    for _ in range(9):
        assert b.update(1.0) == 0.0        # too early to judge
    b.update(1.0)
    assert b.ready
    assert abs(b.update(1.2) - 0.2) < 1e-9


def test_event_rate_windows_out_old_events():
    r = EventRate(window_s=10.0)
    for t in (1.0, 2.0, 3.0):
        r.add(t)
    assert r.count() == 3
    r.rate(30.0)
    assert r.count() == 0


# ---------- eye events ----------

def test_short_closure_is_a_blink_not_a_microsleep():
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 60)                                   # settle baselines, eyes open
    feed(an, 6, p_closed=0.9)                      # 0.2 s closure
    states = feed(an, 5)
    fired = [e for s in states for e in s.events]
    assert 'blink' in fired
    assert 'microsleep' not in fired


def test_sustained_closure_is_a_microsleep():
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 60)
    feed(an, 45, p_closed=0.95)                    # 1.5 s closure
    states = feed(an, 5)
    fired = [e for s in states for e in s.events]
    assert 'microsleep' in fired


def test_closure_duration_is_reported_while_closed():
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 60)
    states = feed(an, 30, p_closed=0.9)
    assert states[-1].closure_s > 0.9


# ---------- yawn ----------

def test_sustained_mouth_opening_is_a_yawn():
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 90, jaw=1.05)                         # baseline: mouth closed
    states = feed(an, 60, jaw=1.30)                # 2 s of open mouth
    fired = [e for s in states for e in s.events]
    assert 'yawn' in fired
    assert states[-1].yawn_rate > 0


def test_brief_mouth_opening_is_not_a_yawn():
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 90, jaw=1.05)
    states = feed(an, 12, jaw=1.30)                # 0.4 s, e.g. speech
    states += feed(an, 10, jaw=1.05)
    assert 'yawn' not in [e for s in states for e in s.events]


def test_yawn_fires_once_per_opening():
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 90, jaw=1.05)
    states = feed(an, 150, jaw=1.30)               # 5 s held open
    assert [e for s in states for e in s.events].count('yawn') == 1


# ---------- nod ----------

def test_head_pitch_excursion_is_a_nod():
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 90, nose_frac=0.55)
    feed(an, 15, nose_frac=0.44)                   # head drops
    states = feed(an, 5, nose_frac=0.55)           # and returns
    assert 'nod' in [e for s in states for e in s.events]


def test_prolonged_head_down_is_not_counted_as_a_nod():
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 90, nose_frac=0.55)
    feed(an, 90, nose_frac=0.44)                   # 3 s, exceeds NOD_MAX_S
    states = feed(an, 5, nose_frac=0.55)
    assert 'nod' not in [e for s in states for e in s.events]


# ---------- sneeze ----------

def test_sneeze_is_detected_and_suppresses_the_score():
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 90)
    # Eyes shut and mouth flung open together, resolving inside a second.
    states = feed(an, 24, p_closed=0.95, jaw=1.32, perclos=0.5)
    fired = [e for s in states for e in s.events]
    assert 'sneeze' in fired
    assert states[-1].sneeze_count == 1
    assert states[-1].suppressed


def test_sneeze_closure_is_not_logged_as_microsleep():
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 90)
    states = feed(an, 30, p_closed=0.95, jaw=1.32)     # 1 s: sneeze-like
    states += feed(an, 10, jaw=1.32)                   # eyes reopen
    fired = [e for s in states for e in s.events]
    assert 'sneeze' in fired
    assert 'microsleep' not in fired


def test_slow_closure_without_mouth_movement_is_still_a_microsleep():
    """The suppression must be specific to sneezes, not blanket."""
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 90)
    states = feed(an, 45, p_closed=0.95, jaw=1.05)     # closed, mouth normal
    states += feed(an, 5)
    fired = [e for s in states for e in s.events]
    assert 'microsleep' in fired
    assert 'sneeze' not in fired


# ---------- fusion ----------

def test_score_rises_with_perclos():
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 60)
    low = feed(an, 2, perclos=0.0)[-1].score
    high = feed(an, 2, perclos=0.9)[-1].score
    assert high > low


def test_score_is_bounded():
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 60)
    for _ in range(20):
        s = feed(an, 30, p_closed=0.95, jaw=1.35, nose_frac=0.40, perclos=1.0)[-1]
        assert 0.0 <= s.score <= 1.0


def test_behaviour_cues_add_risk_beyond_eye_closure():
    """Yawns and nods must move the score even when PERCLOS is identical."""
    quiet = BehaviorAnalyzer(fps=FPS)
    feed(quiet, 90)
    quiet_score = feed(quiet, 2, perclos=0.2)[-1].score

    busy = BehaviorAnalyzer(fps=FPS)
    feed(busy, 90)
    for _ in range(3):                       # several yawns and nods
        feed(busy, 45, jaw=1.30, perclos=0.2)
        feed(busy, 10, jaw=1.05, perclos=0.2)
        feed(busy, 15, nose_frac=0.44, perclos=0.2)
        feed(busy, 10, nose_frac=0.55, perclos=0.2)
    busy_score = busy.update(0.0, face_geometry(upright_landmarks()), 0.2).score
    assert busy_score > quiet_score


def test_reset_clears_state():
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 90, jaw=1.05)
    feed(an, 60, jaw=1.30)
    an.reset()
    s = feed(an, 1)[-1]
    assert s.yawn_rate == 0 and s.sneeze_count == 0 and not s.baselines_ready
