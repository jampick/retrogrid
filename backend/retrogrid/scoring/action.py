"""Action ranking: the console's lens when nobody has a fantasy team.

    action = play_weight            (score, turnover, explosive, 4th down …)
           × leverage               (late and close; overtime)
           + lead_change_bonus      (the *real* scoreboard flipped)
           × favourite boost        (per viewer: the teams they follow)
           × recency_decay

The fantasy THREAT board (threat.py) asks "what is this doing to my week";
this one asks "where on the slate is football happening right now". Events
are viewer-independent — only the favourite boost is personal — so one board
serves every session. All times are sim-seconds. Pure: no I/O.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Collection
from dataclasses import dataclass

from ..models import PlayRow

HALF_LIFE = 600.0                 # 10 sim-minutes, same as the threat board
FAV_BOOST = 2.0                   # a followed team's plays count double
ALERT_THRESHOLD = 9.0             # boosted weight that earns a banner: scores, turnovers, late drama
BOARD_THRESHOLD = 3.0             # below this a play never makes the board
HOT_THRESHOLD = 6.0               # decayed weight before a FEEDS row shows ⚡
LEAD_CHANGE_BONUS = 6.0
HEADLINE_MAX = 28


@dataclass
class ActionEvent:
    play_id: str
    game_id: str
    team: str                     # who it was good for
    tag: str                      # "TD" "INT" "FUM" "SAFETY" "FG" "4TH" "STOP" "BIG" "SACK" "BLOCK" "MISS"
    weight: float                 # unboosted, undecayed
    sim_time: float
    lead_change: bool
    headline: str                 # ≤ 28 chars, uppercase


def _late_and_close(p: PlayRow) -> float:
    margin = abs(p.home_score - p.away_score)
    if p.quarter >= 5:
        return 2.0
    if p.quarter == 4 and margin <= 8:
        return 1.6
    if p.quarter == 4 and margin <= 16:
        return 1.2
    return 1.0


def _short(pid: str | None, directory) -> str:                # noqa: ANN001
    pl = directory.player(pid) if (pid and directory) else None
    if pl is None:
        return ""
    short = pl.short or pl.name
    return (short.split(".", 1)[-1] if "." in short else short.split()[-1]).strip().upper()


def classify(p: PlayRow, directory=None) -> tuple[str, str, float, str] | None:   # noqa: ANN001
    """(tag, team it favours, base weight, headline) — or None for an ordinary down."""
    if p.play_type == "no_play":
        return None
    off, dfn, yd = p.posteam or "", p.defteam or "", p.yards_gained
    who = _short(p.td_player_id or p.receiver_id or p.rusher_id or p.passer_id, directory)
    if p.touchdown:
        team = p.td_team or off
        if p.interception:
            what = "PICK-SIX"
        elif p.fumble_lost:
            what = "SCOOP-SIX"
        elif p.play_type in ("punt", "kickoff"):
            what = "RETURN TD"
        elif p.play_type == "pass":
            what = f"{yd}-YD TD CATCH"
        else:
            what = f"{yd}-YD TD RUN"
        bonus = 3.0 if team != off or p.play_type in ("punt", "kickoff") else min(3.0, max(0, yd - 20) / 15)
        return "TD", team, 10.0 + bonus, f"{who} {what}".strip()
    if p.safety:
        return "SAFETY", dfn, 9.0, "SAFETY"
    if p.interception:
        return "INT", dfn, 8.0, f"{_short(p.interceptor_id, directory)} INTERCEPTION".strip()
    if p.fumble_lost:
        return "FUM", dfn, 8.0, f"{_short(p.fumbler_id, directory)} FUMBLE LOST".strip()
    if p.two_point:
        ok = p.two_point == "success"
        return ("2PT" if ok else "STOP"), (off if ok else dfn), 5.0, "2-PT GOOD" if ok else "2-PT FAILS"
    if p.play_type == "field_goal":
        dist = p.kick_distance or 0
        if p.field_goal_result == "made":
            return "FG", off, 3.0 + max(0, dist - 45) / 5, f"{dist}-YD FG GOOD" if dist else "FG GOOD"
        blocked = p.field_goal_result == "blocked"
        return ("BLOCK" if blocked else "MISS"), dfn, 6.0 if blocked else 4.5, f"{dist}-YD FG {'BLOCKED' if blocked else 'NO GOOD'}".strip()
    if p.play_type == "extra_point" and p.extra_point_result in ("failed", "blocked"):
        return "MISS", dfn, 3.5, "PAT NO GOOD"
    if p.play_type == "punt" and "blocked" in p.desc.lower():
        return "BLOCK", dfn, 7.0, "PUNT BLOCKED"
    if p.down == 4 and p.play_type in ("pass", "run"):
        if p.first_down:
            return "4TH", off, 5.0, f"{who} CONVERTS 4TH & {p.ydstogo}".strip()
        return "STOP", dfn, 6.0, f"4TH & {p.ydstogo} STOPPED"
    if p.play_type in ("punt", "kickoff") and p.return_yards >= 40:
        return "BIG", dfn if p.play_type == "punt" else off, 4.0 + p.return_yards / 20, f"{_short(p.returner_id, directory)} {p.return_yards}-YD RETURN".strip()
    if (p.play_type == "pass" and p.complete and yd >= 25) or (p.play_type == "run" and yd >= 15):
        return "BIG", off, 3.0 + yd / 12, f"{who} {yd}-YD {'CATCH' if p.play_type == 'pass' else 'RUN'}".strip()
    if p.sack and (p.down or 0) >= 3:
        return "SACK", dfn, 3.0, f"{(p.down or 3)}{'RD' if p.down == 3 else 'TH'}-DOWN SACK"
    return None


class ActionBoard:
    """Every game's notable plays, ranked by how much football they were."""

    def __init__(self, directory=None, maxlen: int = 300) -> None:     # noqa: ANN001
        self.directory = directory
        self.events: deque[ActionEvent] = deque(maxlen=maxlen)
        self._lead: dict[str, int] = {}                  # game -> sign of (home - away)

    def reset(self) -> None:
        self.events.clear()
        self._lead.clear()

    def ingest(self, p: PlayRow) -> ActionEvent | None:
        sign = (p.home_score > p.away_score) - (p.home_score < p.away_score)
        before = self._lead.get(p.game_id, 0)
        self._lead[p.game_id] = sign
        flipped = sign != 0 and before == -sign          # took the lead, not merely tied or opened it
        hit = classify(p, self.directory)
        if hit is None:
            return None
        tag, team, weight, text = hit
        weight = weight * _late_and_close(p) + (LEAD_CHANGE_BONUS if flipped else 0.0)
        if weight < BOARD_THRESHOLD:
            return None
        e = ActionEvent(p.play_id, p.game_id, team, tag, weight, p.sim_time, flipped, text[:HEADLINE_MAX])
        self.events.append(e)
        return e

    @staticmethod
    def boosted(e: ActionEvent, favs: Collection[str]) -> float:
        _, _, away, home = e.game_id.split("_")
        return e.weight * (FAV_BOOST if (away in favs or home in favs) else 1.0)

    def score(self, e: ActionEvent, now: float, favs: Collection[str] = ()) -> float:
        return self.boosted(e, favs) * 0.5 ** (max(0.0, now - e.sim_time) / HALF_LIFE)

    def top(self, n: int, now: float, favs: Collection[str] = ()) -> list[ActionEvent]:
        live = [e for e in self.events if e.sim_time <= now]
        return sorted(live, key=lambda e: self.score(e, now, favs), reverse=True)[:n]

    def heat(self, now: float) -> dict[str, float]:
        """Decayed action per game — what FEEDS marks ⚡ and AUTO-DIRECT drifts toward."""
        out: dict[str, float] = {}
        for e in self.events:
            if e.sim_time <= now:
                out[e.game_id] = out.get(e.game_id, 0.0) + self.score(e, now)
        return out

    def should_alert(self, e: ActionEvent, favs: Collection[str] = ()) -> bool:
        return self.boosted(e, favs) >= ALERT_THRESHOLD
