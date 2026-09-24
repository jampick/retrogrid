"""Rank the finished weeks of a season into the reel cache (<DATA>/reel/).

One file per week, about 40 plays each, picked on win probability added plus
a flat bonus for plays that look good whatever the score was (scoring/reel.py).
A week already on disk is left alone, except the newest one: nflverse keeps
filling that in through Monday night, so it is rebuilt whenever the
play-by-play file is newer than the cache.

    retrogrid build-reel [--season 2026] [--weeks 1 2] [--force]
    retrogrid build-reel --from-slate <dir>      rank a prepared slate instead (no WPA: ESPN days)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import paths
from ..providers.reel import REEL_DIR, make_week, save_week, week_from_slate, week_path
from ..scoring.reel import PER_WEEK
from .fetch_nflverse import SEASON


def build_season(season: int, weeks: list[int] | None = None, force: bool = False, n: int = PER_WEEK,
                 reel_dir: Path = REEL_DIR) -> list[int]:
    """Build what is missing or stale. Returns the weeks written."""
    import pandas as pd

    from ..providers.directory import NflversePlayerDirectory
    from .build_slate import _is_admin, to_play_row

    pbp_path = paths.NFLVERSE / f"play_by_play_{season}.parquet"
    if not pbp_path.exists():
        raise FileNotFoundError(pbp_path)
    have = sorted(int(w) for w in pd.read_parquet(pbp_path, columns=["week"]).week.unique())
    todo = []
    for w in weeks or have:
        cache = week_path(season, w, reel_dir)
        stale = w == have[-1] and cache.exists() and cache.stat().st_mtime < pbp_path.stat().st_mtime
        if w in have and (force or stale or not cache.exists()):
            todo.append(w)
    if not todo:
        return []
    pbp = pd.read_parquet(pbp_path)
    for w in todo:
        wk = pbp[pbp.week == w]
        directory = NflversePlayerDirectory.from_data_dir(week=w, season=season)
        by_game, games = {}, []
        for game_id, game in wk.groupby("game_id", sort=True):
            rows = [r for r in game.itertuples(index=False) if not _is_admin(r)]
            by_game[game_id] = [to_play_row(r, seq, 0.0) for seq, r in enumerate(rows)]
            first = game.iloc[0]
            games.append({"id": game_id, "home": first.home_team, "away": first.away_team,
                          "home_score": int(first.home_score), "away_score": int(first.away_score)})
        week = make_week(season, w, by_game, games, directory, n)
        save_week(week, reel_dir)
        top = week.picks[0]
        print(f"reel: {season} wk {w:2d}, {len(games):2d} games, {len(week.picks)} picks  "
              f"#1 {top.play.game_id.split('_', 2)[2].replace('_', '@')} {top.headline}")
    return todo


def refresh(season: int) -> list[int]:
    """Ask nflverse whether the season's files moved (a HEAD each), pull the ones
    that did, rank whatever that made new. What `retrogrid reel` runs at launch and hourly."""
    from . import fetch_nflverse
    fetch_nflverse.DEST.mkdir(parents=True, exist_ok=True)
    for name, url in fetch_nflverse.assets(season).items():
        fetch_nflverse.fetch(name, url, if_newer=True)
    return build_season(season)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, default=SEASON)
    ap.add_argument("--weeks", type=int, nargs="*", help="only these weeks (default: every week in the file)")
    ap.add_argument("--force", action="store_true", help="rebuild weeks already cached")
    ap.add_argument("--n", type=int, default=PER_WEEK, help=f"plays kept per week (default {PER_WEEK})")
    ap.add_argument("--from-slate", type=Path, help="rank a prepared slate dir (data/slate, data/live) instead of nflverse")
    args = ap.parse_args(argv)

    if args.from_slate:
        from ..providers.directory import NflversePlayerDirectory
        from ..providers.slate import load_slate
        slate = load_slate(args.from_slate)
        directory = (NflversePlayerDirectory.from_snapshot(paths.BUNDLED_DIRECTORY) if args.from_slate.resolve() == paths.BUNDLED_SLATE.resolve()
                     else NflversePlayerDirectory.from_data_dir(week=slate.week, season=slate.season))
        week = week_from_slate(slate, directory)
        print(f"reel: {save_week(week)}  ({len(week.picks)} picks, source {week.source})")
        return 0
    try:
        built = build_season(args.season, args.weeks, args.force, args.n)
    except FileNotFoundError as e:
        print(f"missing {e} (run `retrogrid fetch --season {args.season}` first)", file=sys.stderr)
        return 1
    if not built:
        print(f"reel: {args.season} is up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
