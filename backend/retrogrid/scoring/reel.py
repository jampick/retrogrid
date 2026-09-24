"""Highlight ranking: which plays of a finished week are worth watching again.

    score = 100 × |WPA|             (how far the play moved the game)
          + spectacle               (flat: long gains, return TDs, blocks … whatever the leverage)
          + lead_change_bonus

WPA does the judging a hand-written rule cannot: a 9-yard catch on 4th & 8
down four with a minute left outranks a 60-yard touchdown in a blowout. The
spectacle bonus keeps the blowout's 60-yarder in the show anyway, because it
looks good on the field. Rows without WPA (the ESPN path, before nflverse has
the week) fall back to the ACTION board's weight × leverage, scaled to the
same units. Pure: no I/O, no pandas.
"""
from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass

from ..models import PlayRow
from .action import _late_and_close, _short, classify

WPA_SCALE = 100.0                 # a 30% swing scores 30
FALLBACK_SCALE = 1.6              # action weight (TD ≈ 10–13, ×1.6 late and close) -> the same range
LEAD_CHANGE_BONUS = 6.0
FAV_BOOST = 1.5
PER_WEEK = 40
GAME_FLOOR = 2                    # every game gets at least this many plays in the week's list
GAME_CAP = 6                      # and no single shootout takes more than this
CHIP_SHOT = 0.6                   # a made kick under 50 gets this much of its WPA: the drive won the game, the kick is the dull part
CLUTCH_WPA = 0.04                 # an unclassified play needs this much swing, and something to look at
DULL = {"no_play", "qb_kneel", "qb_spike"}


@dataclass
class Pick:
    play: PlayRow
    score: float
    tag: str                      # ACTION tags, plus "CLUTCH": an ordinary-looking down that swung the game
    team: str                     # who it was good for
    headline: str
    lead_change: bool
    before: tuple[int, int]       # (home, away) going into the play
    rank: int = 0                 # 1 = play of the week; set by pick_week


def spectacle(p: PlayRow) -> float:
    """Worth seeing whatever the score was."""
    s = 0.0
    yd = p.yards_gained
    if p.play_type in ("pass", "run") and yd >= 40:
        s += 6.0 + (yd - 40) / 4
    if p.touchdown:
        defensive = p.td_team is not None and p.td_team != p.posteam and p.play_type in ("pass", "run")
        s += 14.0 if defensive or p.play_type in ("punt", "kickoff") else 4.0
    if p.sack and p.fumble_lost:
        s += 8.0
    if p.field_goal_result == "blocked" or p.extra_point_result == "blocked" or (p.play_type == "punt" and "blocked" in p.desc.lower()):
        s += 10.0
    if p.safety:
        s += 8.0
    if p.field_goal_result == "made" and (p.kick_distance or 0) >= 50:
        s += 4.0 + ((p.kick_distance or 50) - 50)
    if p.two_point:
        s += 3.0
    if p.down == 4 and p.first_down and p.play_type in ("pass", "run"):
        s += 3.0
    if p.play_type in ("punt", "kickoff") and p.return_yards >= 40 and not p.touchdown:
        s += 4.0 + (p.return_yards - 40) / 5
    return s


def watchable(p: PlayRow) -> bool:
    if p.play_type in DULL or (p.penalty and "no play" in p.desc.lower()):
        return False
    return not (p.play_type == "extra_point" and p.extra_point_result == "good")


def _is_clutch(p: PlayRow) -> bool:
    """High leverage is not enough: an incompletion or a punt that swung the odds
    is still nothing to watch. It has to be the offence gaining ground."""
    if p.wpa is None or p.wpa < CLUTCH_WPA or p.play_type not in ("pass", "run") or p.sack:
        return False
    return p.yards_gained >= 10 or (p.first_down and (p.down or 0) >= 3)


def _clutch(p: PlayRow, directory) -> tuple[str, str, float, str]:      # noqa: ANN001
    """A play classify() shrugs at, that WPA says mattered."""
    team = p.posteam or ""
    who = _short(p.receiver_id or p.rusher_id or p.passer_id, directory)
    what = f"{who} {p.yards_gained}-YD {'CATCH' if p.play_type == 'pass' else 'RUN'}"
    return "CLUTCH", team, 0.0, what.strip()


def score_game(plays: Iterable[PlayRow], directory=None) -> list[Pick]:       # noqa: ANN001
    """Every watchable play of one game, scored, in game order. `plays` must be
    the whole game in order: the score going in and lead changes come from the row before."""
    out: list[Pick] = []
    home = away = 0
    for p in sorted(plays, key=lambda r: r.seq):
        before = (home, away)
        home, away = p.home_score, p.away_score
        if not watchable(p):
            continue
        hit = classify(p, directory)
        if hit is None and not _is_clutch(p):
            continue                                       # nothing to see, or nothing at stake
        tag, team, weight, text = hit or _clutch(p, directory)
        was = (before[0] > before[1]) - (before[0] < before[1])
        now = (home > away) - (home < away)
        flipped = now != 0 and was == -now
        base = WPA_SCALE * abs(p.wpa) if p.wpa is not None else FALLBACK_SCALE * weight * _late_and_close(p)
        if p.field_goal_result == "made" and (p.kick_distance or 0) < 50:
            base *= CHIP_SHOT
        score = base + spectacle(p) + (LEAD_CHANGE_BONUS if flipped else 0.0)
        out.append(Pick(p, round(score, 2), tag, team, text[:28], flipped, before))
    return out


def pick_week(by_game: dict[str, list[PlayRow]], directory=None, n: int = PER_WEEK,      # noqa: ANN001
              favs: Collection[str] = ()) -> list[Pick]:
    """The week's list, best first, ranked 1..n. Each game is floored and capped
    so the list covers the slate; `favs` lifts a followed team's plays."""
    scored = {g: score_game(ps, directory) for g, ps in by_game.items()}

    def boosted(k: Pick) -> float:
        _, _, a, h = k.play.game_id.split("_")
        return k.score * (FAV_BOOST if (a in favs or h in favs) else 1.0)

    chosen: list[Pick] = []
    rest: list[Pick] = []
    for picks in scored.values():
        ranked = sorted(picks, key=boosted, reverse=True)
        chosen += ranked[:GAME_FLOOR]
        rest += ranked[GAME_FLOOR:GAME_CAP]
    rest.sort(key=boosted, reverse=True)
    chosen += rest[: max(0, n - len(chosen))]
    chosen.sort(key=boosted, reverse=True)
    for i, k in enumerate(chosen, 1):
        k.rank = i
    return chosen
