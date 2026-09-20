"""RADIO: a best-effort flagship-station stream per team.

There is no free, official per-game audio API. What exists is each team's
flagship station publishing its own web stream — and whether that stream
carries the game (or is geo-fenced, or swaps in talk programming) is up to the
station and the league, game by game. So this is a table, not a resolver:

* ``audio_seed.json`` (in this package) — flagship + stream URL where one was
  verified to open. ``url: null`` means "known station, no stream found".
* ``data/audio_streams.json`` — the operator's own overrides, same shape, merged
  on top. ``scripts/find_stream.py`` helps fill it from radio-browser.info.

NFL+ carries every call without blackouts but is paid and DRM'd; the console
only ever links to it.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .. import paths

SEED = Path(__file__).with_name("audio_seed.json")
OVERRIDES = paths.DATA / "audio_streams.json"
NFL_PLUS = "https://www.nfl.com/plus/"


@dataclass
class Stream:
    team: str
    station: str
    url: str | None
    kind: str | None = None        # "hls" | "direct"

    def to_dict(self) -> dict:
        return asdict(self)


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


class AudioTable:
    def __init__(self, seed: Path = SEED, overrides: Path = OVERRIDES) -> None:
        self.seed, self.overrides = seed, overrides
        self._mtime: tuple[float, ...] = ()
        self._rows: dict[str, Stream] = {}
        self.load()

    def load(self) -> None:
        rows = {**_read(self.seed), **_read(self.overrides)}
        self._rows = {}
        for team, r in rows.items():
            url = r.get("url") or None
            kind = r.get("kind") or ("hls" if url and ".m3u8" in url else "direct" if url else None)
            self._rows[team] = Stream(team, r.get("station") or team, url, kind)
        self._mtime = self._stamp()

    def _stamp(self) -> tuple[float, ...]:
        return tuple(p.stat().st_mtime if p.exists() else 0.0 for p in (self.seed, self.overrides))

    def stream(self, team: str) -> Stream | None:
        if self._stamp() != self._mtime:                   # edited while running: no restart needed
            self.load()
        return self._rows.get(team)

    def for_game(self, game_id: str | None) -> dict | None:
        if not game_id:
            return None
        _, _, away, home = game_id.split("_")
        s = {side: self.stream(t) for side, t in (("home", home), ("away", away))}
        return {"game_id": game_id, "nfl_plus": NFL_PLUS, **{k: (v.to_dict() if v else {"team": t, "station": "", "url": None, "kind": None})
                                                             for (k, v), t in zip(s.items(), (home, away))}}
