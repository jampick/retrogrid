#!/usr/bin/env python3
"""Find a radio station's stream and pin it to a team's RADIO slot.

    scripts/find_stream.py "sports hub"              # search radio-browser.info, list candidates
    scripts/find_stream.py "sports hub" --set NE 2   # write candidate #2 to data/audio_streams.json
    scripts/find_stream.py --url https://… --set KC --station "106.5 The Wolf"

Overrides in data/audio_streams.json win over the shipped table
(backend/retroffb/providers/audio_seed.json) and are picked up without a restart.
Station web streams are best effort: many swap in other programming during games.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OVERRIDES = ROOT / "data" / "audio_streams.json"
API = "https://de1.api.radio-browser.info/json/stations/search"
UA = "RetroFFB/0.1 (personal hobby app)"


def search(q: str) -> list[dict]:
    r = httpx.get(API, params={"name": q, "countrycode": "US", "hidebroken": "true", "limit": 12, "order": "clickcount", "reverse": "true"},
                  headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    return r.json()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?")
    ap.add_argument("--set", nargs="+", metavar=("TEAM", "N"), help="team abbr, and which candidate (default 1)")
    ap.add_argument("--url"); ap.add_argument("--station")
    a = ap.parse_args()
    rows = search(a.query) if a.query else []
    for i, s in enumerate(rows, 1):
        print(f"{i:2d}. {s['name'].strip()[:44]:44s} {s.get('state', '')[:14]:14s} {s.get('codec', ''):5s} {s.get('url_resolved') or s.get('url')}")
    if not a.set:
        return 0
    team, n = a.set[0].upper(), int(a.set[1]) if len(a.set) > 1 else 1
    url = a.url or (rows[n - 1].get("url_resolved") or rows[n - 1]["url"] if rows else None)
    if not url:
        print("nothing to set: give a query with results, or --url", file=sys.stderr)
        return 1
    table = json.loads(OVERRIDES.read_text()) if OVERRIDES.exists() else {}
    table[team] = {"station": a.station or (rows[n - 1]["name"].strip() if rows and not a.url else team), "url": url,
                   "kind": "hls" if ".m3u8" in url else "direct"}
    OVERRIDES.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDES.write_text(json.dumps(table, indent=1))
    print(f"{team} -> {table[team]['station']}  {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
