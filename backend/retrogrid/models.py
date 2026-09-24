"""Shared data contracts (DESIGN §7). Everything crossing a provider seam or
the WebSocket is one of these. Plain dataclasses; `to_dict` is the wire form.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Roster slot order is display order in the LINEUP rail.
SLOTS = ("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF")


class _Wire:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[arg-type]


@dataclass
class Player(_Wire):
    id: str                       # nflverse gsis_id, or "DEF-KC" for a team defence
    name: str                     # "Travis Kelce"
    short: str                    # "T.Kelce" — matches play-description prose
    position: str                 # QB RB WR TE K DEF
    team: str                     # "KC"
    number: int | None = None
    headshot: str | None = None   # source URL (ESPN-hosted)


@dataclass
class RosterSlot(_Wire):
    slot: str                     # one of SLOTS, or "BN"
    player_id: str


@dataclass
class Roster(_Wire):
    team_key: str
    week: int
    slots: list[RosterSlot]

    def starters(self) -> list[RosterSlot]:
        return [s for s in self.slots if s.slot != "BN"]


@dataclass
class FantasyTeam(_Wire):
    key: str                      # "t01"
    name: str                     # "DOOMSDAY_DEVICE"
    owner: str                    # "jampick"


@dataclass
class Matchup(_Wire):
    week: int
    a: str                        # team_key
    b: str                        # team_key


@dataclass
class League(_Wire):
    key: str
    name: str
    scoring_rules: dict[str, float]
    teams: list[FantasyTeam]


@dataclass
class Game(_Wire):
    id: str                       # nflverse game_id "2025_03_KC_BUF"
    home: str
    away: str
    kickoff: float                # seconds from slate start (sim timeline)
    status: str = "pre"           # pre | live | half | final
    quarter: int = 0
    clock: str = "15:00"
    home_score: int = 0
    away_score: int = 0
    possession: str | None = None


@dataclass
class PlayRow(_Wire):
    """One play, in nflverse vocabulary. On the live path the same fields are
    recovered from ESPN prose by the parser (§7); unknowns stay None."""
    play_id: str                  # "<game_id>:<play_id>" — seeds the grammar RNG
    game_id: str
    seq: int                      # order within game
    sim_time: float               # seconds from slate start at which the play *lands*
    quarter: int
    clock: str                    # "8:42"
    down: int | None
    ydstogo: int | None
    yardline_100: int | None      # distance to opponent end zone at snap
    posteam: str | None
    defteam: str | None
    desc: str
    play_type: str                # pass run field_goal extra_point punt kickoff qb_kneel qb_spike no_play
    yards_gained: int = 0
    shotgun: bool = False
    no_huddle: bool = False
    qb_scramble: bool = False
    pass_length: str | None = None      # short | deep
    pass_location: str | None = None    # left | middle | right
    air_yards: float | None = None
    yards_after_catch: float | None = None
    run_location: str | None = None     # left | middle | right
    run_gap: str | None = None          # end | tackle | guard
    complete: bool = False
    touchdown: bool = False
    td_team: str | None = None
    interception: bool = False
    fumble_lost: bool = False
    sack: bool = False
    safety: bool = False
    penalty: bool = False
    first_down: bool = False
    two_point: str | None = None        # success | failure
    field_goal_result: str | None = None  # made | missed | blocked
    extra_point_result: str | None = None  # good | failed | blocked
    kick_distance: int | None = None
    return_yards: int = 0
    passer_id: str | None = None
    receiver_id: str | None = None
    rusher_id: str | None = None
    kicker_id: str | None = None
    interceptor_id: str | None = None
    returner_id: str | None = None
    tackler_ids: list[str] = field(default_factory=list)
    td_player_id: str | None = None
    fumbler_id: str | None = None
    home_score: int = 0                 # score AFTER the play
    away_score: int = 0
    wpa: float | None = None            # win probability added, posteam's view. nflverse only: the reel ranks on it
    epa: float | None = None


@dataclass
class StatDelta(_Wire):
    """Fantasy consequence of one play for one player."""
    player_id: str
    points: float
    stats: dict[str, float]       # {"rec": 1, "rec_yd": 9}
