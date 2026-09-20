"""RED ZONE mode: drive tracking, and the director that rides a drive to its end."""
from __future__ import annotations

import asyncio

from conftest import needs_slate
from retrogrid.models import PlayRow
from retrogrid.scoring.drive import DriveTracker


def play(seq: int, yl: int, gain: int = 0, team: str = "KC", **kw) -> PlayRow:
    base = dict(play_id=f"g:{seq}", game_id="g", seq=seq, sim_time=float(seq), quarter=1, clock="10:00", down=1, ydstogo=10,
                yardline_100=yl, posteam=team, defteam="BUF" if team == "KC" else "KC", desc="", play_type="run", yards_gained=gain)
    return PlayRow(**{**base, **kw})


def test_red_zone_starts_with_the_play_that_gets_there():
    t = DriveTracker()
    t.ingest(play(1, 45, 5))
    assert not t.get("g").red_zone
    t.ingest(play(2, 40, 22))                                # down to the 18
    d = t.get("g")
    assert d.red_zone and d.spot == 18 and d.since == 2.0
    seq = d.seq
    t.ingest(play(3, 18, -8, sack=True))                     # knocked back out: still the same drive
    assert not t.get("g").red_zone and t.get("g").seq == seq


def test_drive_resolutions():
    for kw in (dict(touchdown=True), dict(interception=True), dict(fumble_lost=True), dict(play_type="field_goal"),
               dict(play_type="punt"), dict(down=4, ydstogo=3, yards_gained=1)):
        t = DriveTracker()
        t.ingest(play(1, 12, 2))
        seq = t.get("g").seq
        assert t.ingest(play(2, 10, **kw)), kw
        assert not t.get("g").red_zone and t.get("g").seq == seq + 1
    t = DriveTracker()
    t.ingest(play(1, 12, 2))
    assert not t.ingest(play(2, 10, play_type="no_play", penalty=True)) and t.get("g").red_zone
    assert not t.ingest(play(3, 10, 4, down=4, ydstogo=3, first_down=True))      # converted: drive goes on
    seq = t.get("g").seq
    t.ingest(play(4, 80, 3, team="BUF"))                     # other side has it, and we never saw why
    assert t.get("g").seq == seq + 1 and not t.get("g").red_zone


@needs_slate
def test_director_rides_a_drive_then_cuts(monkeypatch):
    monkeypatch.setenv("RETROGRID_LEAGUE", "")
    monkeypatch.setenv("RETROGRID_START", "5400")
    from retrogrid import console

    async def go():
        e = console.Engine()
        e.rebuild()
        a, b, c = [g.id for g in e.slate.games][:3]
        status = {a: "live", b: "live", c: "live"}
        e.drives.reset()
        s = console.Session(ws=None, redzone=True, focus=c)
        assert not await e.rz_direct(s, status)               # nobody inside the 20: leave the screen alone
        da, db = e.drives.get(a), e.drives.get(b)
        da.team, da.spot, db.team, db.spot = "X", 15, "Y", 4
        assert await e.rz_direct(s, status) and s.focus == b  # closest to the goal line
        assert e.rz_riding(s) and e.state_frame(s)["clock"]["riding"]
        da.spot = 1
        assert not await e.rz_direct(s, status) and s.focus == b      # ...and stays until that drive resolves
        db.seq += 1; db.team = db.spot = None
        s.rz_hold = asyncio.get_running_loop().time() + 60
        assert not await e.rz_direct(s, status) and s.focus == b      # the score is still on screen
        s.rz_hold = 0
        assert await e.rz_direct(s, status) and s.focus == a
        await e.handle(s, {"type": "focus_game", "game_id": c})       # a hand-picked feed is a ride too
        assert not await e.rz_direct(s, status) and s.focus == c
        feeds = {f["game_id"]: f for f in e.state_frame(s)["feeds"]}
        assert "rz" in feeds[a]
    asyncio.run(go())
