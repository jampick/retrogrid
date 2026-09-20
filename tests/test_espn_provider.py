"""The LIVE path: ESPN feed entries -> PlayRows, and the grow/amend merge."""
import asyncio

from conftest import needs_nflverse
from retroffb.models import Game
from retroffb.providers import espn
from retroffb.providers.directory import NflversePlayerDirectory
from retroffb.providers.slate import Slate
from retroffb.sim.clock import SimClock

G = {"event": "1", "id": "2026_02_DET_BUF", "home": "BUF", "away": "DET", "status": "live", "teams": {"2": "BUF", "8": "DET"}}


def play(pid, seq, text, kind="Rush", team="2", home=0, away=0, **start):
    return {"id": pid, "sequenceNumber": str(seq), "type": {"text": kind}, "text": text, "homeScore": home, "awayScore": away,
            "period": {"number": 1}, "clock": {"displayValue": "9:13"}, "statYardage": 0,
            "start": {"team": {"id": team}, "down": 1, "distance": 10, "yardsToEndzone": 60, **start}}


def test_split_try():
    td, pat = espn.split_try("(Shotgun) J.Allen pass deep middle to J.Palmer for 43 yards, TOUCHDOWN [S.Gill-Howard]. "
                             "T.Bass extra point is GOOD, Center-R.Ferguson, Holder-T.Doman.")
    assert td.endswith("TOUCHDOWN [S.Gill-Howard].") and pat.startswith("T.Bass extra point is GOOD")
    assert espn.split_try("J.Cook up the middle to BUF 36 for 12 yards (C.Clark).")[1] is None


@needs_nflverse
def test_rows_for_touchdown_kickoff_and_admin():
    d = NflversePlayerDirectory.from_data_dir()
    td = play("10", 100, "J.Allen left guard for 1 yard, TOUCHDOWN. T.Bass extra point is GOOD, Center-R.Ferguson, Holder-T.Doman.",
              "Rushing Touchdown", home=7, yardsToEndzone=1)
    rows = espn.rows_for(G, td, 5.0, (0, 0), d)
    assert [r.play_type for r in rows] == ["run", "extra_point"]
    assert (rows[0].home_score, rows[1].home_score) == (6, 7) and rows[0].touchdown
    assert rows[1].play_id == rows[0].play_id + "x" and rows[1].extra_point_result == "good"

    ko = play("11", 110, "T.Bass kicks 65 yards from BUF 35 to end zone, Touchback to the DET 35.", "Kickoff", team="8", home=7)
    (row,) = espn.rows_for(G, ko, 6.0, (7, 0), d)
    assert (row.play_type, row.posteam, row.defteam, row.yardline_100) == ("kickoff", "DET", "BUF", 35)

    assert espn.rows_for(G, play("12", 120, "Timeout #1 by BUF at 01:51.", "Timeout"), 7.0, (7, 0), d) == []


@needs_nflverse
def test_merge_holds_the_newest_play_then_amends():
    async def go():
        d = NflversePlayerDirectory.from_data_dir()
        slate = Slate(2026, 2, "2026-09-17", "2026-09-17T22:00:00+00:00", 9e4, [Game(G["id"], "BUF", "DET", 0.0)], [])
        prov = espn.EspnPlayProvider(slate, SimClock(9e4, start_at=100.0), d)
        a = play("1", 10, "J.Cook up the middle to BUF 36 for 12 yards (C.Clark).")
        b = play("2", 20, "J.Cook right guard to 50 for 1 yard")
        dirty, fresh = prov._merge(G, [a, b], backfill=False)
        assert not dirty and [r.desc for r in fresh] == [a["text"]]      # b is newest: ESPN may still be typing
        slate.plays.extend(fresh)
        dirty, fresh = prov._merge(G, [a, b], backfill=False)
        assert not dirty and [r.desc for r in fresh] == [b["text"]]      # held still for a poll
        slate.plays.extend(fresh)
        b2 = {**b, "text": "J.Cook right guard to 50 for 1 yard (J.Campbell; T.Williams)."}
        dirty, fresh = prov._merge(G, [a, b2], backfill=False)
        assert dirty and not fresh and slate.plays[-1].desc == b2["text"]
    asyncio.run(go())
