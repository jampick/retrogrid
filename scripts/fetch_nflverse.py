"""Download the nflverse files the stub data layer needs into data/nflverse/.

Public release assets: no account, no key. Idempotent — files already on disk
are skipped (pass --force to re-download).

    .venv/bin/python scripts/fetch_nflverse.py
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

SEASON = int(os.environ.get("RETROFFB_SEASON", "2025"))
BASE = "https://github.com/nflverse/nflverse-data/releases/download"
ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "data" / "nflverse"

ASSETS: dict[str, str] = {
    f"play_by_play_{SEASON}.parquet": f"{BASE}/pbp/play_by_play_{SEASON}.parquet",
    "players.parquet": f"{BASE}/players/players.parquet",
    f"roster_weekly_{SEASON}.parquet": f"{BASE}/weekly_rosters/roster_weekly_{SEASON}.parquet",
    f"stats_player_week_{SEASON}.parquet": f"{BASE}/stats_player/stats_player_week_{SEASON}.parquet",
    f"stats_team_week_{SEASON}.parquet": f"{BASE}/stats_team/stats_team_week_{SEASON}.parquet",
}


def fetch(name: str, url: str, force: bool = False) -> bool:
    """Download one asset. Returns True if it was fetched, False if skipped."""
    path = DEST / name
    if path.exists() and path.stat().st_size > 0 and not force:
        return False
    tmp = path.with_suffix(path.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in resp.iter_bytes(1 << 16):
                fh.write(chunk)
    tmp.replace(path)
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-download existing files")
    args = ap.parse_args(argv)
    DEST.mkdir(parents=True, exist_ok=True)
    failed = 0
    for name, url in ASSETS.items():
        try:
            got = fetch(name, url, args.force)
        except httpx.HTTPError as exc:
            print(f"FAIL  {name}: {exc}", file=sys.stderr)
            failed += 1
            continue
        size = (DEST / name).stat().st_size / 1e6
        print(f"{'fetch' if got else 'skip '} {name}  ({size:.1f} MB)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
