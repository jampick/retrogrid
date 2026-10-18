"""The console with and without the fantasy layer (RETROGRID_LEAGUE)."""
from __future__ import annotations

import pytest

from conftest import needs_slate


def _engine(monkeypatch, league: str):
    monkeypatch.setenv("RETROGRID_LEAGUE", league)
    monkeypatch.setenv("RETROGRID_START", "5400")
    from retrogrid import console
    e = console.Engine()
    e.rebuild()
    return console, e


@needs_slate
def test_nfl_mode_needs_no_league(monkeypatch):
    console, e = _engine(monkeypatch, "off")
    assert e.league is None and e.hub is None and not e.boards
    s = console.Session(ws=None, favs={"KC"})
    s.focus = e.pick_focus(s)
    f = e.state_frame(s)
    assert f["mode"] == "nfl" and not f["ffb_available"] and f["lineup"] == [] and f["viewer"] is None
    assert f["threats"] and all(t["delta"] is None and t["team"] for t in f["threats"])
    away, home = s.focus.split("_")[2:]
    assert f["matchup"]["you"]["name"].startswith(away) and f["matchup"]["them"]["name"].startswith(home)
    assert isinstance(f["matchup"]["you"]["points"], int)
    assert f["audio"]["game_id"] == s.focus and f["chatter"]           # the stub crowd has been talking
    g = f["ghosts"]["you"]
    assert g and e.directory.player(g["player_id"]).team == away
    play = e.last_by_game[s.focus]
    pf = e.play_frame(s, play, focus=True, alert=False)
    assert pf["deltas"] == [] and {a["side"] for a in pf["actors"]} <= {"you", "them", None}


@needs_slate
def test_followed_team_takes_focus(monkeypatch):
    console, e = _engine(monkeypatch, "off")
    team = next(t for t, g in e.team_game.items() if g in e.last_by_game)
    s = console.Session(ws=None, favs={team})
    assert e.pick_focus(s) == e.team_game[team]
    assert any(f["fav"] for f in e.state_frame(s)["feeds"])


@needs_slate
def test_ffb_layer_is_opt_in_per_session(monkeypatch):
    console, e = _engine(monkeypatch, "stub")
    on = console.Session(ws=None, viewer=e.default_viewer, ffb=True)
    off = console.Session(ws=None, viewer=e.default_viewer, ffb=False)
    for s in (on, off):
        s.focus = e.pick_focus(s)
    f_on, f_off = e.state_frame(on), e.state_frame(off)
    assert f_on["mode"] == "ffb" and f_on["lineup"] and f_on["viewers"] and f_on["ffb_available"]
    assert f_off["mode"] == "nfl" and f_off["lineup"] == [] and f_off["ffb_available"]
    assert f_on["chatter"] is not None and f_on["audio"]                  # the base layer stays underneath


@needs_slate
def test_ffb_ghosts_follow_the_focused_game(monkeypatch):
    console, e = _engine(monkeypatch, "stub")
    s = console.Session(ws=None, viewer=e.default_viewer, ffb=True)
    team = lambda pid: e.directory.player(pid).team if e.directory.player(pid) else pid.removeprefix("DEF-")
    mine = [sl.player_id for sl in e.rosters[s.viewer].starters()]
    s.focus = e.team_game[team(mine[0])]
    g = e.state_frame(s)["ghosts"]["you"]
    assert g and e.team_game.get(team(g["player_id"])) == s.focus          # one of mine in this game is the hologram
    empty = next((gid for gid in set(e.team_game.values()) if all(e.team_game.get(team(p)) != gid for p in mine)), None)
    if empty:
        s.focus = empty
        assert e.state_frame(s)["ghosts"]["you"]["player_id"] in mine     # none of mine here: hottest anywhere


@needs_slate
def test_ffb_frame_says_who_has_played_and_shows_the_league(monkeypatch):
    console, e = _engine(monkeypatch, "stub")
    s = console.Session(ws=None, viewer=e.default_viewer, ffb=True)
    f = e.state_frame(s)
    states = {c["game"]["state"] for r in f["lineup"] for c in (r["you"], r["them"])}
    assert states <= {"pre", "live", "done", "none"} and states & {"live", "done"}      # 5400 s in: early games on
    pre = [c for r in f["lineup"] for c in (r["you"], r["them"]) if c["game"]["state"] == "pre"]
    assert all(c["game"]["at"].startswith(e.slate.date[:4]) for c in pre)           # an ISO kickoff for the client to localise
    slate = set(e.team_game.values())
    on_slate = [c for r in f["lineup"] for c in (r["you"], r["them"]) if c["game"]["state"] != "none"]
    assert on_slate and all(c["game"]["game_id"] in slate for c in on_slate)      # a click on the row focuses that game
    you = f["matchup"]["you"]
    assert you["left"] == sum(r["you"]["game"]["state"] in ("pre", "live") for r in f["lineup"])
    assert you["proj"] is None and you["win"] is None                                  # the stub league has no Yahoo outlook
    board = f["league_board"]
    assert len(board) == len(e.league.matchups(e.week)) and board[0]["mine"] and board[0]["a"]["team_key"] == s.viewer
    assert board[0]["a"]["points"] == f["matchup"]["you"]["points"]
