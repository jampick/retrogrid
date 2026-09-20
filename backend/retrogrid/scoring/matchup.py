"""LINEUP rail + status bar data (DESIGN §4 "Regions").

Slot-by-slot you-vs-them comparison, starter-only team totals, the delta
("the most important number on the screen"), and lead-change detection — the
highest-priority event class of §9. Pure: reads a ScoringState, owns no
points of its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Sequence

from ..models import SLOTS, Matchup, Roster
from .engine import ScoringState, round_points

if TYPE_CHECKING:  # typing only: keeps providers' package import out of the pure core
    from ..providers.base import LeagueProvider


@dataclass
class SlotSide:
    player_id: str | None
    points: float                 # unrounded


@dataclass
class SlotRow:
    slot: str
    you: SlotSide
    them: SlotSide
    losing: bool                  # ⚠ in the rail: you trail in this slot

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "you": {"player_id": self.you.player_id, "points": round_points(self.you.points)},
            "them": {"player_id": self.them.player_id, "points": round_points(self.them.points)},
            "losing": self.losing,
        }


@dataclass
class MatchupSnapshot:
    you_key: str
    them_key: str
    rows: list[SlotRow]
    you_total: float
    them_total: float

    @property
    def delta(self) -> float:
        """you − them; negative means you're behind."""
        return self.you_total - self.them_total

    @property
    def leader(self) -> str | None:
        return _leader(self.you_total, self.them_total)

    def to_dict(self) -> dict[str, Any]:
        you, them = round_points(self.you_total), round_points(self.them_total)
        return {
            "you_key": self.you_key,
            "them_key": self.them_key,
            "rows": [r.to_dict() for r in self.rows],
            "you_total": you,
            "them_total": them,
            "delta": round_points(you - them),
            "leader": self.leader,
        }


@dataclass
class LeadChange:
    leader: str                   # "you" | "them" — who leads now
    previous: str                 # who led before
    you_total: float
    them_total: float

    @property
    def delta(self) -> float:
        return self.you_total - self.them_total

    def to_dict(self) -> dict[str, Any]:
        return {
            "leader": self.leader,
            "previous": self.previous,
            "you_total": round_points(self.you_total),
            "them_total": round_points(self.them_total),
            "delta": round_points(self.delta),
        }


def _leader(you_total: float, them_total: float) -> str | None:
    """Compared at display precision so float dust never fakes a lead."""
    you, them = round_points(you_total), round_points(them_total)
    if you == them:
        return None
    return "you" if you > them else "them"


def starter_total(roster: Roster, state: ScoringState) -> float:
    """Team total: starters only, bench never counts."""
    return sum(state.points(s.player_id) for s in roster.starters())


def _by_slot(roster: Roster) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for s in roster.starters():
        grouped.setdefault(s.slot, []).append(s.player_id)
    return grouped


class MatchupBoard:
    """One viewer's head-to-head. Call :meth:`update` after every scored play
    (cheap: 18 dict lookups) to learn about lead changes."""

    def __init__(self, you: Roster, them: Roster, state: ScoringState,
                 template: Sequence[str] = SLOTS) -> None:
        self.template = tuple(template)
        self.you = you
        self.them = them
        self.state = state
        self._last_leader: str | None = None      # last non-tied leader

    @classmethod
    def for_viewer(cls, provider: LeagueProvider, team_key: str, week: int,
                   state: ScoringState) -> MatchupBoard:
        m = provider.matchup(team_key, week)
        opponent = m.b if m.a == team_key else m.a
        return cls(provider.roster(team_key, week), provider.roster(opponent, week), state,
                   getattr(provider, "slots", SLOTS))

    def set_rosters(self, you: Roster, them: Roster) -> None:
        """Slow-lane roster refresh (§9). Lead memory is kept."""
        self.you, self.them = you, them

    def reset(self) -> None:
        """Forget lead history (pair with ScoringState.reset on sim seek)."""
        self._last_leader = None

    def rows(self) -> list[SlotRow]:
        """Rows in SLOTS display order, same-slot rows adjacent; the k-th RB
        faces the k-th RB. A side missing a slot shows `player_id=None` at 0.
        `template` is the league's lineup shape: a real league may run one RB,
        a superflex, no kicker."""
        mine, theirs = _by_slot(self.you), _by_slot(self.them)
        rows: list[SlotRow] = []
        for slot in dict.fromkeys([*self.template, *mine, *theirs]):
            a, b = mine.get(slot, []), theirs.get(slot, [])
            for k in range(max(self.template.count(slot), len(a), len(b))):
                you = self._side(a[k] if k < len(a) else None)
                them = self._side(b[k] if k < len(b) else None)
                losing = round_points(you.points) < round_points(them.points)
                rows.append(SlotRow(slot, you, them, losing))
        return rows

    def _side(self, player_id: str | None) -> SlotSide:
        return SlotSide(player_id, self.state.points(player_id) if player_id else 0.0)

    def totals(self) -> tuple[float, float]:
        return starter_total(self.you, self.state), starter_total(self.them, self.state)

    def snapshot(self) -> MatchupSnapshot:
        you_total, them_total = self.totals()
        return MatchupSnapshot(self.you.team_key, self.them.team_key, self.rows(),
                               you_total, them_total)

    def update(self) -> LeadChange | None:
        """Detect a lead change since the previous call.

        A change is the lead passing from one side to the other, possibly
        through a tie (you → tied → them fires on the last step). Taking the
        first lead of the week, or re-taking it after a tie, is not a change.
        """
        you_total, them_total = self.totals()
        leader = _leader(you_total, them_total)
        if leader is None:
            return None
        previous, self._last_leader = self._last_leader, leader
        if previous is None or previous == leader:
            return None
        return LeadChange(leader, previous, you_total, them_total)


@dataclass
class MatchupTotals:
    a: str                        # team_key
    b: str
    a_total: float
    b_total: float

    @property
    def leader(self) -> str | None:
        side = _leader(self.a_total, self.b_total)
        return None if side is None else (self.a if side == "you" else self.b)

    def to_dict(self) -> dict[str, Any]:
        a, b = round_points(self.a_total), round_points(self.b_total)
        return {"a": self.a, "b": self.b, "a_total": a, "b_total": b,
                "delta": round_points(a - b), "leader": self.leader}


class LeagueBoard:
    """League-wide variant of the rail (◂MATCHUP · LEAGUE▸): starter totals
    for every matchup of the week."""

    def __init__(self, matchups: list[Matchup], rosters: dict[str, Roster],
                 state: ScoringState) -> None:
        self.matchups = matchups
        self.rosters = rosters
        self.state = state

    @classmethod
    def from_provider(cls, provider: LeagueProvider, week: int,
                      state: ScoringState) -> LeagueBoard:
        matchups = provider.matchups(week)
        rosters = {k: provider.roster(k, week) for m in matchups for k in (m.a, m.b)}
        return cls(matchups, rosters, state)

    def team_total(self, team_key: str) -> float:
        roster = self.rosters.get(team_key)
        return starter_total(roster, self.state) if roster else 0.0

    def totals(self) -> list[MatchupTotals]:
        return [MatchupTotals(m.a, m.b, self.team_total(m.a), self.team_total(m.b))
                for m in self.matchups]
