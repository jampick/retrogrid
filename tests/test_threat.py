"""Threat ranking (DESIGN §9 Threat score; §4 alerts and FEEDS markers)."""
from __future__ import annotations

import pytest

from retrogrid.models import Game, Matchup, Player, PlayRow, Roster, RosterSlot, StatDelta
from retrogrid.scoring.engine import ScoringState
from retrogrid.scoring.matchup import MatchupBoard
from retrogrid.scoring.threat import (HEADLINE_MAX, LeagueIndex, ThreatBoard, ThreatHub,
                                     headline)

_seq = iter(range(1, 10_000))


def play(play_type: str, t: float, game: str = "2025_03_KC_BUF", **kw) -> PlayRow:
    n = next(_seq)
    base = dict(play_id=f"{game}:{n}", game_id=game, seq=n, sim_time=t, quarter=1,
                clock="10:00", down=1, ydstogo=10, yardline_100=50, posteam="BUF",
                defteam="KC", desc="", play_type=play_type)
    base.update(kw)
    return PlayRow(**base)


def roster(key: str, *starters: str, bench: tuple[str, ...] = ()) -> Roster:
    slots = [RosterSlot("RB", p) for p in starters] + [RosterSlot("BN", p) for p in bench]
    return Roster(team_key=key, week=3, slots=slots)


class Directory:
    PLAYERS = {
        "allen": Player("allen", "Josh Allen", "J.Allen", "QB", "BUF"),
        "stb": Player("stb", "Amon-Ra St. Brown", "A.St. Brown", "WR", "DET"),
        "long": Player("long", "X", "M.Valdes-Scantlingworthington", "WR", "NO"),
    }

    def player(self, player_id):
        return self.PLAYERS.get(player_id)

    def by_short(self, short, team=None):
        return None


@pytest.fixture
def index() -> LeagueIndex:
    return LeagueIndex(
        [roster("t01", "kelce", "cook", bench=("mybench",)), roster("t02", "allen", "hall"),
         roster("t03", "chase"), roster("t04", "henry")],
        [Matchup(3, "t01", "t02"), Matchup(3, "t03", "t04")])


def td_run(player: str, t: float, yards: int = 40, **kw) -> tuple[PlayRow, list[StatDelta]]:
    p = play("run", t, yards_gained=yards, rusher_id=player, touchdown=True,
             td_team="BUF", td_player_id=player, **kw)
    return p, ScoringState().apply(p)


def run(player: str, t: float, yards: int, **kw) -> tuple[PlayRow, list[StatDelta]]:
    p = play("run", t, yards_gained=yards, rusher_id=player, **kw)
    return p, ScoringState().apply(p)


def only(deltas: list[StatDelta], player: str) -> list[StatDelta]:
    return [d for d in deltas if d.player_id == player]


def test_sides_kinds_and_relevance_zero_excluded(index):
    board = ThreatBoard("t01", index)
    p, d = run("kelce", 10, 20)
    assert [(e.side, e.kind) for e in board.ingest(p, only(d, "kelce"), 10)] == [("you", "help")]
    p, d = run("allen", 11, 20)
    assert [(e.side, e.kind) for e in board.ingest(p, only(d, "allen"), 11)] == [("them", "hurt")]
    p, d = run("allen", 12, 5, fumbler_id="allen", fumble_lost=True)
    assert [(e.side, e.kind) for e in board.ingest(p, only(d, "allen"), 12)] == [("them", "help")]
    p, d = run("kelce", 13, -4)
    assert [e.kind for e in board.ingest(p, only(d, "kelce"), 13)] == ["hurt"]
    p, d = run("chase", 14, 30)
    assert [e.side for e in board.ingest(p, only(d, "chase"), 14)] == ["league"]
    p, d = run("mybench", 15, 30)                          # my bench: rostered, not a starter
    assert [e.side for e in board.ingest(p, only(d, "mybench"), 15)] == ["league"]

    p, d = td_run("freeagent", 16)
    assert board.ingest(p, d, 16) == []                    # relevance 0 (and unrostered DEFs)
    zero = play("pass", 17, passer_id="allen", receiver_id="hall")
    assert board.ingest(zero, ScoringState().apply(zero), 17) == []   # 0-pt deltas

    assert all(e.side != "league" for e in board.top(10, 20))
    assert {e.player_id for e in board.top(10, 20, scope="league")} == {
        "kelce", "allen", "chase", "mybench"}
    assert all(e.player_id != "freeagent" for e in board.top(99, 20, scope="league"))


def test_big_opponent_td_outranks_older_small_gain(index):
    board = ThreatBoard("t01", index)
    board.ingest(*run("kelce", 0, 9), 0)
    board.ingest(*td_run("hall", 900), 900)
    top = board.top(5, 1000)
    assert [e.player_id for e in top] == ["hall", "kelce"]
    assert top[0].kind == "hurt" and top[0].delta_points == pytest.approx(10)
    assert top[0].headline == "HALL 40-YD TD RUN"


def test_recency_beats_size_eventually(index):
    board = ThreatBoard("t01", index, half_life=600)
    board.ingest(*td_run("hall", 0), 0)                    # 10 pts, an hour old
    board.ingest(*run("kelce", 3600, 30), 3600)            # 3 pts, fresh
    assert [e.player_id for e in board.top(2, 3600)] == ["kelce", "hall"]


def test_matchup_player_outranks_equal_league_play(index):
    board = ThreatBoard("t01", index)
    board.ingest(*td_run("chase", 100), 100)
    board.ingest(*td_run("hall", 100), 100)
    assert [e.player_id for e in board.top(2, 100, scope="league")] == ["hall", "chase"]


def test_lead_change_outranks_everything(index):
    board = ThreatBoard("t01", index)
    p, d = run("hall", 0, 12)
    events = board.ingest(p, d, 0, lead_change=True)
    assert [e.lead_change for e in events] == [True]
    board.ingest(*td_run("allen", 1200, yards=80), 1200)   # 14-pt monster, 20 min later
    top = board.top(2, 1200)
    assert [e.player_id for e in top] == ["hall", "allen"]
    assert board.should_alert(top[0])                      # only 1.2 pts, but a lead change


def test_lead_change_pinned_on_biggest_matchup_event(index):
    board = ThreatBoard("t01", index)
    p = play("pass", 5, complete=True, yards_gained=30, touchdown=True, td_team="BUF",
             passer_id="allen", receiver_id="hall", td_player_id="hall")
    events = board.ingest(p, ScoringState().apply(p), 5, lead_change=True)
    assert {e.player_id: e.lead_change for e in events} == {"allen": False, "hall": True}


def test_decay_is_monotonic_and_halves(index):
    board = ThreatBoard("t01", index, half_life=600)
    (event,) = [e for e in board.ingest(*td_run("hall", 0), 0)]
    scores = [board.score(event, t) for t in range(0, 7200, 60)]
    assert all(a > b for a, b in zip(scores, scores[1:]))
    assert scores[0] == pytest.approx(10 * 2.0)
    assert board.score(event, 600) == pytest.approx(10.0)
    assert board.score(event, -50) == pytest.approx(20.0)  # never amplifies the future

    event.lead_change = True
    lead = [board.score(event, t) for t in range(0, 7200, 60)]
    assert all(a > b for a, b in zip(lead, lead[1:]))
    assert all(l > s for l, s in zip(lead, scores))


def test_should_alert_threshold(index):
    board = ThreatBoard("t01", index)
    (small,) = board.ingest(*run("hall", 0, 39), 0)
    (edge,) = board.ingest(*run("hall", 1, 40), 1)
    (league,) = board.ingest(*td_run("chase", 2), 2)
    assert not board.should_alert(small)
    assert board.should_alert(edge)
    assert not board.should_alert(league)


def test_per_game_status(index):
    board = ThreatBoard("t01", index)
    board.ingest(*td_run("hall", 0, game="2025_03_NYJ_MIA"), 0)
    board.ingest(*run("kelce", 0, 30, game="2025_03_NYJ_MIA"), 0)
    board.ingest(*td_run("kelce", 0), 0)
    board.ingest(*run("cook", 0, 2, game="2025_03_DAL_PHI"), 0)
    board.ingest(*td_run("chase", 0, game="2025_03_CIN_MIN"), 0)   # league-only game
    assert board.per_game_status(0) == {
        "2025_03_NYJ_MIA": "hurt", "2025_03_KC_BUF": "help", "2025_03_DAL_PHI": None}
    # Hours later everything has decayed to a wash.
    assert set(board.per_game_status(36_000).values()) == {None}


def test_headlines():
    d = Directory()
    p = play("pass", 0, complete=True, yards_gained=22, touchdown=True, td_team="BUF",
             passer_id="allen", receiver_id="stb", td_player_id="stb", home_score=6)
    state = ScoringState()
    opened = state.open_game(Game(id=p.game_id, home="BUF", away="KC", kickoff=0))
    assert headline(p, opened[0], d) == "BUF DEF GAME OPEN"
    lines = {x.player_id: headline(p, x, d) for x in state.apply(p)}
    assert lines["allen"] == "ALLEN 22-YD TD PASS"
    assert lines["stb"] == "ST. BROWN 22-YD TD CATCH"
    assert lines["DEF-KC"] == "KC DEF ALLOWS 6"

    six = play("pass", 0, interception=True, touchdown=True, td_team="KC", passer_id="allen")
    lines = {x.player_id: headline(six, x, d) for x in ScoringState().apply(six)}
    assert lines["allen"] == "ALLEN INTERCEPTED"
    assert lines["DEF-KC"] == "KC DEF PICK-SIX"

    fg = play("field_goal", 0, field_goal_result="made", kick_distance=52, kicker_id="nobody")
    assert headline(fg, ScoringState().apply(fg)[0]) == "NOBODY 52-YD FG"

    long = play("pass", 0, complete=True, yards_gained=75, touchdown=True, td_team="BUF",
                passer_id="allen", receiver_id="long")
    text = headline(long, StatDelta("long", 14.0, {"rec": 1, "rec_td": 1}), d)
    assert len(text) <= HEADLINE_MAX and text.endswith("75-YD TD CATCH")
    assert text == text.upper()


def test_hub_feeds_every_viewer_from_one_stream(index):
    state = ScoringState()
    rosters = {"t01": roster("t01", "kelce", "cook"), "t02": roster("t02", "allen", "hall")}
    hub = ThreatHub(index, half_life=300.0)
    for key in ("t01", "t02", "t03"):
        hub.board(key)
    mb = MatchupBoard(rosters["t01"], rosters["t02"], state)

    p = play("run", 10, yards_gained=40, rusher_id="hall", touchdown=True, td_team="BUF",
             td_player_id="hall", home_score=6)
    out = hub.ingest(p, state.apply(p), 10)
    assert out["t01"][0].kind == "hurt" and out["t01"][0].side == "them"
    assert out["t02"][0].kind == "help" and out["t02"][0].side == "you"
    assert out["t03"][0].side == "league"
    assert out["t01"][0].headline == out["t03"][0].headline == "HALL 40-YD TD RUN"
    assert hub.board("t01").half_life == 300.0
    assert mb.update() is None                             # first lead

    p = play("run", 20, yards_gained=60, rusher_id="kelce", touchdown=True, td_team="BUF",
             td_player_id="kelce", home_score=12)
    deltas = state.apply(p)
    flipped = {"t01", "t02"} if mb.update() else set()
    out = hub.ingest(p, deltas, 20, lead_changes=flipped)
    assert out["t01"][0].lead_change and out["t02"][0].lead_change
    assert not out["t03"][0].lead_change
    assert hub.board("t01").top(1, 20)[0].player_id == "kelce"

    free = play("run", 30, yards_gained=5, rusher_id="freeagent")
    assert hub.ingest(free, state.apply(free), 30) == {}
    hub.reset()
    assert hub.board("t01").top(5, 30) == []
