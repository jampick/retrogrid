"""Drives, and which of them are inside the 20 (RED ZONE mode).

Derived from landed plays only, so it is the same in SIM and LIVE. A drive is
"in the red zone" from the play that *puts* the ball inside the 20, not from the
first snap there — the switch should beat the snap, not trail it.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import PlayRow

RED_ZONE = 20
_KICKS = ("punt", "field_goal", "kickoff", "extra_point")


@dataclass
class Drive:
    team: str | None = None       # offence; None between drives
    seq: int = 0                  # bumps every time a drive resolves — a lock is (game, seq)
    spot: int | None = None       # yards to the goal line for the *next* snap
    since: float = 0.0            # sim time the ball went inside the 20

    @property
    def red_zone(self) -> bool:
        return self.team is not None and self.spot is not None and self.spot <= RED_ZONE


def resolves(p: PlayRow) -> bool:
    """Did this play end the drive? Scores, turnovers, kicks, a failed 4th down."""
    if p.play_type == "no_play":
        return False
    if p.touchdown or p.interception or p.fumble_lost or p.safety or p.two_point or p.play_type in _KICKS:
        return True
    return bool(p.down == 4 and p.play_type in ("pass", "run") and not p.first_down
                and p.ydstogo is not None and p.yards_gained < p.ydstogo)


class DriveTracker:
    def __init__(self) -> None:
        self.drives: dict[str, Drive] = {}

    def reset(self) -> None:
        self.drives.clear()

    def get(self, game_id: str) -> Drive:
        return self.drives.setdefault(game_id, Drive())

    def ingest(self, p: PlayRow) -> bool:
        """Track one play; True when it resolved its drive."""
        d = self.get(p.game_id)
        if d.team is not None and p.posteam and p.posteam != d.team:      # a change of hands we did not see
            d.seq += 1
            d.team = d.spot = None
        if resolves(p):
            d.seq += 1
            d.team = d.spot = None
            return True
        if p.posteam and p.yardline_100 is not None:
            was = d.red_zone
            d.team = p.posteam
            d.spot = p.yardline_100 - (p.yards_gained if p.play_type != "no_play" else 0)
            if d.red_zone and not was:
                d.since = p.sim_time
        return False
