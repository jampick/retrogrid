"""PlayerDirectory over the nflverse players file (DESIGN §7).

Identity is the gsis_id. `latest_team` in players.parquet tracks *today*, so a
weekly-roster overlay pins team and jersey number to the slate week. Team
defences are synthesized as "DEF-<TEAM>" players.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

from ..models import Player
from .slate import DEFAULT_NFLVERSE_DIR

TEAM_NAMES: dict[str, str] = {
    "ARI": "Arizona", "ATL": "Atlanta", "BAL": "Baltimore", "BUF": "Buffalo",
    "CAR": "Carolina", "CHI": "Chicago", "CIN": "Cincinnati", "CLE": "Cleveland",
    "DAL": "Dallas", "DEN": "Denver", "DET": "Detroit", "GB": "Green Bay",
    "HOU": "Houston", "IND": "Indianapolis", "JAX": "Jacksonville", "KC": "Kansas City",
    "LA": "LA Rams", "LAC": "LA Chargers", "LV": "Las Vegas", "MIA": "Miami",
    "MIN": "Minnesota", "NE": "New England", "NO": "New Orleans", "NYG": "NY Giants",
    "NYJ": "NY Jets", "PHI": "Philadelphia", "PIT": "Pittsburgh", "SEA": "Seattle",
    "SF": "San Francisco", "TB": "Tampa Bay", "TEN": "Tennessee", "WAS": "Washington",
}
FANTASY_POSITIONS = ("QB", "RB", "WR", "TE", "K")
ESPN_HEADSHOT = "https://a.espncdn.com/i/headshots/nfl/players/full/{espn_id}.png"

_JERSEY_PREFIX = re.compile(r"^(\d{1,2})-")


def def_id(team: str) -> str:
    return f"DEF-{team}"


def derive_short(first: str | None, last: str | None, display: str) -> str:
    """"Travis", "Kelce" -> "T.Kelce" (the form used in play-description prose)."""
    if first and last:
        return f"{first[0]}.{last}"
    parts = display.split()
    return f"{parts[0][0]}.{' '.join(parts[1:])}" if len(parts) > 1 else display


def _str(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None


def _int(v: Any) -> int | None:
    return None if pd.isna(v) else int(v)


class NflversePlayerDirectory:
    def __init__(
        self,
        players_path: Path | str,
        roster_path: Path | str | None = None,
        week: int | None = None,
        min_last_season: int | None = None,
    ) -> None:
        """`roster_path` + `week` overlay that week's team / jersey number.
        `min_last_season` drops long-retired players (keeps lookups unambiguous)."""
        df = pd.read_parquet(players_path)
        df = df[df.gsis_id.notna()]
        if min_last_season is not None:
            df = df[df.last_season >= min_last_season]
        overlay = self._roster_overlay(roster_path, week)

        self._players: dict[str, Player] = {}
        self._rank: dict[str, tuple[int, int, int]] = {}
        self._by_short: dict[str, list[str]] = defaultdict(list)
        for r in df.itertuples(index=False):
            team, number = overlay.get(r.gsis_id, (_str(r.latest_team), _int(r.jersey_number)))
            espn_id = _str(r.espn_id)
            player = Player(
                id=r.gsis_id,
                name=r.display_name,
                short=_str(r.short_name) or derive_short(_str(r.first_name), _str(r.last_name), r.display_name),
                position=_str(r.position) or "",
                team=team or "",
                number=number,
                headshot=ESPN_HEADSHOT.format(espn_id=espn_id) if espn_id else _str(r.headshot),
            )
            self._add(player, (
                player.position in FANTASY_POSITIONS,
                r.gsis_id in overlay or r.status == "ACT",
                _int(r.last_season) or 0,
            ))
        for team, city in TEAM_NAMES.items():
            self._add(Player(id=def_id(team), name=city, short=f"{team} DEF", position="DEF", team=team),
                      (False, True, 0))

    @classmethod
    def from_data_dir(
        cls, week: int | None = None, season: int = 2025,
        data_dir: Path | str = DEFAULT_NFLVERSE_DIR,
    ) -> NflversePlayerDirectory:
        d = Path(data_dir)
        roster = d / f"roster_weekly_{season}.parquet"
        return cls(d / "players.parquet", roster if week is not None and roster.exists() else None,
                   week, min_last_season=season - 1)

    @staticmethod
    def _roster_overlay(path: Path | str | None, week: int | None) -> dict[str, tuple[str | None, int | None]]:
        if path is None or week is None:
            return {}
        ro = pd.read_parquet(path, columns=["gsis_id", "team", "jersey_number", "week"])
        ro = ro[(ro.week == week) & ro.gsis_id.notna()]
        return {r.gsis_id: (_str(r.team), _int(r.jersey_number)) for r in ro.itertuples(index=False)}

    def _add(self, player: Player, rank: tuple[bool, bool, int]) -> None:
        self._players[player.id] = player
        self._rank[player.id] = (int(rank[0]), int(rank[1]), rank[2])
        self._by_short[player.short.lower()].append(player.id)

    # ------------------------------------------------------------- contract

    def player(self, player_id: str) -> Player | None:
        return self._players.get(player_id)

    def by_short(self, short: str, team: str | None = None, *, position: str | None = None) -> Player | None:
        """Resolve prose like "T.Kelce" (or "87-T.Kelce"). `team` disambiguates
        namesakes; a jersey prefix and `position` narrow further. Remaining
        ties go to fantasy positions, then rostered players, then recency."""
        number: int | None = None
        m = _JERSEY_PREFIX.match(short)
        if m:
            number, short = int(m.group(1)), short[m.end():]
        cands = [self._players[i] for i in self._by_short.get(short.strip().lower(), [])]
        if team is not None:
            cands = [p for p in cands if p.team == team]
        for attr, want in (("number", number), ("position", position)):
            if want is not None:
                cands = [p for p in cands if getattr(p, attr) == want] or cands
        if not cands:
            return None
        return max(cands, key=lambda p: (self._rank[p.id], p.id))

    def defence(self, team: str) -> Player | None:
        return self._players.get(def_id(team))

    def __len__(self) -> int:
        return len(self._players)
