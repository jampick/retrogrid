"""The prepared SIM SUNDAY slate (DESIGN §7): `scripts/build_slate.py` writes
it under data/slate/, this module reads it back. No pandas at runtime — the
files are plain JSON / JSONL.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import paths
from ..models import Game, PlayRow

DEFAULT_SLATE_DIR = paths.sim_slate()
DEFAULT_NFLVERSE_DIR = paths.NFLVERSE

SLATE_FILE = "slate.json"
PLAYS_FILE = "plays.jsonl"
POOL_FILE = "draft_pool.json"


@dataclass
class PoolEntry:
    """One draftable player (or team defence) for the synthetic league."""
    id: str
    name: str
    position: str                 # QB RB WR TE K DEF
    team: str
    pre_points: float             # fantasy points in the weeks before the slate
    pre_games: int
    week_points: float            # approximate final points for the slate week
    active: bool                  # recorded a play on slate day


@dataclass
class Slate:
    season: int
    week: int
    date: str                     # "2025-12-14"
    start_utc: str                # wall-clock instant of sim_time 0
    duration: float               # sim seconds until the last game is final
    games: list[Game]             # pre-game state; kickoff on the sim axis
    plays: list[PlayRow]          # sorted by (sim_time, game_id, seq)
    final_scores: dict[str, tuple[int, int]] = field(default_factory=dict)  # game_id -> (home, away)

    @property
    def teams(self) -> set[str]:
        return {t for g in self.games for t in (g.home, g.away)}


def slate_available(slate_dir: Path | str = DEFAULT_SLATE_DIR) -> bool:
    d = Path(slate_dir)
    return (d / SLATE_FILE).exists() and (d / PLAYS_FILE).exists()


def load_slate(slate_dir: Path | str = DEFAULT_SLATE_DIR) -> Slate:
    d = Path(slate_dir)
    meta: dict[str, Any] = json.loads((d / SLATE_FILE).read_text())
    games = [
        Game(id=g["id"], home=g["home"], away=g["away"], kickoff=float(g["kickoff"]))
        for g in meta["games"]
    ]
    finals = {g["id"]: (int(g["home_score"]), int(g["away_score"])) for g in meta["games"]}
    plays: list[PlayRow] = []
    with (d / PLAYS_FILE).open() as fh:
        for line in fh:
            if line.strip():
                plays.append(PlayRow(**json.loads(line)))
    plays.sort(key=lambda p: (p.sim_time, p.game_id, p.seq))
    return Slate(
        season=int(meta["season"]),
        week=int(meta["week"]),
        date=meta["date"],
        start_utc=meta["start_utc"],
        duration=float(meta["duration"]),
        games=games,
        plays=plays,
        final_scores=finals,
    )


def load_pool(slate_dir: Path | str = DEFAULT_SLATE_DIR) -> list[PoolEntry]:
    raw = json.loads((Path(slate_dir) / POOL_FILE).read_text())
    return [PoolEntry(**e) for e in raw]
