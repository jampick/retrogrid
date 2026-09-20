"""PlayProvider over ESPN's public scoreboard + summary feeds (DESIGN §7, LIVE).

ESPN hands over identity, situation, the score, yards and the GSIS sentence;
`prose.reparse` recovers the rest, exactly as RETROGRID_PROSE rehearsed. Same
surface as SlatePlayProvider, but the play list grows while you watch:

* a play is accepted once its text has held still for a poll (or a later play
  has appeared) — ESPN posts a stub and amends it seconds later;
* if an accepted play is later rewritten or withdrawn (replay review), the
  list is patched and the clock's epoch bumps, so the engine rebuilds.
"""
from __future__ import annotations

import asyncio
import logging
import re
from bisect import bisect_right
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from zoneinfo import ZoneInfo

import httpx

from ..models import Game, PlayRow
from ..sim.clock import SimClock
from .directory import NflversePlayerDirectory
from ..parser.desc import parse_desc
from .prose import reparse
from .slate import Slate

log = logging.getLogger("retrogrid.espn")
ET = ZoneInfo("America/New_York")
SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
TEAM_FIX = {"WSH": "WAS", "LAR": "LA"}                       # ESPN -> nflverse
POLL_SCOREBOARD, POLL_GAME = 15.0, 8.0
_KICK_FROM = re.compile(r"kicks? (?:onside )?-?\d+ yards? from ([A-Z]{2,3}) (\d+)")
ADMIN_TYPES = {"timeout", "official timeout", "end period", "end of half", "end of game", "end of regulation",
               "two-minute warning", "coin toss"}
_TRY = re.compile(r"TOUCHDOWN.*?[.\]]\s+(?=(?:\([^)]*\)\s*)?(?:[A-Z][a-z]*\.[\w'\- ]+ extra point|TWO-POINT CONVERSION))", re.S)
_STATUS = {"STATUS_SCHEDULED": "pre", "STATUS_HALFTIME": "half", "STATUS_FINAL": "final", "STATUS_FINAL_OVERTIME": "final",
           "STATUS_POSTPONED": "pre", "STATUS_CANCELED": "final", "STATUS_DELAYED": "pre"}


def team(abbr: str | None) -> str | None:
    return TEAM_FIX.get(abbr, abbr) if abbr else None


def game_id(season: int, week: int, away: str, home: str) -> str:
    return f"{season}_{week:02d}_{away}_{home}"


def parse_utc(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)


def season_week(board: dict[str, Any]) -> tuple[int, int]:
    first = (board.get("events") or [{}])[0]                  # a ?dates= board carries them per event
    season = (board.get("season") or first.get("season") or {}).get("year")
    week = (board.get("week") or first.get("week") or {}).get("number")
    return int(season), int(week)


def scoreboard_games(board: dict[str, Any]) -> list[dict[str, Any]]:
    """The scoreboard, flattened: one dict per event, nflverse team codes."""
    season, week = season_week(board)
    out = []
    for e in board.get("events", []):
        comp = e["competitions"][0]
        side = {c["homeAway"]: c for c in comp["competitors"]}
        home, away = team(side["home"]["team"]["abbreviation"]), team(side["away"]["team"]["abbreviation"])
        st = comp.get("status") or e["status"]
        sit = comp.get("situation") or {}
        poss = next((team(c["team"]["abbreviation"]) for c in comp["competitors"] if c["team"].get("id") == sit.get("possession")), None)
        name = st["type"]["name"]
        out.append({
            "event": e["id"], "id": game_id(season, week, away, home), "home": home, "away": away,
            "kickoff_utc": e["date"], "status": _STATUS.get(name, "live"), "quarter": int(st.get("period") or 0),
            "clock": st.get("displayClock") or "15:00", "possession": poss,
            "home_score": int(side["home"].get("score") or 0), "away_score": int(side["away"].get("score") or 0),
            "teams": {side["home"]["team"]["id"]: home, side["away"]["team"]["id"]: away},
        })
    return out


def summary_plays(summary: dict[str, Any]) -> list[dict[str, Any]]:
    drives = summary.get("drives") or {}
    every = [*drives.get("previous", []), *([drives["current"]] if drives.get("current") else [])]
    seen, plays = set(), []
    for d in every:
        for p in d.get("plays", []):
            if p.get("id") and p["id"] not in seen and (p.get("text") or "").strip():
                seen.add(p["id"])
                plays.append(p)
    plays.sort(key=lambda p: int(p.get("sequenceNumber") or 0))
    return plays


def _yards(text: str, off: str | None, p: dict[str, Any]) -> int:
    """The parser nets out fumbles, aborted snaps and spot fouls the way
    nflverse does; ESPN's statYardage does not. Trust the sentence first."""
    said = parse_desc(text, posteam=off).yards_gained
    return int(p.get("statYardage") or 0) if said is None else said


def base_row(g: dict[str, Any], p: dict[str, Any], seq: int, sim_time: float) -> PlayRow:
    """What the feed states outright (prose.LIVE_FIELDS); the sentence does the rest."""
    text = p["text"].strip()
    start = p.get("start") or {}
    off = g["teams"].get(str((start.get("team") or {}).get("id")))
    m = _KICK_FROM.search(text)
    if m and team(m.group(1)) in (g["home"], g["away"]):      # nflverse: on a kickoff posteam *receives*
        off = g["away"] if team(m.group(1)) == g["home"] else g["home"]
    other = (g["away"] if off == g["home"] else g["home"]) if off else None
    down = int(start.get("down") or 0) or None
    togo = int(m.group(2)) if m else start.get("yardsToEndzone")      # nflverse: a kickoff's yardline is the tee
    return PlayRow(
        play_id=f"{g['id']}:{p['id']}", game_id=g["id"], seq=seq, sim_time=sim_time,
        quarter=int((p.get("period") or {}).get("number") or 0), clock=(p.get("clock") or {}).get("displayValue") or "0:00",
        down=down, ydstogo=int(start.get("distance") or 0) if down else None,
        yardline_100=int(togo) if togo else None, posteam=off, defteam=other, desc=text, play_type="no_play",
        yards_gained=_yards(text, off, p),
        home_score=int(p.get("homeScore") or 0), away_score=int(p.get("awayScore") or 0),
    )


def split_try(text: str) -> tuple[str, str | None]:
    """ESPN folds the try into the touchdown's sentence; nflverse (and the
    grammar) want two plays."""
    m = _TRY.search(text)
    return (text[:m.end()].strip(), text[m.end():].strip()) if m else (text, None)


def rows_for(g: dict[str, Any], p: dict[str, Any], sim_time: float, before: tuple[int, int],
             directory: NflversePlayerDirectory) -> list[PlayRow]:
    """One feed entry -> 0 (administrative), 1, or 2 (touchdown + try) plays.
    `before` is (home, away) going in: the feed's score already counts the try."""
    if ((p.get("type") or {}).get("text") or "").lower() in ADMIN_TYPES:
        return []
    seq = int(p.get("sequenceNumber") or 0)
    main, after = split_try(p["text"].strip())
    base = base_row(g, {**p, "text": main}, seq, sim_time)
    row = reparse(base, directory)
    if row is base:                                           # reparse returns its input for timeouts, END QUARTER, …
        return []
    if not after:
        return [row]
    h, a = base.home_score, base.away_score
    home_scored = (h - before[0]) >= (a - before[1])
    td = replace(row, home_score=before[0] + 6 if home_scored else h, away_score=a if home_scored else before[1] + 6)
    scorer = g["home"] if home_scored else g["away"]
    tb = replace(base, play_id=base.play_id + "x", seq=seq + 1, desc=after, down=None, ydstogo=None,
                 yardline_100=2 if "TWO-POINT" in after else 15, yards_gained=0,
                 posteam=scorer, defteam=g["away"] if home_scored else g["home"])
    tr = reparse(tb, directory)
    return [td] if tr is tb else [td, tr]


def game_rows(g: dict[str, Any], plays: list[dict[str, Any]], directory: NflversePlayerDirectory,
              sim_time=lambda p: 0.0) -> list[PlayRow]:     # noqa: ANN001
    """Every real play of one game, in order. Administrative lines drop out."""
    rows: list[PlayRow] = []
    before = (0, 0)
    for p in plays:
        rows += rows_for(g, p, sim_time(p), before, directory)
        before = (int(p.get("homeScore") or 0), int(p.get("awayScore") or 0))
    return rows


class EspnPlayProvider:
    def __init__(self, slate: Slate, clock: SimClock, directory: NflversePlayerDirectory) -> None:
        self.slate, self.clock, self.directory = slate, clock, directory
        self._t0 = parse_utc(slate.start_utc)
        self._plays = slate.plays                             # shared with the engine; grows
        self._plays.clear()
        self._games: dict[str, Game] = {g.id: replace(g) for g in slate.games}
        self._meta: dict[str, dict[str, Any]] = {}            # game_id -> scoreboard dict
        self._text: dict[str, str] = {}                       # accepted play_id -> desc
        self._pending: dict[str, str] = {}                    # newest, not yet settled: play_id -> text last poll
        self._queue: asyncio.Queue[PlayRow] = asyncio.Queue()
        self._done: set[str] = set()                          # finals swept once more after the whistle
        self._primed = asyncio.Event()

    # ------------------------------------------------------------ sync helpers
    def wall(self) -> float:
        return (datetime.now(timezone.utc) - self._t0).total_seconds()

    def plays_until(self, t: float) -> list[PlayRow]:
        return self._plays[: bisect_right([p.sim_time for p in self._plays], t)]

    def live_games(self) -> list[Game]:
        return self.games_at(self.clock.now())

    def games_at(self, t: float) -> list[Game]:               # live: there is only now
        return [replace(g) for g in self._games.values()]

    # ---------------------------------------------------------------- contract
    async def stream_plays(self, after: float | None = None) -> AsyncIterator[PlayRow]:
        while True:
            yield await self._queue.get()

    async def prime(self) -> None:
        """First sweep: everything already played lands at its own wallclock."""
        async with httpx.AsyncClient(timeout=20.0) as http:
            while True:
                try:
                    await self._sweep(http, backfill=True)
                    break
                except Exception as exc:                      # noqa: BLE001 — ESPN drops a handshake now and then
                    log.warning("first sweep failed, retrying: %s", exc)
                    await asyncio.sleep(3.0)
        self._primed.set()

    async def run(self) -> None:
        await self._primed.wait()
        async with httpx.AsyncClient(timeout=20.0) as http:
            while True:
                await asyncio.sleep(POLL_GAME)
                try:
                    await self._sweep(http, backfill=False)
                except Exception as exc:                      # noqa: BLE001 — a bad poll must not end the broadcast
                    log.warning("poll failed: %s", exc)
                if abs(self.clock.now() - self.wall()) > 5.0:  # laptop slept; monotonic time did not
                    self.clock.seek(self.wall())

    # ---------------------------------------------------------------- internal
    async def _sweep(self, http: httpx.AsyncClient, backfill: bool) -> None:
        board = (await http.get(f"{SITE}/scoreboard")).json()
        want = []
        for g in scoreboard_games(board):
            if g["id"] not in self._games:
                continue
            self._meta[g["id"]] = g
            base = self._games[g["id"]]
            if g["status"] == "pre":                          # the feed strip shows when, not 0:00
                g["clock"] = f"{parse_utc(g['kickoff_utc']).astimezone(ET):%H:%M}"
            self._games[g["id"]] = replace(base, status=g["status"], quarter=g["quarter"], clock=g["clock"],
                                           home_score=g["home_score"], away_score=g["away_score"], possession=g["possession"])
            if g["status"] in ("live", "half") or (g["status"] == "final" and g["id"] not in self._done):
                want.append(g)
        got = await asyncio.gather(*(http.get(f"{SITE}/summary", params={"event": g["event"]}) for g in want), return_exceptions=True)
        dirty = False
        fresh: list[PlayRow] = []
        for g, resp in zip(want, got):
            if isinstance(resp, Exception):
                log.warning("summary %s failed: %s", g["id"], resp)
                continue
            raw = summary_plays(resp.json())
            changed, new = self._merge(g, raw, backfill)
            dirty |= changed
            fresh += new
            if g["status"] == "final" and not self._pending.keys() & {f"{g['id']}:{p['id']}" for p in raw}:
                self._done.add(g["id"])
        if dirty:
            self._resort()
            self.clock.seek(self.clock.now())                 # epoch bump: the engine rebuilds
        for row in sorted(fresh, key=lambda p: (p.sim_time, p.game_id, p.seq)):
            self._plays.append(row)
            if not backfill:
                self._queue.put_nowait(row)
        if backfill:
            self._resort()

    def _merge(self, g: dict[str, Any], raw: list[dict[str, Any]], backfill: bool) -> tuple[bool, list[PlayRow]]:
        now = self.clock.now()
        final = g["status"] == "final"
        own = lambda row: row.play_id.removesuffix("x")       # noqa: E731 — a try belongs to its touchdown
        ids = {f"{g['id']}:{p['id']}" for p in raw}
        dirty = False
        if raw:
            for old in [p for p in self._plays if p.game_id == g["id"] and own(p) not in ids]:
                self._plays.remove(old)                       # withdrawn by the booth
                self._text.pop(own(old), None)
                dirty = True
        fresh: list[PlayRow] = []
        before = (0, 0)
        for i, p in enumerate(raw):
            pid, text = f"{g['id']}:{p['id']}", p["text"].strip()
            going_in, before = before, (int(p.get("homeScore") or 0), int(p.get("awayScore") or 0))
            if pid in self._text:
                if self._text[pid] != text:                   # amended after we showed it
                    self._text[pid] = text
                    old = [o for o in self._plays if own(o) == pid]
                    when = old[0].sim_time if old else now
                    for o in old:
                        self._plays.remove(o)
                    self._plays += rows_for(g, p, when, going_in, self.directory)
                    dirty = True
                continue
            newest = i == len(raw) - 1
            if newest and not final and not backfill and self._pending.get(pid) != text:
                self._pending[pid] = text                     # let it hold still for one poll
                continue
            self._pending.pop(pid, None)
            stamp = (parse_utc(p["wallclock"]) - self._t0).total_seconds() if p.get("wallclock") else now
            self._text[pid] = text
            fresh += rows_for(g, p, min(stamp, now) if backfill else now, going_in, self.directory)
        return dirty, fresh

    def _resort(self) -> None:
        self._plays.sort(key=lambda p: (p.sim_time, p.game_id, p.seq))
