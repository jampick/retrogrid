from __future__ import annotations

from retrogrid.models import PlayRow
from retrogrid.scoring.action import ALERT_THRESHOLD, ActionBoard, classify

G = "2025_15_KC_BUF"          # away KC, home BUF


def play(n: int, t: float, **kw) -> PlayRow:
    base = dict(play_id=f"{G}:{n}", game_id=G, seq=n, sim_time=t, quarter=1, clock="10:00", down=1, ydstogo=10,
                yardline_100=50, posteam="KC", defteam="BUF", desc="", play_type="run")
    return PlayRow(**{**base, **kw})


def test_ordinary_downs_stay_off_the_board():
    b = ActionBoard()
    assert b.ingest(play(1, 10, yards_gained=4)) is None
    assert b.ingest(play(2, 20, play_type="pass", complete=True, yards_gained=9, first_down=True)) is None
    assert classify(play(3, 30, play_type="no_play", touchdown=True)) is None
    assert not b.events


def test_tags_and_who_they_favour():
    assert classify(play(1, 0, play_type="pass", complete=True, touchdown=True, td_team="KC", yards_gained=12))[:2] == ("TD", "KC")
    assert classify(play(2, 0, play_type="pass", interception=True))[:2] == ("INT", "BUF")
    assert classify(play(3, 0, play_type="pass", interception=True, touchdown=True, td_team="BUF"))[3].endswith("PICK-SIX")
    assert classify(play(4, 0, down=4, ydstogo=2, yards_gained=1))[:2] == ("STOP", "BUF")
    assert classify(play(5, 0, down=4, ydstogo=2, yards_gained=3, first_down=True))[:2] == ("4TH", "KC")
    assert classify(play(6, 0, play_type="field_goal", field_goal_result="missed", kick_distance=48))[:2] == ("MISS", "BUF")
    assert classify(play(7, 0, yards_gained=31))[0] == "BIG"


def test_late_and_close_outweighs_early():
    b = ActionBoard()
    early = b.ingest(play(1, 100, play_type="field_goal", field_goal_result="made", kick_distance=30, away_score=3))
    late = b.ingest(play(2, 9000, quarter=4, play_type="field_goal", field_goal_result="made", kick_distance=30, away_score=6, home_score=7))
    assert late.weight > early.weight


def test_lead_change_is_taking_the_lead_not_opening_the_scoring():
    b = ActionBoard()
    first = b.ingest(play(1, 10, touchdown=True, td_team="KC", away_score=7))
    answer = b.ingest(play(2, 20, posteam="BUF", defteam="KC", touchdown=True, td_team="BUF", away_score=7, home_score=7))
    ahead = b.ingest(play(3, 30, posteam="BUF", defteam="KC", play_type="field_goal", field_goal_result="made", kick_distance=33, away_score=7, home_score=10))
    assert not first.lead_change and not answer.lead_change
    assert not ahead.lead_change                                   # from level, not from behind
    back = b.ingest(play(4, 40, touchdown=True, td_team="KC", away_score=14, home_score=10))
    assert back.lead_change


def test_favourites_boost_rank_and_alerts():
    b = ActionBoard()
    other = "2025_15_DAL_PHI"
    fg = b.ingest(play(1, 100, play_type="field_goal", field_goal_result="made", kick_distance=52))
    td = b.ingest(PlayRow(**{**play(2, 100, touchdown=True, td_team="DAL").__dict__, "game_id": other, "play_id": f"{other}:2", "posteam": "DAL", "defteam": "PHI"}))
    assert b.top(2, 100)[0] is td
    assert b.should_alert(td) and not b.should_alert(fg)
    big = b.ingest(play(3, 100, yards_gained=40))
    assert big.weight < ALERT_THRESHOLD and not b.should_alert(big) and b.should_alert(big, {"KC"})
    assert b.heat(100)[G] > 0 and b.heat(50) == {}


def test_recency_decay():
    b = ActionBoard()
    old = b.ingest(play(1, 0, touchdown=True, td_team="KC"))
    new = b.ingest(play(2, 3000, play_type="pass", interception=True))
    assert b.top(1, 3000)[0] is new and b.score(old, 0) > b.score(new, 3000) * 0.99
