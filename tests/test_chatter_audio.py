from __future__ import annotations

import json
from pathlib import Path

from retrogrid.models import Game, PlayRow
from retrogrid.providers.audio import AudioTable
from retrogrid.providers.chatter import TEAMS, ChatterBox, ChatterLine, RedditChatter, StubChatter, clean, parse_feed, route

FEED = Path(__file__).parent / "fixtures" / "reddit" / "comments.rss"
GAMES = [Game("2026_02_CLE_TB", "TB", "CLE", 0, "live"), Game("2026_02_PHI_TEN", "TEN", "PHI", 0, "live"),
         Game("2026_02_MIN_CHI", "CHI", "MIN", 0, "live"), Game("2026_02_PIT_NE", "NE", "PIT", 0, "live")]


def test_all_32_teams():
    assert len(TEAMS) == 32 and len({sub.lower() for _, _, sub in TEAMS.values()}) == 32


def test_route_by_title_and_subreddit():
    assert route("nfl", "Game Thread: Cleveland Browns (0-1) @ Tampa Bay Buccaneers (0-1)", GAMES) == ("2026_02_CLE_TB", None)
    assert route("nfl", "[Highlight] Vikings pick off Caleb Williams", GAMES) == ("2026_02_MIN_CHI", None)
    assert route("nfl", "Game Thread: RedZone/Game hub- 9/20/26", GAMES) is None
    assert route("nfl", "Post Game Thread: Cleveland Browns @ Tampa Bay Buccaneers", GAMES) is None
    assert route("nfl", "Game Thread: Green Bay Packers (0-1) @ New York Jets (1-0)", GAMES) is None      # not on this slate
    assert route("nfl", "Browns, Steelers and Bears all trail at the half", GAMES) is None                # names three games
    assert route("eagles", "Game Thread: Eagles (1-0) @ Titans (0-1) - Sun, Sep 20 @ 1:00 PM EDT", GAMES) == ("2026_02_PHI_TEN", "PHI")
    assert route("eagles", "Who are we drafting", GAMES) is None


def test_clean():
    assert clean("&lt;div class=&quot;md&quot;&gt;&lt;p&gt;C&amp;#39;mon  Baker&lt;/p&gt; &lt;/div&gt;") == "C'mon Baker"
    assert clean("&lt;p&gt;[deleted]&lt;/p&gt;") == ""
    assert clean("&lt;p&gt;https://x.com/a/b&lt;/p&gt;") == ""
    assert len(clean("&lt;p&gt;" + "word " * 100 + "&lt;/p&gt;")) <= 160


def test_parse_real_feed_and_dedupe():
    lines = parse_feed(FEED.read_text(), GAMES)
    assert lines and {l.game_id for l in lines} <= {g.id for g in GAMES}
    assert all(l.text and l.source.startswith("r/") and l.id.startswith("t1_") for l in lines)
    box = ChatterBox()
    assert sum(box.push(l) for l in lines) == len(lines)
    assert sum(box.push(l) for l in lines) == 0
    assert box.recent(lines[-1].game_id)[-1]["text"] == [l for l in lines if l.game_id == lines[-1].game_id][-1].text
    assert box.recent(None) == []


def test_reddit_url_is_one_request_for_every_live_game():
    url = RedditChatter(ChatterBox(), lambda: GAMES).url(GAMES)
    assert url.startswith("https://www.reddit.com/r/nfl+") and url.endswith("/comments/.rss?limit=100")
    assert "buccaneers" in url and "CHIBears" in url and url.count("+") == 8


def test_stub_crowd_is_deterministic_and_two_sided():
    p = PlayRow(play_id="2025_15_KC_BUF:9", game_id="2025_15_KC_BUF", seq=9, sim_time=1, quarter=1, clock="1:00", down=1,
                ydstogo=10, yardline_100=20, posteam="KC", defteam="BUF", desc="", play_type="pass", touchdown=True)
    a, b = ChatterBox(), ChatterBox()
    StubChatter(a).react(p, "TD", "KC"); StubChatter(b).react(p, "TD", "KC")
    assert a.recent(p.game_id) == b.recent(p.game_id)
    crowds = {l["team"] for l in a.recent(p.game_id)}
    assert {"KC", "BUF"} <= crowds
    assert all("{" not in l["text"] for l in a.recent(p.game_id))


def test_audio_overrides_merge_and_reload(tmp_path):
    seed, over = tmp_path / "seed.json", tmp_path / "over.json"
    seed.write_text(json.dumps({"DEN": {"station": "KOA 850", "url": "http://x/hls.m3u8"}, "KC": {"station": "KCFX", "url": None}}))
    t = AudioTable(seed, over)
    a = t.for_game("2025_15_DEN_KC")
    assert a["away"] == {"team": "DEN", "station": "KOA 850", "url": "http://x/hls.m3u8", "kind": "hls"}
    assert a["home"]["url"] is None and a["nfl_plus"].startswith("https://")
    over.write_text(json.dumps({"KC": {"station": "MY PICK", "url": "https://y/stream"}}))
    assert t.for_game("2025_15_DEN_KC")["home"] == {"team": "KC", "station": "MY PICK", "url": "https://y/stream", "kind": "direct"}
    assert t.for_game(None) is None
    assert t.for_game("2025_15_SEA_SF")["home"]["url"] is None        # unknown team: a row, no stream


def test_shipped_seed_is_well_formed():
    from retrogrid.providers.audio import SEED
    rows = json.loads(SEED.read_text())
    assert set(rows) <= set(TEAMS)
    for r in rows.values():
        assert r.get("station") and (r.get("url") is None or r["url"].startswith(("http://", "https://")))
