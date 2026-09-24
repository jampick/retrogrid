"""Which day's games the console is showing, and when that should change.

An Engine is built around one slate: the shipped SIM SUNDAY, or one real day
off ESPN's scoreboard. This module keeps the right one up. At startup the CLI
asks ESPN once (games today in US Eastern -> LIVE, otherwise the sim); after
that `run` asks again every POLL seconds and swaps the engine under the open
sessions when the answer moves: a window opened on Thursday afternoon has the
night game in its feed list, a server left up since Sunday rolls to Monday
night once the Sunday axis runs out, then back to the sim, then to Thursday.
Nobody has to remember a flag.

    RETROGRID_MODE   auto  (plain `retrogrid`)  live when ESPN lists games today, else the sim, re-checked
                     live  (`retrogrid live`)   today's games; a finished day rolls to the next one with games
                     sim   (`retrogrid sim`)    the shipped Sunday, ESPN never asked
                     hold  (`live --date`)      the day you asked for, no re-check
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from datetime import date, datetime
from importlib import import_module
from zoneinfo import ZoneInfo

import httpx

from . import paths
from .providers.espn import SITE, parse_utc, scoreboard_games
from .providers.slate import DEFAULT_SLATE_DIR, load_slate, slate_available

log = logging.getLogger("retrogrid.watch")
ET = ZoneInfo("America/New_York")
POLL = float(os.environ.get("RETROGRID_POLL", "300"))     # seconds between scoreboard checks; kickoffs are known days ahead
STALE = 6 * 3600.0            # LIVE re-pulls this season's rosters/stats when older than this


def mode() -> str:
    """Read late: the CLI imports this module before it has decided."""
    return os.environ.get("RETROGRID_MODE") or ("live" if os.environ.get("RETROGRID_LIVE") == "1" else "sim")


def season_now() -> int:
    today = date.today()
    return today.year if today.month >= 3 else today.year - 1


def today_et() -> str:
    return datetime.now(ET).date().isoformat()


def game_ids_on(board: dict, day: str) -> set[str]:
    """ESPN event ids kicking off on `day` (US Eastern). A Thursday night game is Friday in UTC."""
    return {g["id"] for g in scoreboard_games(board)
            if parse_utc(g["kickoff_utc"]).astimezone(ET).date().isoformat() == day}


def games_today(timeout: float = 8.0) -> set[str] | None:
    """One scoreboard request. None when ESPN cannot be reached: the caller keeps what it has."""
    day = today_et()
    try:
        board = httpx.get(f"{SITE}/scoreboard", params={"dates": day.replace("-", "")}, timeout=timeout).json()
        return game_ids_on(board, day)
    except Exception as exc:                                  # noqa: BLE001 — offline is not an error here
        log.warning("scoreboard unreachable: %s", exc)
        return None


def _tool(name: str, argv: list[str]) -> int:
    return int(import_module(f".tools.{name}", __package__).main(argv) or 0)


def cached_day() -> str | None:
    """The day the live slate on disk was built for, if there is one."""
    try:
        return load_slate(paths.LIVE_SLATE).date if slate_available(paths.LIVE_SLATE) else None
    except Exception:                                         # noqa: BLE001
        return None


def prepare_live(day: str | None = None, all_teams: bool = False, keep: bool = False) -> int:
    """Rosters and stats off nflverse, the slate for `day` off ESPN, sprites for its league.
    Blocking (a few MB the first time each day); the watcher runs it in a thread.
    `keep` reuses the cached slate only when it is for the day asked for."""
    season = season_now()
    roster = paths.NFLVERSE / f"roster_weekly_{season}.parquet"
    stale = not roster.exists() or time.time() - roster.stat().st_mtime > STALE
    _tool("fetch_nflverse", ["--season", str(season - 1), "--lite"])       # last year seeds the draft pool early on
    if rc := _tool("fetch_nflverse", ["--season", str(season), "--lite"] + (["--force"] if stale else [])):
        return rc
    if not (keep and cached_day() == (day or today_et())):
        if rc := _tool("build_live", (["--date", day] if day else []) + (["--all-teams"] if all_teams else [])):
            return rc
    return _tool("build_sprites", ["--live"])


def decide(mode: str, engine, today: str, today_ids: set[str] | None) -> str | None:     # noqa: ANN001
    """Which engine should be up: "live" or "sim" when a new one is due, None to leave it alone.
    `today_ids` is None when ESPN did not answer this round."""
    if mode in ("sim", "hold") or today_ids is None:
        return None
    if not engine.live:
        return "live" if today_ids else None
    if engine.slate.date == today:                            # today's slate: only a changed schedule replaces it
        return "live" if today_ids and today_ids != {g.id for g in engine.slate.games} else None
    if engine.clock.now() < engine.clock.duration:            # yesterday's axis still open (a late game running long)
        return None
    if today_ids:
        return "live"
    return "sim" if mode == "auto" else None


async def swap(new, tasks: list[asyncio.Task]) -> list[asyncio.Task]:                    # noqa: ANN001
    """Retire the running engine, put `new` up, and carry every open session across
    with its preferences (favs, layer, AUTO, RED ZONE) intact."""
    from . import console
    for t in tasks:
        t.cancel()
    old, console.engine = console.engine, new
    fresh = console.launch(new)
    for ws, s in list(console._by_ws.items()):
        ns = console.Session(ws, s.viewer if s.viewer in new.teams else new.default_viewer,
                             ffb=s.ffb and new.league is not None, favs=set(s.favs), auto=s.auto, redzone=s.redzone)
        console._by_ws[ws] = ns
        new.sessions.add(ns)
        await new.catch_up(ns)
    if old is not None:
        old.sessions.clear()
    return fresh


async def run() -> None:
    from . import console
    want_mode = mode()
    live = want_mode in ("live", "hold") or (want_mode == "auto" and os.environ.get("RETROGRID_LIVE") == "1")
    if live and not slate_available(paths.LIVE_SLATE):
        raise RuntimeError("no live slate — `retrogrid live` builds one")
    if not live and not slate_available(DEFAULT_SLATE_DIR):
        raise RuntimeError("no slate — run scripts/fetch_nflverse.py then scripts/build_slate.py (or build_live.py)")
    tasks = await swap(console.Engine(live=live), [])
    try:
        while want_mode in ("auto", "live"):
            await asyncio.sleep(POLL)
            today = today_et()
            want = decide(want_mode, console.engine, today, await asyncio.to_thread(games_today))
            if want is None:
                continue
            try:
                if want == "live":
                    if rc := await asyncio.to_thread(prepare_live, today):
                        log.warning("live slate for %s not built (exit %d); trying again in %.0fs", today, rc, POLL)
                        continue
                new = console.Engine(live=want == "live")
            except Exception as exc:                          # noqa: BLE001 — offline, half-fetched data: keep what is up
                log.warning("could not switch to %s: %s", want, exc)
                continue
            tasks = await swap(new, tasks)
            games = ", ".join(f"{g.away}@{g.home}" for g in new.slate.games)
            print(f"RETRO//GRID -> {'LIVE ' + new.slate.date + ': ' + games if new.live else 'SIM SUNDAY'}", file=sys.stderr)
        await asyncio.Event().wait()                          # sim / hold: this engine is the whole show
    finally:
        for t in tasks:
            t.cancel()
