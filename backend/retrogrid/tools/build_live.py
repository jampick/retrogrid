"""Prepare LIVE mode: today's real NFL slate, straight off ESPN's scoreboard.

Writes the data dir's live/ in the same shape build_slate writes slate/ — games
on a wall-clock axis, an empty plays file (the ESPN provider fills it while you
watch) and a draft pool for the synthetic league: last season plus this
season's finished weeks, restricted to active players on teams playing today.

    retrogrid live        (fetches, builds, serves)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from .. import paths
from ..providers.directory import TEAM_NAMES
from ..providers.espn import SITE, parse_utc, scoreboard_games, season_week
from ..providers.slate import PLAYS_FILE, POOL_FILE, SLATE_FILE
from .build_slate import defence_points, player_points

SRC = paths.NFLVERSE
OUT = paths.LIVE_SLATE
ET = ZoneInfo("America/New_York")
LEAD, TAIL = 2 * 3600.0, 4.5 * 3600.0       # the axis opens before the first kickoff, closes after the last
THIN = 4                                     # fewer games than this (a Thursday, a Monday) and two rosters make too thin a pool


def _stats(name: str, seasons: list[int]) -> pd.DataFrame:
    frames = [pd.read_parquet(p) for s in seasons if (p := SRC / f"{name}_{s}.parquet").exists()]
    df = pd.concat(frames, ignore_index=True)
    return df[df.season_type == "REG"].copy()


def build_pool(season: int, week: int, teams: set[str]) -> list[dict]:
    ro = pd.read_parquet(SRC / f"roster_weekly_{season}.parquet")
    ro = ro[ro.week == ro.week[ro.week <= week].max()]
    ro = ro[(ro.status == "ACT") & ro.team.isin(teams) & ro.position.isin(["QB", "RB", "WR", "TE", "K"]) & ro.gsis_id.notna()]
    st = _stats("stats_player_week", [season - 1, season])
    st = st[(st.season < season) | (st.week < week)]
    st["pts"] = player_points(st)
    pre = st.groupby("player_id").agg(pre_points=("pts", "sum"), pre_games=("pts", "size"))
    pool = []
    for r in ro.itertuples(index=False):
        pts, games = float(pre.pre_points.get(r.gsis_id, 0.0)), int(pre.pre_games.get(r.gsis_id, 0))
        if games:
            pool.append({"id": r.gsis_id, "name": r.full_name, "position": r.position, "team": r.team,
                         "pre_points": round(pts, 2), "pre_games": games,
                         "week_points": round(pts / max(games, 4), 2), "active": True})   # a projection, not a result
    tm = _stats("stats_team_week", [season - 1, season])
    tm = tm[(tm.season < season) | (tm.week < week)]
    allowed = tm.points_allowed if "points_allowed" in tm else pd.Series(21, index=tm.index)
    tm["pts"] = defence_points(tm, allowed.fillna(21))
    pre_t = tm.groupby("team").agg(pre_points=("pts", "sum"), pre_games=("pts", "size"))
    for team in sorted(teams):
        pts, games = float(pre_t.pre_points.get(team, 0.0)), int(pre_t.pre_games.get(team, 0))
        pool.append({"id": f"DEF-{team}", "name": TEAM_NAMES.get(team, team), "position": "DEF", "team": team,
                     "pre_points": round(pts, 2), "pre_games": games,
                     "week_points": round(pts / max(games, 4), 2), "active": True})
    pool.sort(key=lambda e: (-e["pre_points"], e["id"]))
    return pool


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=None, help="slate day in US Eastern, YYYY-MM-DD (default: today)")
    ap.add_argument("--all-teams", action="store_true", help=f"draft from the whole week (automatic on a day with fewer than {THIN} games)")
    args = ap.parse_args(argv)
    day = args.date or datetime.now(ET).date().isoformat()

    board = httpx.get(f"{SITE}/scoreboard", params={"dates": day.replace("-", "")}, timeout=30.0).json()
    season, week = season_week(board)
    games = [g for g in scoreboard_games(board) if parse_utc(g["kickoff_utc"]).astimezone(ET).date().isoformat() == day]
    if not games:
        print(f"no NFL games on {day}", file=sys.stderr)
        return 1
    kicks = [parse_utc(g["kickoff_utc"]) for g in games]
    t0 = min(kicks) - timedelta(seconds=LEAD)
    meta = [{"id": g["id"], "home": g["home"], "away": g["away"], "kickoff": (k - t0).total_seconds(),
             "kickoff_utc": g["kickoff_utc"], "home_score": 0, "away_score": 0, "event": g["event"]}
            for g, k in sorted(zip(games, kicks), key=lambda gk: (gk[1], gk[0]["id"]))]
    wide = args.all_teams or len(games) < THIN
    pool = build_pool(season, week, {t for g in (scoreboard_games(httpx.get(f"{SITE}/scoreboard", timeout=30.0).json()) if wide else games)
                                      for t in (g["home"], g["away"])})

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / SLATE_FILE).write_text(json.dumps({
        "season": season, "week": week, "date": day, "start_utc": t0.isoformat(),
        "duration": (max(kicks) - t0).total_seconds() + TAIL, "games": meta,
    }, indent=2))
    (OUT / PLAYS_FILE).write_text("")
    (OUT / POOL_FILE).write_text(json.dumps(pool, indent=1))
    print(f"live: {season} week {week}, {day}, {len(games)} games, pool {len(pool)}{' (whole week)' if wide else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
