"""Behaviour event logic, driven by synthetic traces with known ground truth."""
import math

from drowsyguard.behavior import (BehaviorAnalyzer, Baseline, EventRate, EyeGate,
                                  FaceGeometry, MICROSLEEP_MIN_S, NOD_PITCH_DELTA,
                                  face_geometry)

FPS = 30.0


def upright_landmarks(jaw=1.05, nose_frac=0.55, roll_deg=0.0, eye_dist=60.0, cx=320, cy=200,
                      mouth_w=0.60):
    """Synthesize landmarks with a chosen jaw drop / pitch / roll / mouth width.

    `mouth_w` is the corner separation as a fraction of eye distance, i.e. exactly
    FaceGeometry.mouth_ratio. It is a knob because a real mouth opening moves the
    corners inward as well as down, and that inward motion is half the yawn signal
    five landmarks carry - see MOUTH_NARROW_W.
    """
    half = eye_dist / 2.0
    mouth_y = jaw * eye_dist
    nose_y = nose_frac * mouth_y
    mouth_x = eye_dist * mouth_w / 2.0
    pts = [(-half, 0.0), (half, 0.0), (0.0, nose_y), (-mouth_x, mouth_y), (mouth_x, mouth_y)]
    a = math.radians(roll_deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + ca * x - sa * y, cy + sa * x + ca * y) for x, y in pts]


def feed(an, frames, p_closed=0.0, jaw=1.05, nose_frac=0.55, perclos=0.0, mouth_w=0.60,
         fresh=True):
    out = []
    for _ in range(frames):
        g = face_geometry(upright_landmarks(jaw=jaw, nose_frac=nose_frac, mouth_w=mouth_w))
        out.append(an.update(p_closed, g, perclos, fresh=fresh))
    return out


def events_of(states):
    return [e for s in states for e in s.events]


def nose_frac_holding_nose_still(jaw, base_jaw=1.05, base_nose_frac=0.55):
    """The nose_frac that leaves the nose where it was when only the jaw moved.

    nose_frac is measured against the eye-to-mouth span, so opening the jaw shrinks
    it even with the head perfectly still. This inverts that so a test can open the
    mouth without moving the head at all.
    """
    return base_nose_frac * base_jaw / jaw


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
    # A closure is only classified once it is over, and CUE_GAP_S of open frames is
    # what makes it over - so the trailing window has to outlast the tolerance.
    states = feed(an, 15)
    fired = events_of(states)
    assert 'blink' in fired
    assert 'microsleep' not in fired


def test_sustained_closure_is_a_microsleep():
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 60)
    states = feed(an, 45, p_closed=0.95)           # 1.5 s closure
    states += feed(an, 15)
    assert 'microsleep' in events_of(states)


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
    states = feed(an, 12, nose_frac=0.55)          # and returns, past CUE_GAP_S
    assert 'nod' in events_of(states)


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


def test_a_yawn_that_also_closes_the_eyes_is_not_a_sneeze():
    """The discriminator SNEEZE_MOUTH_LEAD_S exists for.

    A yawn opens the mouth wide and closes the eyes, so it clears SNEEZE_JAW_DELTA
    comfortably - the absolute level cannot tell the two apart. What differs is the
    order: the mouth has been open for a second by the time the eyes close. Getting
    this wrong is worse than missing a sneeze, because the suppression window would
    then silence a genuine drowsiness cue for SNEEZE_MAX_S.
    """
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 90)
    states = feed(an, 30, jaw=1.32)                              # 1 s of open mouth
    states += feed(an, 45, p_closed=0.95, jaw=1.32)              # then the eyes close
    states += feed(an, 5, jaw=1.05)
    fired = events_of(states)
    assert 'sneeze' not in fired, 'a yawn must not be reclassified as a sneeze'
    assert 'microsleep' in fired, 'and the closure must still be alarmed on'
    assert states[-1].sneeze_count == 0


def test_a_sneeze_produces_exactly_one_alert():
    """Detection is per closure; the announcement is an edge. The distinction is the
    difference between a counter that is honest and a speaker that is bearable."""
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 90)
    states = feed(an, 24, p_closed=0.95, jaw=1.32)
    states += feed(an, 10, jaw=1.05)
    assert sum(1 for s in states if s.sneeze_alert) == 1
    assert states[-1].sneeze_alerts == 1
    assert states[-1].sneeze_count == 1


def test_a_burst_of_sneezes_is_counted_fully_but_announced_once():
    """One sneeze is often two or three closures a second apart. Each is a real
    detection and belongs in the counter; three announcements in three seconds is
    noise that trains a driver to ignore the speaker."""
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 90)
    states = []
    for _ in range(3):                       # three closures, ~1.1 s apart
        states += feed(an, 20, p_closed=0.95, jaw=1.32)
        states += feed(an, 13, jaw=1.05)
    assert states[-1].sneeze_count == 3, 'every sneeze is counted'
    assert states[-1].sneeze_alerts == 1, 'and the burst is one announcement'


def test_sneezes_further_apart_than_the_cooldown_are_announced_separately():
    """The other side of the same rule: the cooldown collapses a fit, it does not
    collapse a drive."""
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 90)
    states = []
    for _ in range(3):
        states += feed(an, 20, p_closed=0.95, jaw=1.32)
        states += feed(an, 120, jaw=1.05)    # 4 s apart, past SNEEZE_ALERT_COOLDOWN_S
    assert states[-1].sneeze_count == 3
    assert states[-1].sneeze_alerts == 3


def test_the_sneeze_alert_is_an_edge_not_a_level():
    """The caller triggers audio directly on this flag, so it must be true on exactly
    one frame. A level would announce once per frame for the length of the closure."""
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 90)
    states = feed(an, 24, p_closed=0.95, jaw=1.32)
    flags = [s.sneeze_alert for s in states]
    assert flags.count(True) == 1
    assert not states[0].sneeze_alert, 'not on the first frame of the closure either'


def test_sneeze_suppression_still_holds_the_score_down():
    """The original purpose, unchanged by adding the announcement: an involuntary
    reflex must not drive a drowsiness alert."""
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 90)
    states = feed(an, 24, p_closed=0.95, jaw=1.32, perclos=0.5)
    assert states[-1].suppressed
    # Capped at the PERCLOS term alone: the long-blink, yawn and nod contributions
    # accumulated during the reflex are excluded.
    from drowsyguard.behavior import W_PERCLOS
    assert states[-1].score <= W_PERCLOS * 0.5 + 1e-6


def test_reset_clears_the_sneeze_alert_state():
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 90)
    feed(an, 24, p_closed=0.95, jaw=1.32)
    an.reset()
    feed(an, 90)
    states = feed(an, 24, p_closed=0.95, jaw=1.32)
    assert states[-1].sneeze_count == 1
    assert states[-1].sneeze_alerts == 1, 'the cooldown must not survive a reset'


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


# ---------- the eye gate ----------

def test_eye_gate_removes_an_isolated_flip_without_moving_the_edges():
    g = EyeGate(threshold=0.5, hysteresis=0.10)
    seq = [0.0, 0.0, 0.9, 0.9, 0.9, 0.02, 0.9, 0.9, 0.9, 0.0, 0.0, 0.0]
    out = [g.update(v) for v in seq]
    assert out[5] is True             # the flipped frame is filtered out entirely
    assert out[:3] == [False, False, False]
    assert out[3] is True             # one frame of lag on the closing edge
    assert out[9] is True             # and one on the opening edge
    assert out[10] is False


def test_a_probability_sitting_on_the_threshold_does_not_emit_a_burst_of_blinks():
    """Hysteresis, not smoothing, is what this fixes. A probability dithering across
    0.5 used to produce one counted blink per frame - 30 a second into a rate window
    whose full scale is 12 a minute."""
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 60)
    states = []
    for i in range(60):
        states += feed(an, 1, p_closed=0.45 + 0.1 * (i % 2))
    states += feed(an, 15)
    assert 'blink' not in events_of(states)
    assert states[-1].blink_rate == 0


# ---------- microsleep: the alarm must not wait for the driver to wake up ----------

def test_microsleep_is_announced_while_the_eyes_are_still_shut():
    """The defect this replaces: closures were classified only on release, so the
    warning for "the driver is asleep" was gated on the driver waking up. Eyes shut
    for five seconds produced five seconds of silence."""
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 60)
    states = feed(an, 45, p_closed=0.95)          # 1.5 s, and they never reopen
    fired = events_of(states)
    assert fired.count('microsleep') == 1         # once, not once per frame

    at = next(i for i, st in enumerate(states) if 'microsleep' in st.events)
    # At the threshold, plus at most the gate's one frame of lag on each edge.
    assert MICROSLEEP_MIN_S <= (at + 1) / FPS <= MICROSLEEP_MIN_S + 3.0 / FPS
    assert states[at].closed                      # still shut when it fired


def test_one_noisy_frame_does_not_split_a_microsleep():
    """The eye model is IR-trained and scores AUC 0.62 on visible light, so a single
    frame reading "open" inside a real closure is its normal behaviour. That frame
    used to cut a 1.5 s closure into two 0.7 s long blinks, which is to say the most
    dangerous event was the one a single noisy frame erased."""
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 60)
    states = feed(an, 20, p_closed=0.95)
    states += feed(an, 1, p_closed=0.02)          # one frame flips
    states += feed(an, 24, p_closed=0.95)
    states += feed(an, 15)
    fired = events_of(states)
    assert fired.count('microsleep') == 1
    assert 'blink' not in fired                   # nor is the flip its own blink


def test_the_gap_tolerance_does_not_promote_a_long_blink_to_a_microsleep():
    """A closure is measured to where the eyes reopened, not to where the tolerance
    expired. Measuring to the latter would add CUE_GAP_S to every closure and turn a
    0.9 s long blink into a 1.1 s microsleep."""
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 60)
    states = feed(an, 27, p_closed=0.95)          # 0.9 s
    states += feed(an, 15)
    fired = events_of(states)
    assert 'long_blink' in fired
    assert 'microsleep' not in fired


# ---------- yawn: use both halves of the signal five landmarks carry ----------

def test_a_mouth_that_narrows_is_a_yawn_even_when_the_jaw_drop_is_small():
    """The corners pull inward as the jaw opens wide, and that inward motion is half
    the yawn signal available from five landmarks. jaw_drop alone cannot see this
    opening at all."""
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 90, jaw=1.05, mouth_w=0.60)
    states = feed(an, 60, jaw=1.12, mouth_w=0.42)
    states += feed(an, 15)
    assert 'yawn' in events_of(states)

    # Same jaw drop, corners unmoved: under JAW_OPEN_DELTA, and correctly ignored.
    lonely = BehaviorAnalyzer(fps=FPS)
    feed(lonely, 90, jaw=1.05, mouth_w=0.60)
    quiet = feed(lonely, 60, jaw=1.12, mouth_w=0.60)
    quiet += feed(lonely, 15)
    assert 'yawn' not in events_of(quiet)


def test_one_noisy_frame_does_not_split_a_yawn():
    """YAWN_MIN_S is 1.2 s of continuous opening; one frame of landmark noise used to
    reset the timer and lose the yawn."""
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 90, jaw=1.05)
    states = feed(an, 20, jaw=1.30)
    states += feed(an, 1, jaw=1.05)               # one frame of noise
    states += feed(an, 20, jaw=1.30)
    states += feed(an, 15, jaw=1.05)
    assert events_of(states).count('yawn') == 1


# ---------- nod ----------

def test_a_mouth_movement_with_the_head_still_is_not_a_nod():
    """nose_frac divides by the eye-to-mouth span, so a jaw drop shrinks it even with
    the head perfectly still: a 0.25 jaw drop reads as a 0.11 pitch drop, well past
    NOD_PITCH_DELTA.

    Measured against the previous version with the head held still, a mouth open for
    0.5 s fired a nod and one open for 1.0 s fired a nod - so ordinary speech and
    chewing were being reported as head nods. Openings past NOD_MAX_S escaped only by
    outlasting it.

    Both durations are checked here because they exercise different halves of the
    fix: the 1.0 s case is a false nod on its own, and the 1.4 s case used to fire a
    yawn AND a nod, which is double-counted in the fused score and announced as the
    nod, because that bit is tested first."""
    for frames, expect_yawn in ((30, False), (42, True)):        # 1.0 s, 1.4 s
        an = BehaviorAnalyzer(fps=FPS)
        feed(an, 90, jaw=1.05, nose_frac=0.55)
        nf = nose_frac_holding_nose_still(1.30)   # the nose does not move at all
        states = feed(an, frames, jaw=1.30, nose_frac=nf)
        states += feed(an, 15, jaw=1.05, nose_frac=0.55)
        fired = events_of(states)
        assert 'nod' not in fired, frames
        assert ('yawn' in fired) is expect_yawn, frames
        assert not any(st.head_down for st in states)
        # The single-channel cue really did cross its threshold; the second channel
        # is what rejects it, so this would fail against the old logic.
        assert any(st.pitch_dev <= -NOD_PITCH_DELTA for st in states)


def test_single_frame_pitch_jitter_is_not_a_nod():
    """The pitch proxy is a ratio of two five-point distances, so one frame of keypoint
    noise can carry it past the threshold and back. Without NOD_MIN_S, six such
    frames a minute drive the nod cue to full scale on their own."""
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 90)
    states = []
    for _ in range(8):
        states += feed(an, 1, nose_frac=0.40)
        states += feed(an, 20)
    assert 'nod' not in events_of(states)
    assert states[-1].nod_rate == 0


def test_a_shallow_pitch_excursion_is_not_a_nod():
    """Over the threshold that starts an excursion, under the peak a nod must reach."""
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 90)
    states = feed(an, 15, nose_frac=0.47)
    states += feed(an, 12)
    assert 'nod' not in events_of(states)


def test_one_noisy_frame_does_not_split_a_nod_into_two():
    """Over-counting is the worse failure here: two counted nods are twice the
    contribution to the fused risk score of the one that actually happened."""
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 90)
    states = feed(an, 12, nose_frac=0.44)
    states += feed(an, 1, nose_frac=0.55)         # one frame back up
    states += feed(an, 12, nose_frac=0.44)
    states += feed(an, 12, nose_frac=0.55)
    assert events_of(states).count('nod') == 1


# ---------- held landmarks ----------

def test_held_landmarks_do_not_advance_the_geometric_cues():
    """A held box is byte-identical to the last real one, so it is evidence about a
    moment that has passed. Feeding it in pushed duplicate samples into a 10 s
    baseline window and kept the mouth and nod timers running - and a head pitching
    far enough down to lose the detector is exactly when that happened."""
    an = BehaviorAnalyzer(fps=FPS)
    feed(an, 90, jaw=1.05)
    states = feed(an, 60, jaw=1.30, fresh=False)
    assert 'yawn' not in events_of(states)
    assert all(st.stale for st in states)
    assert not any(st.mouth_open for st in states)
