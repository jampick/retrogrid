"""The highlight reel: the ranker, the week cache, the rundown and the show loop."""
from __future__ import annotations

import asyncio
import random

from retrogrid import paths
from retrogrid.models import PlayRow
from retrogrid.providers.directory import NflversePlayerDirectory
from retrogrid.providers.reel import load_week, load_weeks, make_week, save_week, week_from_slate
from retrogrid.providers.slate import load_slate
from retrogrid.scoring.reel import GAME_CAP, GAME_FLOOR, pick_week, score_game, watchable

G = "2025_15_KC_BUF"          # away KC, home BUF


def play(n: int, game: str = G, **kw) -> PlayRow:
    away, home = game.split("_")[2:]
    base = dict(play_id=f"{game}:{n}", game_id=game, seq=n, sim_time=0.0, quarter=1, clock="10:00", down=1, ydstogo=10,
                yardline_100=50, posteam=away, defteam=home, desc="", play_type="run")
    return PlayRow(**{**base, **kw})


def test_leverage_beats_yardage():
    """The point of ranking on WPA: a 9-yard catch on 4th & 8 late outranks a long TD in a blowout."""
    rows = [
        play(1, play_type="pass", complete=True, touchdown=True, td_team="KC", yards_gained=60, away_score=35, wpa=0.004),
        play(2, quarter=4, clock="1:10", down=4, ydstogo=8, play_type="pass", complete=True, yards_gained=9, first_down=True,
             away_score=35, wpa=0.41),
    ]
    picks = sorted(score_game(rows), key=lambda k: -k.score)
    assert [k.play.seq for k in picks] == [2, 1]
    assert picks[0].tag == "4TH" and picks[1].tag == "TD"          # and the blowout's 60-yarder still makes the list


def test_what_never_airs():
    assert not watchable(play(1, play_type="no_play", wpa=0.3))
    assert not watchable(play(2, play_type="qb_kneel"))
    assert not watchable(play(3, play_type="extra_point", extra_point_result="good"))
    assert not watchable(play(4, play_type="pass", penalty=True, desc="pass complete. PENALTY on BUF, holding, No Play."))
    assert watchable(play(5, play_type="extra_point", extra_point_result="blocked"))
    dull = [play(1, play_type="pass", wpa=-0.25, quarter=5),                        # a big swing with nothing to look at
            play(2, play_type="punt", kick_distance=56, wpa=0.24, quarter=5),
            play(3, yards_gained=3, wpa=0.01)]
    assert score_game(dull) == []


def test_clutch_is_an_ordinary_down_that_swung_the_game():
    k, = score_game([play(1, quarter=4, down=3, ydstogo=9, play_type="pass", complete=True, yards_gained=11, first_down=True, wpa=0.18)])
    assert (k.tag, k.team, k.headline) == ("CLUTCH", "KC", "11-YD CATCH")


def test_chip_shot_is_demoted_below_the_catch_that_set_it_up():
    rows = [play(1, quarter=4, play_type="pass", complete=True, yards_gained=22, first_down=True, wpa=0.36, away_score=20, home_score=20),
            play(2, quarter=4, play_type="field_goal", field_goal_result="made", kick_distance=31, wpa=0.50, away_score=23, home_score=20)]
    catch, kick = score_game(rows)
    assert catch.score > kick.score


def test_before_score_and_lead_change_come_from_the_previous_row():
    rows = [play(1, play_type="field_goal", field_goal_result="made", kick_distance=52, away_score=3, wpa=0.05),
            play(2, posteam="BUF", defteam="KC", play_type="pass", complete=True, touchdown=True, td_team="BUF", yards_gained=30,
                 away_score=3, home_score=6, wpa=0.12)]
    fg, td = score_game(rows)
    assert fg.before == (0, 0) and not fg.lead_change              # opening the scoring is not a lead change
    assert td.before == (0, 3) and td.lead_change


def test_no_wpa_falls_back_to_action_weight():
    """The ESPN path, before nflverse has the week."""
    rows = [play(1, play_type="pass", complete=True, touchdown=True, td_team="KC", yards_gained=40, away_score=6),
            play(2, yards_gained=18, away_score=6)]
    td, run = score_game(rows)
    assert td.score > run.score > 0


def test_every_game_is_floored_and_capped():
    shootout = "2025_15_DAL_PHI"
    by_game = {shootout: [play(i, shootout, play_type="pass", complete=True, touchdown=True, td_team="DAL", yards_gained=30,
                               away_score=7 * i, wpa=0.2) for i in range(1, 12)]}
    for i, g in enumerate(("2025_15_NYJ_JAX", "2025_15_TEN_SF")):
        by_game[g] = [play(n, g, yards_gained=16 + n, wpa=0.01) for n in range(1, 5)]
    picks = pick_week(by_game, n=12)
    per = {g: sum(k.play.game_id == g for k in picks) for g in by_game}
    assert per[shootout] == GAME_CAP and all(per[g] >= GAME_FLOOR for g in by_game)
    assert [k.rank for k in picks] == list(range(1, len(picks) + 1))
    assert picks[0].play.game_id == shootout


def test_followed_teams_are_lifted():
    by_game = {"2025_15_NYJ_JAX": [play(1, "2025_15_NYJ_JAX", yards_gained=30, wpa=0.10)],
               "2025_15_TEN_SF": [play(1, "2025_15_TEN_SF", yards_gained=30, wpa=0.12)]}
    assert pick_week(by_game)[0].play.game_id == "2025_15_TEN_SF"
    assert pick_week(by_game, favs={"JAX"})[0].play.game_id == "2025_15_NYJ_JAX"


# ── the shipped Sunday, ranked: what `retrogrid reel` shows with nothing downloaded ──────────────────────────

def _shipped():
    slate = load_slate(paths.BUNDLED_SLATE)
    return week_from_slate(slate, NflversePlayerDirectory.from_snapshot(paths.BUNDLED_DIRECTORY))


def test_shipped_slate_makes_a_week(tmp_path):
    w = _shipped()
    assert (w.season, w.week) == (2025, 15) and len(w.picks) == 40
    assert {k.play.game_id for k in w.picks} == {g["id"] for g in w.games}       # every game is in the show
    assert all(watchable(k.play) for k in w.picks)
    top = w.picks[0]
    star = w.stars[top.play.play_id]
    assert star.player_id in w.players and w.final(top.play.game_id) is not None
    back = load_week(save_week(w, tmp_path))
    assert [(k.play.play_id, k.rank, k.before) for k in back.picks] == [(k.play.play_id, k.rank, k.before) for k in w.picks]
    assert back.stars == w.stars and back.players == w.players
    assert [x.week for x in load_weeks(tmp_path)] == [15]


def _weeks():
    w = _shipped()
    older = make_week(2025, 14, {}, [], None)
    older.picks, older.stars, older.players = w.picks[20:30], w.stars, w.players
    return [w, older]


def test_rundown_shape():
    from retrogrid.reel_console import build_rundown
    weeks = _weeks()
    fav = weeks[0].picks[15].play.game_id.split("_")[2]
    segments, items = build_rundown(weeks, {fav}, random.Random(3))
    assert [s.title for s in segments] == ["TOP 10 · WK 15", "YOUR TEAMS", "AROUND THE LEAGUE · WK 15", "WK 14 REWIND"]
    assert [i.number for i in items[:10]] == [f"#{n}" for n in range(10, 0, -1)] and items[9].pick.rank == 1
    assert all(i.replay for i in items[:10]) and not any(i.replay for i in items if not i.number)
    latest = [i.pick.play.play_id for i in items if i.week is weeks[0]]
    assert len(latest) == len(set(latest))                                        # nothing airs twice in a loop
    assert sum(s.count for s in segments) == len(items) and all(items[s.start].segment == n for n, s in enumerate(segments))
    other = build_rundown(weeks, {fav}, random.Random(4))[1]
    assert [i.pick.play.play_id for i in other[:10]] == [i.pick.play.play_id for i in items[:10]]
    assert [i.pick.play.play_id for i in other] != [i.pick.play.play_id for i in items]      # the recap order is reshuffled


class _Sock:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_text(self, data: str) -> None:
        import json
        self.frames.append(json.loads(data))


def test_the_show_runs_card_play_result_and_never_says_live(monkeypatch):
    from retrogrid import reel_console as rc
    from retrogrid.console import Session
    for name in ("CARD", "SNAP", "LINGER", "REPLAY_LINGER"):
        monkeypatch.setattr(rc, name, 0.0)

    async def go() -> tuple[list[dict], rc.ReelEngine]:
        e = rc.ReelEngine(_weeks(), seed=1)
        monkeypatch.setattr(e, "play_frame", lambda *a, **k: {**rc.ReelEngine.play_frame(e, *a, **k), "duration": 0.0})
        sock = _Sock()
        s = Session(ws=sock)                                                       # type: ignore[arg-type]
        e.sessions.add(s)
        await e.air(e.items[0])
        await e.handle(s, {"type": "sim", "action": "skip", "value": 1})
        return sock.frames, e

    frames, e = asyncio.run(go())
    kinds = [f["type"] for f in frames]
    assert kinds[:2] == ["reel_card", "state"] and kinds.count("play") == 2        # a countdown play airs twice
    card, first = frames[0], e.items[0].pick
    assert card["number"] == "#10" and card["score"].split(" · ")[0].endswith(str(first.before[1]))
    plays = [f for f in frames if f["type"] == "play"]
    assert [p["reel"]["replay"] for p in plays] == [False, True] and plays[0]["reel"]["week"] == 15
    states = [f for f in frames if f["type"] == "state"]
    assert states[0]["active"] is None and states[0]["ghosts"] == {"you": None, "them": None}      # the card gives nothing away
    row = next(t for t in states[0]["threats"] if t["current"])
    assert row["headline"] == "" and states[-1]["reel"]["segments"][0]["current"]
    shown = next(s for s in states if s["reel"]["phase"] == "result")
    assert (shown["matchup"]["them"]["points"], shown["matchup"]["you"]["points"]) == (first.play.home_score, first.play.away_score)
    assert next(t for t in shown["threats"] if t["current"])["headline"] == first.headline
    assert shown["active"]["player_id"] == e.weeks[0].stars[first.play.play_id].player_id
    assert e._jump == 1                                                            # [→] asked for the next item


def test_pregame_reel_fills_the_wait_and_leaves_at_kickoff(monkeypatch):
    """Before anything kicks off a live console shows last week's reel; picking today's feed
    (or [B]) is a choice that sticks, and the first kickoff brings everyone back to the feed."""
    from conftest import needs_slate
    from retrogrid import console, reel_console as rc
    if needs_slate.args[0]:
        return
    monkeypatch.setenv("RETROGRID_LEAGUE", "")
    monkeypatch.setenv("RETROGRID_START", "0")                                    # the sim slate, an hour before its first game
    monkeypatch.setattr(rc, "refresh_cache", lambda: None)

    async def go() -> None:
        e = console.Engine()
        e.rebuild()
        assert e.waiting() and e.kickoff_label().startswith("KICKOFF ")
        e.reel = rc.PregameReel(e, _weeks())
        sock = _Sock()
        s = console.Session(ws=sock)                                               # type: ignore[arg-type]
        await e.attach(s)
        assert s.reel is True and s in e.reel.sessions and s not in e.sessions
        state = next(f for f in sock.frames if f["type"] == "state")
        assert state["reel"]["pregame"] == e.kickoff_label() and state["reel"]["week"] == 15
        assert [f["status"] for f in state["feeds"]] == ["PRE"] * len(e.slate.games)        # today's games, not last week's finals
        game = state["feeds"][0]["game_id"]

        sock.frames.clear()
        await e.handle(s, {"type": "focus_game", "game_id": game})                  # clicking today's feed leaves the reel
        assert s.reel is False and s in e.sessions and s not in e.reel.sessions
        state = sock.frames[-1]
        assert state["type"] == "state" and "reel" not in state and state["pregame"] is True
        await e.end_wait()
        assert s.reel is False                                                     # a pregame tick changes nothing
        await e.handle(s, {"type": "reel"})                                        # [B]: back to the reel
        assert s.reel is True and sock.frames[-1]["type"] == "state" and "reel" in sock.frames[-1]

        e.clock.seek(min(g.kickoff for g in e.slate.games) + 1)
        assert not e.waiting()
        await e.end_wait()
        assert s.reel is None and s in e.sessions and not e.reel.sessions
        await e.handle(s, {"type": "reel", "on": True})                            # nothing to go back to once the day is on
        assert s.reel is None and sock.frames[-1].get("pregame") is False
        late = console.Session(ws=_Sock())                                         # type: ignore[arg-type]
        await e.attach(late)
        assert late.reel is None and late in e.sessions

    asyncio.run(go())
