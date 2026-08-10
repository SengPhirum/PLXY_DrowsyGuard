"""Locks risk.RiskFilter to the semantics of firmware/esp32s3/main/risk_filter.cpp."""
from drowsyguard.risk import RiskFilter


def test_requires_sustained_risk_before_firing():
    f = RiskFilter(trigger=0.7, required=3, cooldown=5)
    assert [f.update(0.9) for _ in range(2)] == [False, False]
    assert f.update(0.9) is True


def test_single_spike_does_not_fire():
    f = RiskFilter(trigger=0.7, required=3, cooldown=5)
    assert not any(f.update(p) for p in [0.9, 0.1, 0.9, 0.1, 0.9, 0.1])


def test_streak_decays_by_one_and_never_below_zero():
    f = RiskFilter(trigger=0.7, required=5, cooldown=5)
    for _ in range(3):
        f.update(0.9)
    assert f.streak == 3
    f.update(0.1)
    assert f.streak == 2
    for _ in range(10):
        f.update(0.1)
    assert f.streak == 0


def test_cooldown_suppresses_and_freezes_streak():
    f = RiskFilter(trigger=0.7, required=2, cooldown=3)
    f.update(0.9)
    assert f.update(0.9) is True
    assert f.cooldown_left == 3 and f.streak == 0
    # Cooldown frames return early: no alert and the streak stays frozen at 0.
    for _ in range(3):
        assert f.update(0.9) is False
        assert f.streak == 0
    assert f.cooldown_left == 0
    # Filter is live again and needs a fresh streak.
    assert f.update(0.9) is False
    assert f.update(0.9) is True


def test_trigger_is_inclusive():
    f = RiskFilter(trigger=0.7, required=1, cooldown=0)
    assert f.update(0.7) is True


def test_reset_clears_state():
    f = RiskFilter(trigger=0.7, required=2, cooldown=9)
    f.update(0.9); f.update(0.9)
    f.reset()
    assert f.streak == 0 and f.cooldown_left == 0
