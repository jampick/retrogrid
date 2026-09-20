"""Local fantasy scoring engine (DESIGN §9 "Points").

ESPN/nflverse play rows × Yahoo scoring rules → an *optimistic instant delta*
per player, per play. Pure: no I/O, no pandas, no provider imports. Yahoo's
official number reconciles drift elsewhere (slow lane); nothing here knows
about it.

Design: every play is reduced to a per-player *stat line* (``{"rec": 1,
"rec_yd": 9}``); points are the dot product of that line with the flat rules
dict. Stat keys that are not scoring rules (``pass_att``, ``tgt`` …) ride along
for the ACTIVE CARD (§4) and score nothing. The one exception is the team
defence points-allowed tier, which is a function of the running game score and
therefore lives in the stateful :class:`ScoringState`.

Accumulation is never rounded; use :func:`round_points` at presentation
boundaries only.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..models import Game, PlayRow, StatDelta

Rules = Mapping[str, float]

# Yahoo half-PPR defaults. Mirrors the stub league's `scoring_rules`.
DEFAULT_RULES: dict[str, float] = {
    "pass_yd": 0.04, "pass_td": 4, "pass_int": -1,
    "rush_yd": 0.1, "rush_td": 6,
    "rec": 0.5, "rec_yd": 0.1, "rec_td": 6,
    "ret_td": 6, "two_pt": 2, "fum_lost": -2,
    "fg_0_39": 3, "fg_40_49": 4, "fg_50": 5, "xp": 1,
    "def_sack": 1, "def_int": 2, "def_fum_rec": 2, "def_td": 6,
    "def_safety": 2, "def_block": 2,
    "def_pa_0": 10, "def_pa_1_6": 7, "def_pa_7_13": 4, "def_pa_14_20": 1,
    "def_pa_21_27": 0, "def_pa_28_34": -1, "def_pa_35": -4,
}

# Stat keys scored through another rule when the league has no rule of their own.
_RULE_FALLBACK = {"off_fum_ret_td": "ret_td"}

# Upper bound (inclusive) of each points-allowed tier, in order.
_PA_TIERS: tuple[tuple[int, str], ...] = (
    (0, "def_pa_0"), (6, "def_pa_1_6"), (13, "def_pa_7_13"),
    (20, "def_pa_14_20"), (27, "def_pa_21_27"), (34, "def_pa_28_34"),
)
_PA_TOP = "def_pa_35"

_RUN_TYPES = frozenset({"run", "qb_kneel"})
_KICK_RETURN_TYPES = frozenset({"kickoff", "punt"})


def round_points(points: float) -> float:
    """Presentation-boundary rounding (2 dp). Never feed the result back into
    an accumulator."""
    rounded = round(points + 0.0, 2)
    return 0.0 if rounded == 0 else rounded


def def_id(team: str) -> str:
    """Player id of a team defence (models.Player.id convention)."""
    return f"DEF-{team}"


def pa_tier(points_allowed: int) -> str:
    """Rule key of the points-allowed tier for a DEF that has conceded
    `points_allowed`."""
    for upper, key in _PA_TIERS:
        if points_allowed <= upper:
            return key
    return _PA_TOP


def fg_tier(distance: int) -> str:
    if distance >= 50:
        return "fg_50"
    return "fg_40_49" if distance >= 40 else "fg_0_39"


def stat_points(stats: Mapping[str, float], rules: Rules) -> float:
    """Dot product of a stat line with the rules. Unknown keys score 0."""
    total = 0.0
    for key, value in stats.items():
        weight = rules.get(key)
        if weight is None and key in _RULE_FALLBACK:
            weight = rules.get(_RULE_FALLBACK[key])
        if weight:
            total += weight * value
    return total


def is_nullified(play: PlayRow) -> bool:
    """A play that never happened: nflverse `no_play`, or a live-path row whose
    prose says the penalty wiped it out. A penalty flag alone does NOT nullify
    (declined / tacked-on penalties leave the stats standing)."""
    if play.play_type == "no_play":
        return True
    return play.penalty and "no play" in play.desc.lower()


class _Lines:
    """Per-player stat lines for one play, in first-touch order."""

    def __init__(self) -> None:
        self.lines: dict[str, dict[str, float]] = {}

    def add(self, player_id: str | None, key: str, value: float = 1) -> None:
        if not player_id:
            return
        line = self.lines.setdefault(player_id, {})
        line[key] = line.get(key, 0) + value


def _scoring_team(play: PlayRow) -> str | None:
    """Who scored the TD. `td_team` wins; when the live parser could not
    recover it, a turnover play is assumed to be a defensive score."""
    if play.td_team:
        return play.td_team
    if play.interception or play.fumble_lost:
        return play.defteam
    return play.posteam


def _fg_distance(play: PlayRow) -> int:
    if play.kick_distance is not None:
        return play.kick_distance
    # LOS + 10 (end zone) + 7 (snap depth).
    return (play.yardline_100 or 0) + 17


def _fumble_recovering_team(play: PlayRow) -> str | None:
    """Team whose DEF is credited with the recovery of a lost fumble.
    nflverse: posteam is the receiving team on kickoffs but the *punting* team
    on punts, so a returner's muff on a punt is recovered by posteam."""
    if play.play_type == "punt" and play.returner_id and play.fumbler_id in (None, play.returner_id):
        return play.posteam
    return play.defteam


def play_stats(play: PlayRow) -> dict[str, dict[str, float]]:
    """Reduce one play to per-player stat lines (rule-independent)."""
    out = _Lines()
    if is_nullified(play):
        return out.lines
    kind = play.play_type
    off, dfn = play.posteam, play.defteam

    if play.two_point is not None:
        # Conversion attempts carry no yardage or TD credit. Yahoo awards the
        # 2 points to every offensive party involved.
        if play.two_point == "success":
            if kind == "pass":
                out.add(play.passer_id, "two_pt")
                out.add(play.receiver_id, "two_pt")
            else:
                out.add(play.rusher_id or play.passer_id, "two_pt")
        return out.lines

    off_td = play.touchdown and _scoring_team(play) == off
    primary: str | None = None      # the offensive player who'd own the TD

    if kind == "pass":
        if play.sack:
            if dfn:
                out.add(def_id(dfn), "def_sack")
        else:
            out.add(play.passer_id, "pass_att")
            out.add(play.receiver_id, "tgt")
            if play.complete:
                primary = play.receiver_id
                out.add(play.passer_id, "pass_cmp")
                out.add(play.passer_id, "pass_yd", play.yards_gained)
                out.add(play.receiver_id, "rec")
                out.add(play.receiver_id, "rec_yd", play.yards_gained)
            if play.interception:
                out.add(play.passer_id, "pass_int")
                if dfn:
                    out.add(def_id(dfn), "def_int")
    elif kind in _RUN_TYPES:
        primary = play.rusher_id
        out.add(play.rusher_id, "rush_att")
        out.add(play.rusher_id, "rush_yd", play.yards_gained)
    elif kind == "field_goal":
        out.add(play.kicker_id, "fg_att")
        if play.field_goal_result == "made":
            out.add(play.kicker_id, fg_tier(_fg_distance(play)))
    elif kind == "extra_point":
        out.add(play.kicker_id, "xp_att")
        if play.extra_point_result == "good":
            out.add(play.kicker_id, "xp")

    blocked = (
        play.field_goal_result == "blocked"
        or play.extra_point_result == "blocked"
        or (kind == "punt" and "blocked" in play.desc.lower())
    )
    if blocked and dfn:
        out.add(def_id(dfn), "def_block")

    if play.fumble_lost:
        out.add(play.fumbler_id or (primary if kind not in _KICK_RETURN_TYPES else play.returner_id), "fum_lost")
        recovering = _fumble_recovering_team(play)
        if recovering:
            out.add(def_id(recovering), "def_fum_rec")

    if play.safety and dfn:
        out.add(def_id(dfn), "def_safety")

    if play.touchdown:
        scorer_team = _scoring_team(play)
        if kind in _KICK_RETURN_TYPES:
            # D/ST owns every special-teams TD; the returner also gets his own
            # ret_td when the receiving team ran it back.
            receiving = off if kind == "kickoff" else dfn
            if scorer_team:
                out.add(def_id(scorer_team), "def_td")
            if scorer_team == receiving:
                out.add(play.td_player_id or play.returner_id, "ret_td")
        elif off_td and kind in ("pass", "run", "qb_kneel"):
            scorer = play.td_player_id or primary
            if scorer == primary and primary is not None:
                if kind == "pass":
                    out.add(play.passer_id, "pass_td")
                    out.add(primary, "rec_td")
                else:
                    out.add(primary, "rush_td")
            else:
                # Teammate fell on a fumble in the end zone: no pass/rush TD.
                out.add(scorer, "off_fum_ret_td")
        elif off_td:
            # Fake/botched kick run in by the kicking team: credit the scorer.
            out.add(play.td_player_id, "off_fum_ret_td")
        elif scorer_team:
            out.add(def_id(scorer_team), "def_td")

    return out.lines


def score_play(play: PlayRow, rules: Rules = DEFAULT_RULES) -> list[StatDelta]:
    """Fantasy consequences of ONE play, one StatDelta per involved player.

    Stateless: excludes the DEF points-allowed tier, which depends on the
    running score — :meth:`ScoringState.apply` adds that. Deltas may carry 0
    points (an incompletion is still a target)."""
    return [
        StatDelta(player_id=pid, points=stat_points(line, rules), stats=line)
        for pid, line in play_stats(play).items()
    ]


def _teams_from_game_id(game_id: str) -> tuple[str, str] | None:
    """nflverse ids read ``<season>_<week>_<away>_<home>``."""
    parts = game_id.split("_")
    if len(parts) >= 4 and parts[-1] and parts[-2]:
        return parts[-1], parts[-2]
    return None


def _merge(deltas: Iterable[StatDelta]) -> list[StatDelta]:
    """One StatDelta per player, first-appearance order. Inputs are not mutated."""
    merged: dict[str, StatDelta] = {}
    for d in deltas:
        held = merged.get(d.player_id)
        if held is None:
            merged[d.player_id] = StatDelta(d.player_id, d.points, dict(d.stats))
            continue
        held.points += d.points
        for key, value in d.stats.items():
            held.stats[key] = held.stats.get(key, 0) + value
    return list(merged.values())


class ScoringState:
    """Slate-wide accumulator: per-player points and stat lines, plus the
    per-game score needed for DEF points-allowed tiers (DESIGN §9).

    Points allowed = the opponent's total score, whatever unit conceded it
    (pick-sixes thrown by the offence included). A DEF opens at ``def_pa_0``
    via :meth:`open_game` and bleeds the tier *difference* on the scoring play.

    Deterministic in its inputs, so sim seek is `rebuild(games, plays)`.
    Re-applying a play_id is a no-op, which makes live re-polls idempotent.
    """

    def __init__(self, rules: Rules | None = None) -> None:
        self.rules: dict[str, float] = dict(DEFAULT_RULES if rules is None else rules)
        self._points: dict[str, float] = {}
        self._stats: dict[str, dict[str, float]] = {}
        self._games: dict[str, tuple[str, str]] = {}      # game_id → (home, away)
        self._scores: dict[str, tuple[int, int]] = {}     # game_id → (home, away)
        self._seen: set[str] = set()
        self._base: dict[str, float] = {}                 # survives reset: not ours to recompute

    def set_base(self, points: Mapping[str, float]) -> None:
        """Official points for players whose game is outside the slate
        (Thursday's starters on a Sunday): they count, but no play of ours
        will ever produce them."""
        self._base = dict(points)

    # -- lifecycle ---------------------------------------------------------
    def reset(self) -> None:
        self._points.clear()
        self._stats.clear()
        self._games.clear()
        self._scores.clear()
        self._seen.clear()

    def rebuild(self, games: Iterable[Game], plays: Iterable[PlayRow]) -> None:
        """Reset, then replay (sim seek). `plays` must be in landing order."""
        self.reset()
        for game in games:
            self.open_game(game)
        for play in plays:
            self.apply(play)

    def open_game(self, game: Game) -> list[StatDelta]:
        """Register a game at 0–0 and hand both DEFs their shutout points.
        Idempotent. Always opens at 0–0 (not `game.home_score`) so a replay
        from the first play walks the tiers correctly."""
        return self._open(game.id, game.home, game.away)

    def _open(self, game_id: str, home: str, away: str) -> list[StatDelta]:
        if game_id in self._games:
            return []
        self._games[game_id] = (home, away)
        self._scores[game_id] = (0, 0)
        base = self.rules.get(pa_tier(0), 0.0)
        deltas = [
            StatDelta(player_id=def_id(team), points=base, stats={"pts_allowed": 0})
            for team in (home, away)
        ]
        self._accumulate(deltas)
        return deltas

    # -- per play ----------------------------------------------------------
    def apply(self, play: PlayRow) -> list[StatDelta]:
        """Score a play, fold it in, return the deltas (one per player, PA tier
        changes merged into the same DEF's delta)."""
        if play.play_id in self._seen:
            return []
        self._seen.add(play.play_id)

        deltas = score_play(play, self.rules)
        opening: list[StatDelta] = []
        if play.game_id not in self._games:
            teams = _teams_from_game_id(play.game_id)
            if teams:
                opening = self._open(play.game_id, *teams)
        deltas = _merge(deltas + self._points_allowed(play))
        self._accumulate(deltas)
        # A lazily-opened game's +10s (already accumulated) are real point
        # changes; report them, still one delta per player.
        return _merge(deltas + opening) if opening else deltas

    def _points_allowed(self, play: PlayRow) -> list[StatDelta]:
        teams = self._games.get(play.game_id)
        if teams is None:
            return []
        home, away = teams
        old_home, old_away = self._scores[play.game_id]
        new_home, new_away = play.home_score, play.away_score
        self._scores[play.game_id] = (new_home, new_away)
        out: list[StatDelta] = []
        # The home DEF concedes the away score, and vice versa.
        for team, old, new in ((home, old_away, new_away), (away, old_home, new_home)):
            if new == old:
                continue
            change = self.rules.get(pa_tier(new), 0.0) - self.rules.get(pa_tier(old), 0.0)
            out.append(StatDelta(def_id(team), change, {"pts_allowed": new - old}))
        return out

    def _accumulate(self, deltas: Iterable[StatDelta]) -> None:
        for d in deltas:
            self._points[d.player_id] = self._points.get(d.player_id, 0.0) + d.points
            line = self._stats.setdefault(d.player_id, {})
            for key, value in d.stats.items():
                line[key] = line.get(key, 0) + value

    # -- reads ---------------------------------------------------------------
    def points(self, player_id: str) -> float:
        """Unrounded running total; 0.0 for a player who hasn't registered."""
        return self._points.get(player_id, 0.0) + self._base.get(player_id, 0.0)

    def statline(self, player_id: str) -> dict[str, float]:
        return dict(self._stats.get(player_id, {}))

    def all_points(self) -> dict[str, float]:
        return {pid: self.points(pid) for pid in {*self._points, *self._base}}

    def game_score(self, game_id: str) -> tuple[int, int] | None:
        """(home, away) as last seen, or None for an unopened game."""
        return self._scores.get(game_id)
