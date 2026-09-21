"""Download the nflverse files the stub data layer needs into the data dir's nflverse/.

Public release assets: no account, no key. Idempotent — files already on disk
are skipped (pass --force to re-download).

    retrogrid fetch [--season 2026] [--lite] [--if-newer]
"""
from __future__ import annotations

import argparse
import os
import sys
import httpx

from .. import paths

SEASON = int(os.environ.get("RETROGRID_SEASON", "2025"))
BASE = "https://github.com/nflverse/nflverse-data/releases/download"
DEST = paths.NFLVERSE


def assets(season: int, lite: bool = False) -> dict[str, str]:
    """`lite` skips the 20 MB play-by-play: LIVE mode only needs rosters and weekly stats."""
    out = {
        f"play_by_play_{season}.parquet": f"{BASE}/pbp/play_by_play_{season}.parquet",
        "players.parquet": f"{BASE}/players/players.parquet",
        f"roster_weekly_{season}.parquet": f"{BASE}/weekly_rosters/roster_weekly_{season}.parquet",
        f"stats_player_week_{season}.parquet": f"{BASE}/stats_player/stats_player_week_{season}.parquet",
        f"stats_team_week_{season}.parquet": f"{BASE}/stats_team/stats_team_week_{season}.parquet",
    }
    if lite:
        out.pop(f"play_by_play_{season}.parquet")
    return out


def fetch(name: str, url: str, force: bool = False, if_newer: bool = False) -> bool:
    """Download one asset. Returns True if it was fetched, False if skipped.
    `if_newer` asks the server first (a HEAD, a few hundred bytes) and downloads
    only when the ETag moved: the reel checks hourly, the file changes nightly."""
    path = DEST / name
    tag = path.with_suffix(path.suffix + ".etag")
    have = path.exists() and path.stat().st_size > 0
    if have and if_newer and not force:
        head = httpx.head(url, follow_redirects=True, timeout=30.0)
        head.raise_for_status()
        if tag.exists() and head.headers.get("etag") == tag.read_text().strip():
            return False
    elif have and not force:
        return False
    tmp = path.with_suffix(path.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as resp:
        resp.raise_for_status()
        if etag := resp.headers.get("etag"):
            tag.write_text(etag)
        with tmp.open("wb") as fh:
            for chunk in resp.iter_bytes(1 << 16):
                fh.write(chunk)
    tmp.replace(path)
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-download existing files")
    ap.add_argument("--season", type=int, default=SEASON)
    ap.add_argument("--lite", action="store_true", help="skip play-by-play (enough for LIVE mode)")
    ap.add_argument("--if-newer", action="store_true", help="re-download only files that changed upstream")
    args = ap.parse_args(argv)
    DEST.mkdir(parents=True, exist_ok=True)
    failed = 0
    for name, url in assets(args.season, args.lite).items():
        try:
            got = fetch(name, url, args.force, args.if_newer)
        except httpx.HTTPError as exc:
            print(f"FAIL  {name}: {exc}", file=sys.stderr)
            failed += 1
            continue
        size = (DEST / name).stat().st_size / 1e6
        print(f"{'fetch' if got else 'skip '} {name}  ({size:.1f} MB)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
