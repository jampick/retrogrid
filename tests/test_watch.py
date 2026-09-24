"""watch.decide: which engine should be up, given the day and what ESPN says."""
from __future__ import annotations

from types import SimpleNamespace as NS

from retrogrid.watch import decide, game_ids_on


def _engine(live: bool, day: str = "2026-09-20", ids=("a", "b"), now: float = 0.0, duration: float = 100.0):
    return NS(live=live, slate=NS(date=day, games=[NS(id=i) for i in ids]), clock=NS(now=lambda: now, duration=duration))


def test_sim_and_hold_never_move():
    assert decide("sim", _engine(False), "2026-09-24", {"x"}) is None
    assert decide("hold", _engine(True), "2026-09-24", {"x"}) is None


def test_no_answer_from_espn_keeps_what_is_up():
    assert decide("auto", _engine(False), "2026-09-24", None) is None
    assert decide("live", _engine(True), "2026-09-24", None) is None


def test_the_sim_goes_live_when_today_has_games():
    assert decide("auto", _engine(False), "2026-09-24", {"x"}) == "live"
    assert decide("auto", _engine(False), "2026-09-23", set()) is None


def test_todays_slate_only_rebuilds_on_a_changed_schedule():
    e = _engine(True, "2026-09-24", ids=("x",))
    assert decide("auto", e, "2026-09-24", {"x"}) is None
    assert decide("auto", e, "2026-09-24", {"x", "y"}) == "live"
    assert decide("auto", e, "2026-09-24", set()) is None          # ESPN dropped the game? not our call


def test_a_finished_day_rolls_forward_and_a_running_one_waits():
    sunday = _engine(True, "2026-09-20", now=50.0, duration=100.0)     # SNF still on the axis
    assert decide("auto", sunday, "2026-09-21", {"mnf"}) is None
    sunday = _engine(True, "2026-09-20", now=100.0, duration=100.0)
    assert decide("auto", sunday, "2026-09-21", {"mnf"}) == "live"
    assert decide("auto", sunday, "2026-09-22", set()) == "sim"     # Tuesday: nothing on
    assert decide("live", sunday, "2026-09-22", set()) is None      # `retrogrid live` never falls back to the sim


def test_thursday_night_is_thursday_in_eastern_time():
    board = {"season": {"year": 2026}, "week": {"number": 3}, "events": [{
        "id": "401872948", "date": "2026-09-25T00:15Z", "competitions": [{"competitors": [
            {"homeAway": "home", "team": {"id": "9", "abbreviation": "GB"}, "score": "0"},
            {"homeAway": "away", "team": {"id": "1", "abbreviation": "ATL"}, "score": "0"}],
            "status": {"type": {"name": "STATUS_SCHEDULED"}, "period": 0, "displayClock": "0:00"}}]}]}
    assert game_ids_on(board, "2026-09-24") == {"2026_03_ATL_GB"}
    assert game_ids_on(board, "2026-09-25") == set()
