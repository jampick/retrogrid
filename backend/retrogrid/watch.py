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

The GAME DAY picker ([G] in the console) overrides all of that from inside:
a past Sunday picked there is built on demand into <DATA>/sims/ and stays up,
the watcher standing aside, until LIVE is picked again.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from importlib import import_module
from zoneinfo import ZoneInfo

import httpx

from . import paths
from .providers.espn import SITE, parse_utc, scoreboard_games
from .providers.slate import DEFAULT_SLATE_DIR, load_slate, slate_available

log = logging.getLogger("retrogrid.watch")
ET = ZoneInfo("America/New_York")
SIMS = paths.DATA / "sims"
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
        await new.attach(ns)
    if old is not None:
        old.sessions.clear()
        if old.reel is not None:
            old.reel.sessions.clear()
    return fresh


# ── GAME DAY picker ──────────────────────────────────────────────────────────
pinned: str | None = None         # a sim picked by hand ("sim:2026:2", "sim:shipped"); the watcher leaves it up
busy: str | None = None           # the pick being built right now
today_ids: set[str] | None = None  # the watcher's last answer, for the picker's LIVE row
_tasks: list[asyncio.Task] = []
_lock = asyncio.Lock()
_weeks: dict[int, tuple[float, list[tuple[int, str]]]] = {}


def finished_sundays(season: int) -> list[tuple[int, str]]:
    """(week, date) of each regular-season Sunday in the play-by-play on disk
    that is over, newest first. Cached per file mtime: the picker asks often."""
    import pandas as pd
    f = paths.NFLVERSE / f"play_by_play_{season}.parquet"
    if not f.exists():
        return []
    mtime = f.stat().st_mtime
    if season not in _weeks or _weeks[season][0] != mtime:
        g = pd.read_parquet(f, columns=["week", "game_date", "season_type"]).drop_duplicates()
        g = g[(g.season_type == "REG") & (pd.to_datetime(g.game_date).dt.dayofweek == 6) & (g.game_date < today_et())]
        _weeks[season] = (mtime, sorted({(int(w), str(d)) for w, d in zip(g.week, g.game_date)}, reverse=True))
    return _weeks[season][1]


def sim_dir(season: int, week: int) -> Path:
    return SIMS / f"{season}-wk{week:02d}"


def gameday_options(engine) -> list[dict]:                    # noqa: ANN001
    """Rows for the picker: LIVE, this season's finished Sundays, last season's, the shipped one."""
    n = len(today_ids) if today_ids is not None else None
    cur = pinned or ("live" if engine is not None and engine.live else "sim:shipped")
    rows = [{"key": "live", "label": "LIVE · TODAY'S GAMES" if n is None else f"LIVE · {n} GAMES TODAY" if n else "LIVE · NO GAMES TODAY, WAITS FOR THE NEXT"}]
    season = season_now()
    for yr in (season, season - 1):
        for week, day in finished_sundays(yr):
            rows.append({"key": f"sim:{yr}:{week}", "label": f"SIM · {yr} WK {week} · {datetime.fromisoformat(day):%b %d}".upper()})
    shipped = load_slate(paths.sim_slate())
    rows.append({"key": "sim:shipped", "label": f"SIM SUNDAY · {shipped.season} WK {shipped.week} · SHIPPED"})
    for r in rows:
        r["current"] = r["key"] == cur
    return rows


def prepare_sim(season: int, week: int) -> Path:
    """nflverse files for the season, then the slate and its sprites. Blocking; minutes the first time."""
    out = sim_dir(season, week)
    if not slate_available(out):
        from .tools import fetch_nflverse
        fetch_nflverse.DEST.mkdir(parents=True, exist_ok=True)
        for name, url in fetch_nflverse.assets(season).items():
            fetch_nflverse.fetch(name, url)
        if rc := _tool("build_slate", ["--season", str(season), "--week", str(week), "--out", str(out)]):
            raise RuntimeError(f"build_slate exit {rc}")
        _tool("build_sprites", ["--slate", str(out)])
    return out


async def _say(frame: dict) -> None:
    from . import console
    for s in list(console._by_ws.values()):
        try:
            await s.send(frame)
        except Exception:                                     # noqa: BLE001 — a closing socket is not our problem
            pass


async def choose(key: str) -> None:
    """A pick from the GAME DAY picker, for every open window."""
    global pinned, busy
    from . import console
    if busy or _lock.locked():
        return
    busy = key
    await _say({"type": "gameday", "busy": key})
    note = ""
    try:
        async with _lock:
            if key == "live":
                pinned = None
                if mode() in ("sim", "hold"):
                    os.environ["RETROGRID_MODE"] = "auto"     # hand the day back to the watcher
                if not (console.engine and console.engine.live and console.engine.slate.date == today_et()):
                    ids = await asyncio.to_thread(games_today)
                    if ids:
                        if rc := await asyncio.to_thread(prepare_live, None, False, True):
                            raise RuntimeError(f"live slate not built (exit {rc})")
                        await _put(console.Engine(live=True))
                    else:
                        note = "NO GAMES TODAY: THE WATCHER GOES LIVE AT THE NEXT ONE"
            elif key == "sim:shipped" or key.startswith("sim:"):
                if key == "sim:shipped":
                    slate_dir = paths.sim_slate()
                else:
                    _, yr, wk = key.split(":")
                    slate_dir = await asyncio.to_thread(prepare_sim, int(yr), int(wk))
                pinned = key
                await _put(console.Engine(live=False, slate_dir=slate_dir))
    except Exception as exc:                                  # noqa: BLE001 — offline, a week nflverse lacks: keep what is up
        log.warning("GAME DAY %s: %s", key, exc)
        note = f"COULD NOT LOAD: {exc}"[:80].upper()
    finally:
        busy = None
    await _say({"type": "gameday", "busy": None, "note": note, "options": gameday_options(console.engine)})


async def _put(new) -> None:                                  # noqa: ANN001
    global _tasks
    _tasks = await swap(new, _tasks)
    label = f"LIVE {new.slate.date}" if new.live else f"SIM {new.slate.season} WK {new.slate.week}"
    print(f"RETRO//GRID -> {label}", file=sys.stderr)


async def run() -> None:
    global today_ids
    from . import console
    want_mode = mode()
    live = want_mode in ("live", "hold") or (want_mode == "auto" and os.environ.get("RETROGRID_LIVE") == "1")
    if live and not slate_available(paths.LIVE_SLATE):
        raise RuntimeError("no live slate — `retrogrid live` builds one")
    if not live and not slate_available(DEFAULT_SLATE_DIR):
        raise RuntimeError("no slate — run scripts/fetch_nflverse.py then scripts/build_slate.py (or build_live.py)")
    await _put(console.Engine(live=live))
    if console.engine.live and console.engine.slate.date == today_et():
        today_ids = {g.id for g in console.engine.slate.games}
    try:
        while True:
            await asyncio.sleep(POLL)
            want_mode = mode()
            if want_mode in ("sim", "hold"):
                continue
            today = today_et()
            today_ids = await asyncio.to_thread(games_today)
            if pinned or _lock.locked():                      # a sim picked by hand stays up until LIVE is picked
                continue
            want = decide(want_mode, console.engine, today, today_ids)
            if want is None:
                continue
            async with _lock:
                try:
                    if want == "live":
                        if rc := await asyncio.to_thread(prepare_live, today):
                            log.warning("live slate for %s not built (exit %d); trying again in %.0fs", today, rc, POLL)
                            continue
                    new = console.Engine(live=want == "live")
                except Exception as exc:                      # noqa: BLE001 — offline, half-fetched data: keep what is up
                    log.warning("could not switch to %s: %s", want, exc)
                    continue
                await _put(new)
    finally:
        for t in _tasks:
            t.cancel()
