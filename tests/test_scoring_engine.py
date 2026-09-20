"""Scoring engine (DESIGN §9 Points). Plays are built by hand; no data files."""
from __future__ import annotations

import pytest

from retroffb.models import Game, PlayRow, Roster, RosterSlot
from retroffb.scoring.engine import DEFAULT_RULES, ScoringState, pa_tier, round_points, score_play
from retroffb.scoring.matchup import LeagueBoard, MatchupBoard
from retroffb.models import Matchup

GAME = "2025_03_KC_BUF"          # KC away @ BUF home
_seq = iter(range(1, 10_000))


def play(play_type: str, **kw) -> PlayRow:
    n = next(_seq)
    base = dict(play_id=f"{GAME}:{n}", game_id=GAME, seq=n, sim_time=float(n), quarter=1,
                clock="10:00", down=1, ydstogo=10, yardline_100=50, posteam="BUF",
                defteam="KC", desc="", play_type=play_type)
    base.update(kw)
    return PlayRow(**base)


def pts(deltas) -> dict[str, float]:
    return {d.player_id: pytest.approx(d.points) for d in deltas}


def test_td_pass_scores_all_three_parties():
    p = play("pass", complete=True, yards_gained=22, touchdown=True, td_team="BUF",
             passer_id="allen", receiver_id="kincaid", td_player_id="kincaid")
    got = {d.player_id: d for d in score_play(p, DEFAULT_RULES)}
    assert got["allen"].points == pytest.approx(22 * 0.04 + 4)
    assert got["kincaid"].points == pytest.approx(0.5 + 2.2 + 6)
    assert got["kincaid"].stats == {"tgt": 1, "rec": 1, "rec_yd": 22, "rec_td": 1}
    assert "DEF-KC" not in got


def test_incompletion_is_a_target_worth_nothing():
    d = score_play(play("pass", passer_id="allen", receiver_id="kincaid"))
    assert pts(d) == {"allen": 0, "kincaid": 0}


def test_sack_gives_def_one_and_qb_nothing():
    p = play("pass", sack=True, yards_gained=-8, passer_id="allen")
    assert pts(score_play(p)) == {"DEF-KC": 1}


def test_rush_td_and_scramble_yards():
    p = play("run", yards_gained=12, qb_scramble=True, rusher_id="allen",
             touchdown=True, td_team="BUF", td_player_id="allen")
    assert pts(score_play(p)) == {"allen": 1.2 + 6}
    assert pts(score_play(play("run", yards_gained=-3, rusher_id="cook"))) == {"cook": -0.3}


def test_pick_six():
    state = ScoringState()
    state.open_game(Game(id=GAME, home="BUF", away="KC", kickoff=0))
    p = play("pass", interception=True, touchdown=True, td_team="KC", passer_id="allen",
             receiver_id="kincaid", interceptor_id="mcduffie", td_player_id="mcduffie",
             home_score=0, away_score=6)
    got = pts(state.apply(p))
    assert got["allen"] == -1
    assert got["DEF-KC"] == 2 + 6
    assert got["DEF-BUF"] == 7 - 10          # victim's DEF: tier 0 → 1-6
    assert got["kincaid"] == 0
    assert "mcduffie" not in got             # no IDP
    assert state.points("DEF-KC") == pytest.approx(18)
    assert state.points("DEF-BUF") == pytest.approx(7)


@pytest.mark.parametrize("dist,expected", [(39, 3), (40, 4), (49, 4), (50, 5), (61, 5), (19, 3)])
def test_fg_tiers(dist, expected):
    p = play("field_goal", field_goal_result="made", kick_distance=dist, kicker_id="bass")
    assert pts(score_play(p)) == {"bass": expected}


def test_fg_distance_falls_back_to_yardline():
    p = play("field_goal", field_goal_result="made", yardline_100=23, kicker_id="bass")
    assert pts(score_play(p)) == {"bass": 4}


def test_missed_and_blocked_fg():
    miss = play("field_goal", field_goal_result="missed", kick_distance=45, kicker_id="bass")
    assert pts(score_play(miss)) == {"bass": 0}
    block = play("field_goal", field_goal_result="blocked", kick_distance=45, kicker_id="bass")
    assert pts(score_play(block)) == {"bass": 0, "DEF-KC": 2}


def test_extra_point():
    assert pts(score_play(play("extra_point", extra_point_result="good", kicker_id="bass"))) == {"bass": 1}
    assert pts(score_play(play("extra_point", extra_point_result="failed", kicker_id="bass"))) == {"bass": 0}


def test_two_point_pass_pays_both_and_no_yardage():
    p = play("pass", two_point="success", complete=True, yards_gained=2,
             passer_id="allen", receiver_id="kincaid")
    assert pts(score_play(p)) == {"allen": 2, "kincaid": 2}
    run = play("run", two_point="success", yards_gained=2, rusher_id="cook")
    assert pts(score_play(run)) == {"cook": 2}
    fail = play("run", two_point="failure", yards_gained=1, rusher_id="cook")
    assert score_play(fail) == []


def test_fumble_lost():
    p = play("run", yards_gained=4, rusher_id="cook", fumbler_id="cook", fumble_lost=True)
    assert pts(score_play(p)) == {"cook": 0.4 - 2, "DEF-KC": 2}


def test_scoop_six_after_catch():
    p = play("pass", complete=True, yards_gained=10, passer_id="allen", receiver_id="kincaid",
             fumbler_id="kincaid", fumble_lost=True, touchdown=True, td_team="KC")
    assert pts(score_play(p)) == {"allen": 0.4, "kincaid": 0.5 + 1 - 2, "DEF-KC": 2 + 6}


def test_safety_and_sack():
    p = play("pass", sack=True, safety=True, yards_gained=-5, passer_id="allen")
    assert pts(score_play(p)) == {"DEF-KC": 3}


def test_kick_and_punt_return_tds():
    ko = play("kickoff", posteam="BUF", defteam="KC", touchdown=True, td_team="BUF",
              returner_id="ret", td_player_id="ret")
    assert pts(score_play(ko)) == {"ret": 6, "DEF-BUF": 6}
    punt = play("punt", posteam="BUF", defteam="KC", touchdown=True, td_team="KC",
                returner_id="kcret", td_player_id="kcret")
    assert pts(score_play(punt)) == {"kcret": 6, "DEF-KC": 6}


def test_no_play_scores_nothing():
    p = play("no_play", penalty=True, complete=True, yards_gained=40, touchdown=True,
             td_team="BUF", passer_id="allen", receiver_id="kincaid")
    assert score_play(p) == []
    wiped = play("pass", penalty=True, desc="(Shotgun) J.Allen pass ... PENALTY on BUF, NO PLAY.",
                 complete=True, yards_gained=40, passer_id="allen", receiver_id="kincaid")
    assert score_play(wiped) == []
    stands = play("pass", penalty=True, desc="PENALTY on KC, Roughing, 15 yards, enforced.",
                  complete=True, yards_gained=10, passer_id="allen", receiver_id="kincaid")
    assert pts(score_play(stands))["allen"] == 0.4


def test_points_allowed_tier_walk():
    state = ScoringState()
    opened = state.open_game(Game(id=GAME, home="BUF", away="KC", kickoff=0))
    assert pts(opened) == {"DEF-BUF": 10, "DEF-KC": 10}
    assert state.open_game(Game(id=GAME, home="BUF", away="KC", kickoff=0)) == []
    expected = {0: 10, 7: 4, 14: 1, 21: 0, 28: -1, 35: -4}
    for score, total in expected.items():
        if score:
            d = state.apply(play("extra_point", posteam="KC", defteam="BUF", away_score=score))
            assert [x.player_id for x in d] == ["DEF-BUF"]
        assert state.points("DEF-BUF") == pytest.approx(total), score
    assert state.points("DEF-KC") == pytest.approx(10)
    assert state.statline("DEF-BUF")["pts_allowed"] == 35
    assert [pa_tier(n) for n in (0, 1, 6, 7, 13, 14, 20, 21, 27, 28, 34, 35, 60)] == [
        "def_pa_0", "def_pa_1_6", "def_pa_1_6", "def_pa_7_13", "def_pa_7_13", "def_pa_14_20",
        "def_pa_14_20", "def_pa_21_27", "def_pa_21_27", "def_pa_28_34", "def_pa_28_34",
        "def_pa_35", "def_pa_35"]


def test_lazy_open_parses_home_away_from_game_id():
    state = ScoringState()
    d = pts(state.apply(play("field_goal", field_goal_result="made", kick_distance=30,
                             kicker_id="bass", home_score=3)))
    assert d == {"bass": 3, "DEF-BUF": 10, "DEF-KC": 7}
    assert state.points("DEF-BUF") == pytest.approx(10)
    assert state.points("DEF-KC") == pytest.approx(7)


def test_state_accumulates_is_idempotent_and_rebuilds():
    game = Game(id=GAME, home="BUF", away="KC", kickoff=0)
    plays = [
        play("pass", complete=True, yards_gained=9, passer_id="allen", receiver_id="kincaid"),
        play("pass", complete=True, yards_gained=13, passer_id="allen", receiver_id="kincaid"),
        play("run", yards_gained=7, rusher_id="allen"),
    ]
    state = ScoringState()
    state.rebuild([game], plays)
    assert state.apply(plays[0]) == []                     # re-poll is a no-op
    assert state.points("allen") == pytest.approx(22 * 0.04 + 0.7)
    assert state.statline("kincaid") == {"tgt": 2, "rec": 2, "rec_yd": 22}
    assert state.points("nobody") == 0.0
    before = state.all_points()
    state.rebuild([game], plays[:2])                       # seek backwards
    assert state.points("allen") == pytest.approx(0.88)
    state.rebuild([game], plays)
    assert state.all_points() == before


def test_no_rounding_in_accumulation():
    state = ScoringState()
    for _ in range(3):
        state.apply(play("pass", complete=True, yards_gained=1, passer_id="allen", receiver_id="x"))
    assert state.points("allen") == pytest.approx(0.12)
    assert state.points("allen") != round(0.04, 1) * 3    # 0.04s were not rounded away
    assert round_points(state.points("allen")) == 0.12
    assert round_points(-0.001) == 0.0


def test_custom_rules():
    rules = dict(DEFAULT_RULES, rec=1.0, pass_td=6)
    p = play("pass", complete=True, yards_gained=10, touchdown=True, td_team="BUF",
             passer_id="allen", receiver_id="kincaid")
    assert pts(score_play(p, rules)) == {"allen": 6.4, "kincaid": 8}


# -- matchup ---------------------------------------------------------------------

def roster(key: str, **slots: str) -> Roster:
    return Roster(team_key=key, week=3, slots=[
        RosterSlot(slot=s.rstrip("12"), player_id=pid) for s, pid in slots.items()])


def gain(state: ScoringState, player: str, yards: int) -> None:
    state.apply(play("run", yards_gained=yards, rusher_id=player))


def test_matchup_rows_totals_and_lead_changes():
    state = ScoringState()
    you = roster("t01", QB="allen", RB1="cook", RB2="hall", BN="benchguy")
    them = roster("t02", RB1="pacheco", QB="mahomes", RB2="henry")
    board = MatchupBoard(you, them, state)
    assert board.update() is None                          # 0–0

    gain(state, "cook", 30)
    gain(state, "benchguy", 99)                            # bench never counts
    assert board.update() is None                          # first lead isn't a change
    gain(state, "henry", 50)
    lc = board.update()
    assert lc is not None and (lc.leader, lc.previous) == ("them", "you")
    assert lc.delta == pytest.approx(-2.0)
    assert board.update() is None                          # no re-fire
    gain(state, "hall", 20)                                # tie at 5.0
    assert board.update() is None
    gain(state, "hall", 1)
    lc = board.update()
    assert lc is not None and lc.leader == "you"

    snap = board.snapshot()
    assert snap.you_total == pytest.approx(5.1) and snap.them_total == pytest.approx(5.0)
    rows = [r.to_dict() for r in snap.rows]
    assert [r["slot"] for r in rows[:3]] == ["QB", "RB", "RB"]
    assert rows[1] == {"slot": "RB", "you": {"player_id": "cook", "points": 3.0},
                       "them": {"player_id": "pacheco", "points": 0.0}, "losing": False}
    assert rows[2]["losing"] is True                       # hall 2.1 < henry 5.0
    assert rows[3]["you"]["player_id"] is None             # unfilled WR slot
    assert len(rows) == 9
    assert snap.to_dict()["delta"] == 0.1


def test_league_board_totals():
    state = ScoringState()
    rosters = {k: roster(k, RB1=f"rb-{k}") for k in ("t01", "t02", "t03", "t04")}
    gain(state, "rb-t02", 40)
    board = LeagueBoard([Matchup(3, "t01", "t02"), Matchup(3, "t03", "t04")], rosters, state)
    a, b = board.totals()
    assert (a.a_total, a.b_total, a.leader) == (0.0, pytest.approx(4.0), "t02")
    assert b.leader is None
    assert a.to_dict()["delta"] == -4.0
