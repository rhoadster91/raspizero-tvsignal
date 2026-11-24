"""Decision logic. The first block is ported unchanged from the proof of concept."""

import pytest

from tvsignal.model import Follower, Observation

ON = Observation(True, "On", "On")
DARK = Observation(True, "On", "Off")          # e.g. background wake, panel off
STANDBY = Observation(True, "Standby", "Off")
GONE = Observation(False)


def test_active_rules():
    assert ON.active
    assert not DARK.active
    assert not STANDBY.active
    assert not GONE.active
    assert Observation(True, "On", None).active  # TV without screenstate support


def test_on_after_delay_off_after_delay():
    f = Follower(on_delay=10, off_delay=60, plug_on=False)
    assert f.update(ON, 0) is None
    assert f.update(ON, 9) is None
    assert f.update(ON, 10) is True
    assert f.update(ON, 20) is None          # already on
    assert f.update(STANDBY, 30) is None
    assert f.update(STANDBY, 89) is None
    assert f.update(STANDBY, 90) is False


def test_default_switches_on_at_first_active_poll():
    f = Follower(plug_on=False)
    assert f.update(ON, 0) is True


def test_owner_examples():
    # On-Off-On-Off-On within ~3 s: plug goes ON at once and stays ON.
    f = Follower(plug_on=False)
    seq = [(0, ON), (0.5, STANDBY), (1, ON), (2, GONE), (3, ON)]
    actions = [f.update(o, t) for t, o in seq]
    assert actions == [True, None, None, None, None] and f.plug_on is True
    # On for an hour, then Off: plug goes OFF 10 s after the TV does.
    f = Follower(plug_on=False)
    assert f.update(ON, 0) is True
    assert f.update(ON, 3600) is None
    assert f.update(STANDBY, 3600) is None
    assert f.update(STANDBY, 3609) is None
    assert f.update(STANDBY, 3610) is False


def test_brief_blip_is_ignored():
    f = Follower(on_delay=10, off_delay=60, plug_on=True)
    f.update(ON, 0)
    assert f.update(GONE, 5) is None          # one failed poll
    assert f.update(ON, 10) is None           # back before off_delay
    assert f.update(ON, 100) is None          # timer reset, stays on


def test_background_wake_never_switches_on():
    f = Follower(on_delay=10, off_delay=60, plug_on=False)
    for t in range(0, 600, 5):
        assert f.update(DARK, t) is None


def test_unknown_initial_state_converges():
    f = Follower(on_delay=10, off_delay=60, plug_on=None)
    f.update(STANDBY, 0)
    assert f.update(STANDBY, 60) is False


# --------------------------------------------------------------------------- every observed TV state

@pytest.mark.parametrize(
    "obs, active, when",
    [
        (Observation(True, "On", "On"), True, "watching"),
        (Observation(True, "On", "Off"), False, "~2 s while waking, before the picture"),
        (Observation(True, "Standby", "On"), False, "~2.5 s while waking, on another occasion"),
        (Observation(True, "Standby", "Off"), False, "just after power-off; Ambilight fading"),
        (Observation(False), False, "network asleep"),
        (Observation(True, "StandbyKeep", "Off"), False, "network wake, no picture"),
        (Observation(True, None, None), False, "powerstate endpoint returned nothing"),
        (Observation(True, "On", None), True, "no screenstate support: fall back to powerstate"),
    ],
)
def test_observed_states(obs, active, when):
    assert obs.active is active, when


def test_any_active_reading_resets_off_timer():
    f = Follower(plug_on=True)
    f.update(STANDBY, 0)
    assert f.update(STANDBY, 9) is None
    assert f.update(ON, 9.5) is None          # resets
    assert f.update(STANDBY, 10) is None
    assert f.update(STANDBY, 19) is None
    assert f.update(STANDBY, 20) is False


def test_unknown_initial_state_and_active_switches_on_at_once():
    f = Follower(plug_on=None)
    assert f.update(ON, 0) is True
