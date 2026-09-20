"""Synthetic 12-team league (DESIGN §7: "real plays, fake league").

Deterministic from a seed: team names, a snake draft over real season
production, lineups, and the week's pairings. Stands in for Yahoo until
Phase 8; implements LeagueProvider exactly.
"""
from __future__ import annotations

import random
from collections import Counter
from pathlib import Path
from typing import Sequence

from ..models import SLOTS, FantasyTeam, League, Matchup, PlayRow, Roster, RosterSlot
from .base import PlayerDirectory
from .slate import DEFAULT_SLATE_DIR, PoolEntry, load_pool, load_slate

YAHOO_HALF_PPR: dict[str, float] = {
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

_PA_TIERS: tuple[tuple[int, str], ...] = (
    (0, "def_pa_0"), (6, "def_pa_1_6"), (13, "def_pa_7_13"),
    (20, "def_pa_14_20"), (27, "def_pa_21_27"), (34, "def_pa_28_34"),
)


def pa_tier_key(points_allowed: int) -> str:
    """Scoring-rule key for a defence's points-allowed tier."""
    for ceiling, key in _PA_TIERS:
        if points_allowed <= ceiling:
            return key
    return "def_pa_35"


N_TEAMS = 12
BENCH = 5
ROUNDS = len(SLOTS) + BENCH
USER_TEAM = "t01"
USER_OWNER = "jampick"
FLEX = ("RB", "WR", "TE")

TEAM_NAME_POOL = (
    "DOOMSDAY_DEVICE", "NEON_GRIDIRON", "BLACK_ICE", "NULL_POINTER", "CHROME_REAPERS",
    "GHOST_PROTOCOL", "SYNTH_WAVE_RIDERS", "KERNEL_PANIC", "NIGHT_CITY_BLITZ", "DEAD_PIXEL",
    "VOLTAGE_SPIKE", "ROGUE_DAEMON", "ZERO_DAY_EXPLOIT", "STATIC_BLOOM", "WETWARE_KINGS",
    "HEX_DUMP", "OVERCLOCKED", "SEGFAULT_SAINTS",
)
OWNER_POOL = (
    "n30n_wolf", "zerocool", "acid_burn", "gl1tch", "packet_rat", "vapor_kid", "r00tkit",
    "m0nolith", "deckard", "crashoverride", "sprawl_rat", "wintermute", "hiro_p", "molly_m",
)

# Draft shape: roster caps, replacement rank per position, earliest K/DEF round.
POSITION_CAPS = {"QB": 2, "RB": 5, "WR": 5, "TE": 2, "K": 1, "DEF": 1}
STARTER_MIN = Counter(s for s in SLOTS if s != "FLEX")
REPLACEMENT_RANK = {"QB": 13, "RB": 34, "WR": 38, "TE": 13, "K": 12, "DEF": 12}
SPECIALIST_ROUND = 12
MIN_GAMES = 8                  # ppg denominator floor — damps small samples
DRAFT_NOISE = 1.2              # ppg; gives each seed a different draft
NEED_BONUS = 1.5               # ppg nudge toward an open starter slot
BACKUP_PENALTY = 5.0           # ppg discount on a second QB / TE
CLOSE_MARGIN = 10.0            # a "close" matchup, in projected final points


def _value(e: PoolEntry) -> float:
    return e.pre_points / max(e.pre_games, MIN_GAMES)


class SyntheticLeagueProvider:
    def __init__(
        self,
        seed: int,
        week: int,
        pool: Sequence[PoolEntry],
        directory: PlayerDirectory | None = None,
        plays: Sequence[PlayRow] | None = None,
    ) -> None:
        """`pool` is the draftable universe (see scripts/build_slate.py).
        `directory`, if given, drops pool entries it cannot resolve. `plays`
        lets the matchup picker prefer an opponent with lead changes."""
        self.seed = seed
        self.week = week
        rng = random.Random(seed)
        entries = [e for e in pool if directory is None or directory.player(e.id) is not None]
        self._pool = {e.id: e for e in self._lively(entries)}

        names = rng.sample(TEAM_NAME_POOL, N_TEAMS)
        owners = [USER_OWNER, *rng.sample(OWNER_POOL, N_TEAMS - 1)]
        self._teams = [FantasyTeam(key=f"t{i + 1:02d}", name=names[i], owner=owners[i]) for i in range(N_TEAMS)]
        self._league = League(
            key=f"synthetic.l.{seed}", name="SIM_SUNDAY_LEAGUE",
            scoring_rules=dict(YAHOO_HALF_PPR), teams=self._teams,
        )
        picks = self._draft(rng)
        self._rosters = {key: self._set_lineup(key, ids) for key, ids in picks.items()}
        self._matchups = self._pair(rng, plays or ())

    @classmethod
    def from_slate(
        cls, seed: int = 1, slate_dir: Path | str = DEFAULT_SLATE_DIR,
        directory: PlayerDirectory | None = None,
    ) -> SyntheticLeagueProvider:
        slate = load_slate(slate_dir)
        return cls(seed, slate.week, load_pool(slate_dir), directory, slate.plays)

    # ------------------------------------------------------------- contract

    def league(self) -> League:
        return self._league

    def roster(self, team_key: str, week: int) -> Roster:
        r = self._rosters[team_key]
        return Roster(team_key=r.team_key, week=week, slots=list(r.slots))

    def matchup(self, team_key: str, week: int) -> Matchup:
        for m in self.matchups(week):
            if team_key in (m.a, m.b):
                return m
        raise KeyError(team_key)

    def matchups(self, week: int) -> list[Matchup]:
        return [Matchup(week=week, a=m.a, b=m.b) for m in self._matchups]

    def official_points(self, team_key: str, week: int) -> dict[str, float]:
        return {}

    # --------------------------------------------------------------- extras

    def projected_final(self, team_key: str) -> float:
        """Approximate final starter points for the slate week (from box scores)."""
        return round(sum(self._pool[s.player_id].week_points for s in self._rosters[team_key].starters()), 2)

    # ---------------------------------------------------------------- draft

    @staticmethod
    def _lively(entries: list[PoolEntry]) -> list[PoolEntry]:
        """Prefer players who recorded a play on slate day; fall back to the
        full pool only if a position would run dry."""
        active = [e for e in entries if e.active]
        have = Counter(e.position for e in active)
        if all(have[pos] >= cap * N_TEAMS for pos, cap in POSITION_CAPS.items()):
            return active
        return entries

    def _draft(self, rng: random.Random) -> dict[str, list[str]]:
        by_pos: dict[str, list[float]] = {}
        for e in self._pool.values():
            by_pos.setdefault(e.position, []).append(_value(e))
        baseline = {
            pos: sorted(vals, reverse=True)[min(REPLACEMENT_RANK[pos], len(vals)) - 1]
            for pos, vals in by_pos.items()
        }
        available = sorted(self._pool.values(), key=lambda e: e.id)
        picks: dict[str, list[str]] = {t.key: [] for t in self._teams}
        held: dict[str, Counter[str]] = {t.key: Counter() for t in self._teams}
        order = [t.key for t in self._teams]
        rng.shuffle(order)

        for rnd in range(1, ROUNDS + 1):
            for key in order if rnd % 2 else reversed(order):
                have = held[key]
                needs = {p for p, n in STARTER_MIN.items() if have[p] < n}
                must_fill = ROUNDS - rnd + 1 <= sum(STARTER_MIN[p] - have[p] for p in needs)

                def eligible(e: PoolEntry) -> bool:
                    if have[e.position] >= POSITION_CAPS[e.position]:
                        return False
                    if must_fill:
                        return e.position in needs
                    return rnd >= SPECIALIST_ROUND or e.position not in ("K", "DEF")

                cands = [e for e in available if eligible(e)]

                def fit(pos: str) -> float:
                    if pos in needs:
                        return NEED_BONUS
                    return -BACKUP_PENALTY if pos in ("QB", "TE") else 0.0

                scored = [
                    (_value(e) - baseline[e.position] + fit(e.position) + rng.gauss(0.0, DRAFT_NOISE), e)
                    for e in cands
                ]
                choice = max(scored, key=lambda s: (s[0], s[1].id))[1]
                available.remove(choice)
                picks[key].append(choice.id)
                have[choice.position] += 1
        return picks

    def _set_lineup(self, team_key: str, ids: list[str]) -> Roster:
        """Start the best producer per slot, the way a sane manager would."""
        left = sorted((self._pool[i] for i in ids), key=lambda e: (-_value(e), e.id))
        slots: list[RosterSlot] = []
        for slot in SLOTS:
            ok = FLEX if slot == "FLEX" else (slot,)
            pick = next(e for e in left if e.position in ok)
            left.remove(pick)
            slots.append(RosterSlot(slot=slot, player_id=pick.id))
        slots.extend(RosterSlot(slot="BN", player_id=e.id) for e in left)
        return Roster(team_key=team_key, week=self.week, slots=slots)

    # ------------------------------------------------------------- matchups

    def _pair(self, rng: random.Random, plays: Sequence[PlayRow]) -> list[Matchup]:
        others = [t.key for t in self._teams if t.key != USER_TEAM]
        mine = self.projected_final(USER_TEAM)
        gap = {k: abs(self.projected_final(k) - mine) for k in others}
        close = [k for k in others if gap[k] <= CLOSE_MARGIN] or [min(others, key=lambda k: (gap[k], k))]
        swings = {k: self._lead_changes(USER_TEAM, k, plays) for k in close}
        rival = min(close, key=lambda k: (swings[k] == 0, gap[k], k))

        rest = [k for k in others if k != rival]
        rng.shuffle(rest)
        pairs = [(USER_TEAM, rival), *zip(rest[::2], rest[1::2])]
        return [Matchup(week=self.week, a=a, b=b) for a, b in pairs]

    def _lead_changes(self, a: str, b: str, plays: Sequence[PlayRow]) -> int:
        side = {s.player_id: 1.0 for s in self._rosters[a].starters()}
        side.update({s.player_id: -1.0 for s in self._rosters[b].starters()})
        diff, leader, changes = 0.0, 0, 0
        for play in sorted(plays, key=lambda p: p.sim_time):
            delta = sum(side.get(pid, 0.0) * pts for pid, pts in approx_play_points(play).items())
            if not delta:
                continue
            diff += delta
            now = (diff > 1e-9) - (diff < -1e-9)
            if now and leader and now != leader:
                changes += 1
            leader = now or leader
        return changes


def approx_play_points(play: PlayRow) -> dict[str, float]:
    """Rough per-play fantasy points, only to judge how dramatic a matchup is.
    The real engine lives in retrogrid.scoring; this skips returns, laterals
    and points-allowed."""
    R = YAHOO_HALF_PPR
    out: dict[str, float] = {}

    def add(pid: str | None, pts: float) -> None:
        if pid and pts:
            out[pid] = out.get(pid, 0.0) + pts

    off_td = play.touchdown and play.td_team is not None and play.td_team == play.posteam
    if play.two_point == "success":
        for pid in (play.passer_id, play.receiver_id, play.rusher_id):
            add(pid, R["two_pt"])
    elif play.two_point is None and play.play_type == "pass":
        if play.complete:
            add(play.passer_id, play.yards_gained * R["pass_yd"] + (R["pass_td"] if off_td else 0))
            add(play.receiver_id, R["rec"] + play.yards_gained * R["rec_yd"] + (R["rec_td"] if off_td else 0))
        if play.interception:
            add(play.passer_id, R["pass_int"])
    elif play.two_point is None and play.play_type == "run":
        add(play.rusher_id, play.yards_gained * R["rush_yd"] + (R["rush_td"] if off_td else 0))
    elif play.play_type == "field_goal" and play.field_goal_result == "made":
        dist = play.kick_distance or 0
        add(play.kicker_id, R["fg_50"] if dist >= 50 else R["fg_40_49"] if dist >= 40 else R["fg_0_39"])
    elif play.play_type == "extra_point" and play.extra_point_result == "good":
        add(play.kicker_id, R["xp"])

    if play.fumble_lost:
        add(play.fumbler_id, R["fum_lost"])
    if play.defteam and play.play_type in ("pass", "run"):
        dst = f"DEF-{play.defteam}"
        add(dst, play.sack * R["def_sack"] + play.interception * R["def_int"]
            + play.fumble_lost * R["def_fum_rec"] + play.safety * R["def_safety"])
        if play.touchdown and play.td_team == play.defteam:
            add(dst, R["def_td"])
    return out
