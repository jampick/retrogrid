"""Threat ranking (DESIGN §9 "Threat score", §4 THREAT BOARD / FEEDS / alerts).

    threat = |Δ fantasy points|
           × relevance      (2.0 your/opponent starter, 1.0 league-wide, 0 else)
           × recency_decay
           + lead_change_bonus

Ranked by impact, not chronology. A :class:`ThreatBoard` is per-viewer; the
expensive-ish, viewer-independent work (league index, headlines) is done once
in :class:`LeagueIndex` / :class:`ThreatHub`, so feeding 12 boards from one
play stream costs a few dict lookups per delta per viewer. All times are
sim-seconds (PlayRow.sim_time). Pure: no I/O.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..models import Matchup, PlayRow, Roster, StatDelta
from .engine import round_points

if TYPE_CHECKING:  # typing only: keeps providers' package import out of the pure core
    from ..providers.base import LeagueProvider, PlayerDirectory

RELEVANCE_MATCHUP = 2.0           # your / opponent starter
RELEVANCE_LEAGUE = 1.0            # rostered anywhere in the league
HALF_LIFE = 600.0                 # 10 sim-minutes
LEAD_CHANGE_BONUS = 50.0          # dwarfs any single play (|Δ|×2 rarely > 25)
LEAD_CHANGE_HALF_LIFE = 1800.0    # lead changes linger three times longer
ALERT_THRESHOLD = 4.0             # |Δ| that earns a banner (§4 alert-and-tap)
STATUS_THRESHOLD = 0.5            # net decayed pts before a FEEDS marker shows
HEADLINE_MAX = 28
_EPS = 1e-9


@dataclass
class ThreatEvent:
    play_id: str
    game_id: str
    player_id: str
    delta_points: float           # unrounded, signed
    side: str                     # "you" | "them" | "league"
    kind: str                     # "help" | "hurt" (from the viewer's chair)
    sim_time: float
    lead_change: bool
    headline: str                 # ≤ 28 chars, uppercase

    def to_dict(self) -> dict[str, Any]:
        return {
            "play_id": self.play_id, "game_id": self.game_id,
            "player_id": self.player_id,
            "delta_points": round_points(self.delta_points),
            "side": self.side, "kind": self.kind, "sim_time": self.sim_time,
            "lead_change": self.lead_change, "headline": self.headline,
        }


# -- shared, viewer-independent ------------------------------------------------

class LeagueIndex:
    """Who matters to whom, built once per roster refresh and shared by every
    viewer's board."""

    def __init__(self, rosters: Iterable[Roster], matchups: Iterable[Matchup]) -> None:
        self.starters: dict[str, frozenset[str]] = {}
        rostered: set[str] = set()
        for r in rosters:
            self.starters[r.team_key] = frozenset(s.player_id for s in r.starters())
            rostered.update(s.player_id for s in r.slots)
        self.rostered: frozenset[str] = frozenset(rostered)
        self.opponent: dict[str, str] = {}
        for m in matchups:
            self.opponent[m.a] = m.b
            self.opponent[m.b] = m.a

    @classmethod
    def from_provider(cls, provider: LeagueProvider, week: int) -> LeagueIndex:
        matchups = provider.matchups(week)
        keys = dict.fromkeys(k for m in matchups for k in (m.a, m.b))
        return cls([provider.roster(k, week) for k in keys], matchups)

    def side(self, viewer: str, player_id: str) -> str | None:
        """"you" | "them" | "league", or None for relevance 0."""
        if player_id in self.starters.get(viewer, ()):
            return "you"
        if player_id in self.starters.get(self.opponent.get(viewer, ""), ()):
            return "them"
        return "league" if player_id in self.rostered else None


def _label(player_id: str, directory: PlayerDirectory | None) -> str:
    """"T.Kelce" → KELCE; "DEF-KC" → KC DEF."""
    if player_id.startswith("DEF-"):
        return f"{player_id[4:]} DEF"
    player = directory.player(player_id) if directory else None
    if player is None:
        return player_id.upper()
    short = player.short or player.name
    return (short.split(".", 1)[-1] if "." in short else short.split()[-1]).strip().upper()


def _action(play: PlayRow, stats: Mapping[str, float]) -> str:
    """What the player did, most consequential fact first."""
    yd = play.yards_gained
    if "pass_td" in stats:
        return f"{yd}-YD TD PASS"
    if "rec_td" in stats:
        return f"{yd}-YD TD CATCH"
    if "rush_td" in stats:
        return f"{yd}-YD TD RUN"
    if "ret_td" in stats:
        return "RETURN TD"
    if "off_fum_ret_td" in stats:
        return "FUMBLE REC TD"
    if "def_td" in stats:
        if "def_int" in stats:
            return "PICK-SIX"
        return "SCOOP-SIX" if "def_fum_rec" in stats else "RETURN TD"
    if "two_pt" in stats:
        return "2-PT CONV"
    if "pass_int" in stats:
        return "INTERCEPTED"
    if "fum_lost" in stats:
        return "FUMBLE LOST"
    for key in ("fg_0_39", "fg_40_49", "fg_50"):
        if key in stats:
            dist = play.kick_distance
            return f"{dist}-YD FG" if dist is not None else "FG GOOD"
    if "xp" in stats:
        return "XP GOOD"
    for key, text in (("def_safety", "SAFETY"), ("def_int", "INT"),
                      ("def_fum_rec", "FUMBLE REC"), ("def_block", "BLOCKED KICK"),
                      ("def_sack", "SACK")):
        if key in stats:
            return text
    if "pts_allowed" in stats:
        allowed = int(stats["pts_allowed"])
        return f"ALLOWS {allowed}" if allowed else "GAME OPEN"
    if "rec" in stats:
        return f"{yd}-YD CATCH"
    if "rush_yd" in stats:
        return f"{yd}-YD SCRAMBLE" if play.qb_scramble else f"{yd}-YD RUN"
    if "pass_yd" in stats:
        return f"{yd}-YD PASS"
    return play.play_type.upper().replace("_", " ")


def headline(play: PlayRow, delta: StatDelta,
             directory: PlayerDirectory | None = None) -> str:
    """"ALLEN 22-YD TD PASS". The name gives way before the action does."""
    action = _action(play, delta.stats)[:HEADLINE_MAX]
    room = HEADLINE_MAX - len(action) - 1
    name = _label(delta.player_id, directory)[:max(room, 0)].rstrip()
    return f"{name} {action}" if name else action


# -- per viewer ------------------------------------------------------------------

class ThreatBoard:
    """One viewer's ranked event memory.

    Server loop, per play::

        deltas = state.apply(play)
        lead = matchup_board.update()
        events = threat_board.ingest(play, deltas, now, lead_change=lead is not None)
        banners = [e for e in events if threat_board.should_alert(e)]
    """

    def __init__(self, viewer_team_key: str, index: LeagueIndex,
                 directory: PlayerDirectory | None = None, *,
                 half_life: float = HALF_LIFE,
                 lead_change_bonus: float = LEAD_CHANGE_BONUS,
                 lead_change_half_life: float = LEAD_CHANGE_HALF_LIFE,
                 alert_threshold: float = ALERT_THRESHOLD,
                 max_events: int = 256) -> None:
        self.viewer = viewer_team_key
        self.index = index
        self.directory = directory
        self.half_life = half_life
        self.lead_change_bonus = lead_change_bonus
        self.lead_change_half_life = lead_change_half_life
        self.alert_threshold = alert_threshold
        self._events: deque[ThreatEvent] = deque(maxlen=max_events)

    def reset(self) -> None:
        self._events.clear()

    def set_index(self, index: LeagueIndex) -> None:
        """Roster refresh. Past events keep the side they had when they landed."""
        self.index = index

    # -- write -------------------------------------------------------------
    def ingest(self, play: PlayRow, deltas: Iterable[StatDelta], now: float | None = None,
               lead_change: bool = False,
               headlines: Mapping[str, str] | None = None) -> list[ThreatEvent]:
        """Record this play's relevant, non-zero deltas; returns the new events.

        `now` is accepted for call-site symmetry; events are stamped with
        `play.sim_time` so a replayed slate ranks identically. `lead_change`
        (from MatchupBoard.update) is pinned on the play's biggest
        matchup-side event. `headlines` lets ThreatHub share strings across
        viewers."""
        del now
        fresh: list[ThreatEvent] = []
        for d in deltas:
            if abs(d.points) < _EPS:
                continue
            side = self.index.side(self.viewer, d.player_id)
            if side is None:
                continue
            good = d.points < 0 if side == "them" else d.points > 0
            helps = good and side != "league"
            text = (headlines or {}).get(d.player_id) or headline(play, d, self.directory)
            fresh.append(ThreatEvent(
                play_id=play.play_id, game_id=play.game_id, player_id=d.player_id,
                delta_points=d.points, side=side, kind="help" if helps else "hurt",
                sim_time=play.sim_time, lead_change=False, headline=text))
        if lead_change:
            matchup = [e for e in fresh if e.side != "league"]
            if matchup:
                max(matchup, key=lambda e: abs(e.delta_points)).lead_change = True
        self._events.extend(fresh)
        return fresh

    # -- score ---------------------------------------------------------------
    @staticmethod
    def relevance(event: ThreatEvent) -> float:
        return RELEVANCE_LEAGUE if event.side == "league" else RELEVANCE_MATCHUP

    def _decay(self, event: ThreatEvent, now: float, half_life: float) -> float:
        age = max(now - event.sim_time, 0.0)
        return 0.5 ** (age / half_life)

    def score(self, event: ThreatEvent, now: float) -> float:
        """The §9 formula. The lead-change bonus decays too, but slowly."""
        s = abs(event.delta_points) * self.relevance(event) * self._decay(event, now, self.half_life)
        if event.lead_change:
            s += self.lead_change_bonus * self._decay(event, now, self.lead_change_half_life)
        return s

    # -- read ----------------------------------------------------------------
    def top(self, n: int, now: float, scope: str = "matchup") -> list[ThreatEvent]:
        """Best `n` events. scope="matchup": your and their starters only;
        scope="league": every rostered player (matchup events still weigh 2×)."""
        if scope not in ("matchup", "league"):
            raise ValueError(f"unknown scope {scope!r}")
        pool = [e for e in self._events
                if e.sim_time <= now and (scope == "league" or e.side != "league")]
        pool.sort(key=lambda e: (self.score(e, now), e.sim_time), reverse=True)
        return pool[:n]

    def per_game_status(self, now: float) -> dict[str, str | None]:
        """FEEDS rail markers: is each game currently helping or hurting the
        viewer? Net decayed matchup impact per game; None when it's a wash."""
        net: dict[str, float] = {}
        for e in self._events:
            if e.side == "league" or e.sim_time > now:
                continue
            impact = abs(e.delta_points) * self._decay(e, now, self.half_life)
            net[e.game_id] = net.get(e.game_id, 0.0) + (impact if e.kind == "help" else -impact)
        return {g: ("help" if v >= STATUS_THRESHOLD else "hurt" if v <= -STATUS_THRESHOLD else None)
                for g, v in net.items()}

    def should_alert(self, event: ThreatEvent) -> bool:
        """Banner + synth sting (§4): any lead change, or a matchup player
        moving ≥ `alert_threshold` points on one play."""
        if event.lead_change:
            return True
        return event.side != "league" and abs(event.delta_points) >= self.alert_threshold - _EPS


class ThreatHub:
    """Fan one play stream out to every viewer's board, building each headline
    once."""

    def __init__(self, index: LeagueIndex, directory: PlayerDirectory | None = None,
                 **board_options: float) -> None:
        self.index = index
        self.directory = directory
        self._options = board_options
        self.boards: dict[str, ThreatBoard] = {}

    def board(self, team_key: str) -> ThreatBoard:
        """Get-or-create; viewers cost nothing until they connect."""
        b = self.boards.get(team_key)
        if b is None:
            b = self.boards[team_key] = ThreatBoard(
                team_key, self.index, self.directory, **self._options)  # type: ignore[arg-type]
        return b

    def set_index(self, index: LeagueIndex) -> None:
        self.index = index
        for b in self.boards.values():
            b.set_index(index)

    def reset(self) -> None:
        for b in self.boards.values():
            b.reset()

    def ingest(self, play: PlayRow, deltas: Iterable[StatDelta], now: float | None = None,
               lead_changes: Iterable[str] = ()) -> dict[str, list[ThreatEvent]]:
        """`lead_changes`: team_keys whose MatchupBoard.update() just fired.
        Returns the new events per viewer (empty lists omitted)."""
        live = [d for d in deltas
                if abs(d.points) >= _EPS and d.player_id in self.index.rostered]
        if not live:
            return {}
        texts = {d.player_id: headline(play, d, self.directory) for d in live}
        flipped = set(lead_changes)
        out: dict[str, list[ThreatEvent]] = {}
        for key, b in self.boards.items():
            events = b.ingest(play, live, now, lead_change=key in flipped, headlines=texts)
            if events:
                out[key] = events
        return out
