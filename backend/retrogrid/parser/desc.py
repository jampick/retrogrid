"""NFL play-description prose -> structured fields (DESIGN §7).

A Python port of the regex layer nflfastR uses to derive its columns from the
league's rigidly formatted play text.  `parse_desc` works from prose alone and
never raises; `apply_to_row` merges the result into `PlayRow` kwargs.

Names are returned in the prose's short form ("T.Kelce", "A.St. Brown") with
any jersey / team prefix ("15-", "KC-15-") stripped.  Resolving a name to a
player id is the caller's job (`PlayerDirectory.by_short`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from typing import Any

__all__ = ["ParsedDesc", "parse_desc", "apply_to_row", "clean_name"]

# ───────────────────────────── regex vocabulary ─────────────────────────────

_PFX = r"(?:[A-Z]{2,3}-)?(?:\d{1,2}-)?"
_FIRST = r"(?:[A-Z]\.[A-Z]\. ?|[A-Z][a-z']{1,3}\. (?=[A-Z][a-z])|[A-Z][a-z']{0,3}[A-Z]?\.)"
_SUR = (
    r"(?:St\.? ?|Van Der |Vander |Vanden |Van |Von |De |Del |La |Le |Da |Di |Du )?"
    r"[A-Z][A-Za-z'\-]*[a-z]"
    r"(?: (?:Jr|Sr)\.?| (?:III|II|IV)(?![A-Za-z]))?"
)
_NAME = rf"(?<![A-Za-z.]){_PFX}(?:{_FIRST}{_SUR})"
_LOC = r"(?:[A-Z]{2,3} -?\d{1,2}|50|[Ee]nd [Zz]one)"
_GAIN = r"for (?:(?P<{g}>-?\d+) yards?|(?P<{z}>no gain))"


def _n(group: str) -> str:
    """A named, prefix-stripping capture for one player name."""
    return rf"(?<![A-Za-z.]){_PFX}(?P<{group}>{_FIRST}{_SUR})"


RE_NAME = re.compile(_n("name"))
RE_WS = re.compile(r"\s+")
RE_REVERSED = re.compile(r"\bREVERSED\b\.?", re.I)
RE_CLOCK = re.compile(r"^\(\d{0,2}:\d{2}\)\s*")
RE_LEAD_PAREN = re.compile(r"^\((?![^)]*\d-)[^)]*\)\s*")
RE_ELIGIBLE = re.compile(
    rf"^(?:{_NAME}(?:(?:,| and|, and) {_NAME})*\s+)?"
    r"(?:reported in as eligible|reports? as eligible|is eligible)[^.]*\.\s*",
    re.I,
)
RE_DIRECT_SNAP = re.compile(rf"^Direct snap to {_NAME}\.\s*", re.I)
RE_LEAD_NOISE = re.compile(
    r"^(?:[^()]{0,60}?\bin (?:game )?at (?:QB|quarterback)\b\.?|New quarterback.{0,60}?\.(?= )|"
    r"[A-Z][A-Za-z ]+ formation\.|Yard line changed[^.]*\.)\s*",
    re.I,
)
RE_SHOTGUN = re.compile(r"Shotgun")
RE_NO_HUDDLE = re.compile(r"No Huddle")
RE_PENALTY = re.compile(r"\bpenalty on\b", re.I)
RE_PENALTY_CLAUSE = re.compile(
    r"penalty on (?P<team>[A-Z]{2,3})?(?:-[^,]*)?,\s*(?P<type>[^,]+),(?P<rest>.*?)(?=penalty on|$)",
    re.I | re.S,
)
RE_NO_PLAY = re.compile(r"\bNo Play\b", re.I)
RE_TIMEOUT = re.compile(r"^\s*(?:\(\d{0,2}:\d{2}\)\s*)?Time ?out\b", re.I)
RE_ADMIN = re.compile(
    r"^\s*(?:\(\d{0,2}:\d{2}\)\s*)?(?:END (?:OF )?(?:QUARTER|GAME|HALF)|GAME\b|"
    r"End of (?:quarter|game|half)|Two-Minute Warning|.{0,40}wins? the (?:coin )?toss)",
    re.I,
)
RE_TD = re.compile(r"\bTOUCHDOWN\b(?!\s+NULLIFIED)")
RE_SAFETY = re.compile(r"\bSAFETY\b")
RE_FUMBLE = re.compile(r"\b(?:FUMBLES|MUFFS)\b")
RE_FUMBLE_LOST = re.compile(  # upper-case RECOVERED = the other team
    r"\bRECOVERED by\b|FUMBLES[^.]*out of bounds in End Zone, Touchback"
)
RE_RECOVERY = re.compile(r"\brecovered by (?P<team>[A-Z]{2,3})-", re.I)
RE_LATERAL = re.compile(r"\bLateral\b", re.I)

RE_TWO_POINT = re.compile(r"TWO-POINT CONVERSION ATTEMPT", re.I)
RE_DEF_TWO_POINT = re.compile(r"DEFENSIVE TWO-POINT ATTEMPT", re.I)
RE_TWO_POINT_OK = re.compile(r"ATTEMPT SUCCEEDS", re.I)
RE_TWO_POINT_FAIL = re.compile(r"ATTEMPT FAILS", re.I)
RE_TWO_POINT_PASS = re.compile(
    _n("passer") + r" (?:pass(?: (?:to|intended for) " + _n("receiver") + r")?"
    r"(?: is (?P<result>complete|incomplete))?|is sacked)"
)
RE_TWO_POINT_RUN = re.compile(
    _n("rusher") + r" rushes(?: (?:(?P<dir>left|right) (?P<gap>end|tackle|guard)|(?P<mid>up the middle)))?"
)

RE_KICKOFF = re.compile(
    _n("kicker") + r" kicks(?P<onside> onside)? (?P<dist>-?\d+) yards? from " + _LOC
)
RE_KICKOFF_KW = re.compile(r"\bkicks\b")
RE_PUNT = re.compile(_n("punter") + r" punts (?P<dist>-?\d+) yards? to")
RE_PUNT_KW = re.compile(r"\bpunts\b|\bpunt is (?:BLOCKED|blocked)")
RE_PUNT_BLOCKED = re.compile(_n("punter") + r" punt is BLOCKED", re.I)
RE_FG = re.compile(
    _n("kicker") + r" (?P<dist>\d+) yard field goal is (?P<result>GOOD|No Good|BLOCKED)", re.I
)
RE_FG_KW = re.compile(r"\bfield goal\b", re.I)
RE_XP = re.compile(
    _n("kicker") + r" extra point is (?P<result>GOOD|No Good|BLOCKED|Aborted)", re.I
)
RE_XP_KW = re.compile(r"\bextra point\b", re.I)
RE_FAIR_CATCH = re.compile(r"fair catch by " + _n("returner"))
RE_RETURN = re.compile(
    _n("returner")
    + r"(?: MUFFS catch[^.]*\.| (?:to|pushed ob at|ran ob at) "
    + _LOC
    + r" "
    + _GAIN.format(g="ret", z="retz")
    + r"| "
    + _GAIN.format(g="ret2", z="retz2")
    + r")"
)

RE_KNEEL_KW = re.compile(r"\bkneels?\b")
RE_SPIKE_KW = re.compile(r"\bspiked?\b")
RE_SACK_KW = re.compile(r"\bsacked\b")
RE_KNEEL = re.compile(_n("rusher") + r" kneels?\b")
RE_SPIKE = re.compile(_n("passer") + r" spiked?\b")
RE_SACK = re.compile(_n("passer") + r" sacked\b")
RE_PASS = re.compile(
    _n("passer")
    + r" pass(?P<inc> incomplete)?(?: (?P<len>short|deep))?(?: (?P<loc>left|middle|right))?"
    + r"(?: (?:to|intended for) "
    + _n("receiver")
    + r")?"
)
RE_PASS_KW = re.compile(r"\bpass\b")
RE_INTERCEPT = re.compile(r"\bINTERCEPTED\b(?: by " + _n("interceptor") + r")?")
RE_RUN = re.compile(
    _n("rusher")
    + r"(?P<scr> scrambles)?"
    + r"(?: (?:(?P<dir>left|right) (?P<gap>end|tackle|guard)|(?P<mid>up the middle)))?"
    + r"(?= (?:to|pushed ob|ran ob|tackled|for|FUMBLES|Aborted)\b)"
)
RE_SCRAMBLE = re.compile(r"\bscrambles\b")
RE_ABORTED = re.compile(r"\bAborted\b", re.I)
RE_GAIN = re.compile(_GAIN.format(g="yds", z="zero"))
RE_GAIN_TACKLE = re.compile(
    _GAIN.format(g="yds", z="zero") + r"(?:, (?:TOUCHDOWN|SAFETY))?\s*\((?P<tk>[^)]*)\)"
)
RE_SACK_SPLIT = re.compile(r"sacked[^.(]*\((?P<tk>[^)]*)\)")
RE_SPOT = re.compile(r"(?:(?P<side>[A-Z]{2,3}) (?P<n>-?\d{1,2})|(?P<mid>50))")
RE_CARRY_END = re.compile(
    r"(?:\bto|\bob at|\bsacked at|\bsacked ob at) (?P<spot>[A-Z]{2,3} -?\d{1,2}|50) for "
)
RE_ENFORCED = re.compile(r"enforced at (?P<spot>[A-Z]{2,3} -?\d{1,2}|50)")
RE_FUMBLE_EVENT = re.compile(  # first place the loose ball is accounted for
    r"\bFUMBLES\b(?:(?!FUMBLES|Lateral).)*?"
    r"(?:touched at|(?:RECOVERED|recovered) by .{0,40}? at|and recovers at|out of bounds at) "
    r"(?P<spot>[A-Z]{2,3} -?\d{1,2}|50)"
)
RE_OWN_RECOVERY = re.compile(
    r"\bFUMBLES\b(?:(?!FUMBLES|Lateral|RECOVERED).)*?"
    r"(?:(?P<self>and recovers at)|recovered by .{0,40}? at) (?P<spot>[A-Z]{2,3} -?\d{1,2}|50)"
)
RE_FUMBLE_TEAM = re.compile(
    r"\bFUMBLES\b(?:(?!FUMBLES|Lateral).)*?"
    r"(?:RECOVERED by (?P<opp>[A-Z]{2,3})-|recovered by (?P<own>[A-Z]{2,3})-)"
)
RE_SNAP_FUMBLE = re.compile(
    r"(?:" + _NAME + r" Aborted\. )?"
    + _n("qb") + r"(?: to " + _LOC + r" for (?:-?\d+ yards?|no gain))?(?: \([^)]*\))?[. ]+FUMBLES\b"
    r"(?:(?!RECOVERED|Lateral).)*?(?:and recovers|recovered by)(?:(?!RECOVERED|Lateral).)*?\.\s*"
    r"(?=" + _NAME + r" (?:pass|sacked)\b)"
)
RE_PLAY_START = re.compile(  # last resort: a play verb anywhere after free-text noise
    r"(?=" + _NAME + r" (?:pass|sacked|scrambles|kneels?|spiked?|(?:left|right) (?:end|tackle|guard)|up the middle)\b)"
)
RE_SENTENCE_START = re.compile(r"(?<=[.)]) +(?=\(|" + _NAME + r")")
RE_GROUPS = re.compile(r"\([^)]*\)|\[[^\]]*\]")
RE_CENTER_HOLDER = re.compile(rf"(?:Center|Holder)-{_NAME}")

_FG_RESULT = {"good": "made", "no good": "missed", "blocked": "blocked"}
_XP_RESULT = {"good": "good", "no good": "failed", "blocked": "blocked", "aborted": "failed"}
_SCRIMMAGE = ("pass", "run", "qb_kneel", "qb_spike")


# ─────────────────────────────── result type ────────────────────────────────


@dataclass
class ParsedDesc:
    """Everything recoverable from one play description. Unknown -> None/False."""

    play_type: str | None = None          # None = administrative line (END QUARTER ...)
    nullified_play_type: str | None = None  # what the play was before a No Play penalty
    shotgun: bool = False
    no_huddle: bool = False
    qb_scramble: bool = False
    pass_length: str | None = None
    pass_location: str | None = None
    run_location: str | None = None
    run_gap: str | None = None
    complete: bool = False
    incomplete: bool = False
    yards_gained: int | None = None
    touchdown: bool = False
    interception: bool = False
    sack: bool = False
    fumble: bool = False
    fumble_lost: bool = False
    fumble_recovery_team: str | None = None
    safety: bool = False
    penalty: bool = False
    penalty_nullified: bool = False
    penalty_team: str | None = None
    penalty_type: str | None = None
    two_point_attempt: bool = False
    two_point_result: str | None = None   # success | failure
    field_goal_result: str | None = None  # made | missed | blocked
    extra_point_result: str | None = None  # good | failed | blocked
    kick_distance: int | None = None
    return_yards: int | None = None
    touchback: bool = False
    lateral: bool = False
    reversed: bool = False
    passer: str | None = None
    receiver: str | None = None
    rusher: str | None = None
    kicker: str | None = None
    punter: str | None = None
    interceptor: str | None = None
    returner: str | None = None
    fumbler: str | None = None
    td_scorer: str | None = None
    tacklers: list[str] = field(default_factory=list)


# ───────────────────────────── small pure helpers ───────────────────────────


def clean_name(raw: str) -> str:
    """'KC-15-P.Mahomes' -> 'P.Mahomes'; 'D.K. Metcalf' -> 'D.K.Metcalf'."""
    m = RE_NAME.search(raw)
    name = m.group("name") if m else raw.strip()
    return re.sub(r"^([A-Z]\.[A-Z]\.) ", r"\1", name)


def _name(m: re.Match[str] | None, group: str) -> str | None:
    if m is None:
        return None
    try:
        raw = m.group(group)
    except IndexError:
        return None
    return clean_name(raw) if raw else None


def _names_in(text: str) -> list[str]:
    return [clean_name(m.group("name")) for m in RE_NAME.finditer(text)]


def _gain(m: re.Match[str] | None, g: str = "yds", z: str = "zero") -> int | None:
    if m is None:
        return None
    if m.group(g) is not None:
        return int(m.group(g))
    return 0 if m.group(z) else None


def _truth_segment(text: str) -> tuple[str, bool]:
    """After a replay reversal the restated play is the truth."""
    parts = RE_REVERSED.split(text)
    if len(parts) < 2:
        return text, False
    after = parts[-1].strip()
    if len(after) >= 12 and (RE_NAME.search(after) or RE_PENALTY.search(after)):
        return after, True
    return parts[0], True


def _strip_lead(core: str) -> str:
    """Drop clock, formation parentheticals and pre-snap housekeeping."""
    prev = None
    while prev != core:
        prev = core
        core = RE_CLOCK.sub("", core)
        core = RE_LEAD_PAREN.sub("", core)
        core = RE_ELIGIBLE.sub("", core)
        core = RE_DIRECT_SNAP.sub("", core)
        core = RE_LEAD_NOISE.sub("", core)
    return core


def _tacklers(core: str) -> list[str]:
    pre_fumble = RE_FUMBLE.split(core)[0]
    group: str | None = None
    for m in RE_GAIN_TACKLE.finditer(pre_fumble):
        group = m.group("tk")
    if group is None:
        m = RE_SACK_SPLIT.search(pre_fumble)
        group = m.group("tk") if m else None
    return _names_in(group) if group else []


def _last_carrier(text: str) -> str | None:
    """Last player named outside (tacklers) / [pressures] / Center- / Holder-."""
    names = _names_in(RE_CENTER_HOLDER.sub("", RE_GROUPS.sub(" ", text)))
    return names[-1] if names else None


def _td_scorer(core: str) -> str | None:
    m = RE_TD.search(core)
    return _last_carrier(core[: m.start()]) if m else None


def _penalties(body: str, p: ParsedDesc) -> None:
    live = [
        m
        for m in RE_PENALTY_CLAUSE.finditer(body)
        if not re.search(r"\b(?:declined|offsetting)\b", m.group(0), re.I)
    ]
    if live:
        p.penalty = True
        p.penalty_team = live[0].group("team")
        p.penalty_type = live[0].group("type").strip()


def _fumbles(core: str, p: ParsedDesc) -> None:
    if not RE_FUMBLE.search(core):
        return
    p.fumble = True
    p.fumble_lost = bool(RE_FUMBLE_LOST.search(core))
    rec = None
    for rec in RE_RECOVERY.finditer(core):
        pass
    p.fumble_recovery_team = rec.group("team") if rec else None
    p.fumbler = _last_carrier(core[: RE_FUMBLE.search(core).start()])  # type: ignore[union-attr]


def _spot(text: str | None) -> tuple[str | None, int] | None:
    m = RE_SPOT.search(text) if text else None
    if not m:
        return None
    return (None, 50) if m.group("mid") else (m.group("side"), int(m.group("n")))


def _coord(spot: tuple[str | None, int], ref: str | None) -> int:
    """Yards from `ref`'s own goal line."""
    side, n = spot
    n = max(n, 0)  # 'IND -3' is in the end zone: the goal line for yardage
    return n if side is None or side == ref else 100 - n


def _spot_foul_yards(core: str, body: str, gain: int, foul_team: str | None) -> int:
    """Offensive spot fouls: the gain is credited only up to the foul.

    'left end to BUF 45 for 5 yards ... enforced at BUF 41' is a 1-yard run.
    Direction is inferred from prose: the enforcement spot must lie strictly
    between the (two candidate) line-of-scrimmage spots and the end of the run.
    """
    enf = RE_ENFORCED.search(body)
    enforced = _spot(enf.group("spot")) if enf else None
    if enforced is None or gain <= 0 or RE_NO_PLAY.search(body):
        return gain
    end_m = RE_CARRY_END.search(core)
    ref = enforced[0]
    if end_m:
        end_spot = _spot(end_m.group("spot"))
        if end_spot is None:
            return gain
        ref = end_spot[0] or enforced[0]
        end = _coord(end_spot, ref)
    elif "TOUCHDOWN" in core and foul_team:
        x, start = _coord(enforced, foul_team), 100 - gain
        return x - start if start < x < 100 else gain
    else:
        return gain
    x = _coord(enforced, ref)
    for start in (end - gain, end + gain):
        lo, hi = sorted((start, end))
        if lo < x < hi:
            return abs(x - start)
    return gain


def _fumble_yards(core: str, gain: int, posteam: str | None) -> int:
    """Net a fumble into the gain the way the league's stat feed does.

    The carrier is charged for ground lost between where he fumbled and where
    the ball was next touched / recovered; ground *gained* by a loose ball is
    credited only back up to the line of scrimmage (or the original gain),
    unless the fumbler himself (or, on a sack, his team) recovers and advances.
    Needs the direction of play: `posteam`, else the recovery clause
    ('recovered by KC-' = offence, 'RECOVERED by KC-' = defence).
    """
    ev = RE_FUMBLE_EVENT.search(core)
    if not ev:
        return gain
    sack = bool(RE_SACK.match(core))
    own = RE_OWN_RECOVERY.search(core)
    advance = 0
    if own:
        tail = RE_FUMBLE.split(core[own.end():])[0]
        advance = sum(_gain(m) or 0 for m in RE_GAIN.finditer(tail))
    # The offence keeps the ball and the recovery spot matters when the
    # fumbler recovers himself, or a sacked QB's team-mate recovers and advances.
    keeps_ball = own is not None and (bool(own.group("self")) or (sack and advance > 0))
    before = None
    for before in RE_CARRY_END.finditer(core[: ev.start()]):
        pass
    ball = own.group("spot") if keeps_ball and own else ev.group("spot")
    fumble_spot, ball_spot = _spot(before.group("spot") if before else None), _spot(ball)
    if fumble_spot is None or ball_spot is None:
        return gain
    team = RE_FUMBLE_TEAM.search(core)
    offence = posteam or (team.group("own") if team else None)
    if offence:
        delta = _coord(ball_spot, offence) - _coord(fumble_spot, offence)
    elif team and team.group("opp"):
        delta = _coord(fumble_spot, team.group("opp")) - _coord(ball_spot, team.group("opp"))
    else:
        return gain
    net = gain + delta
    if keeps_ball:
        net += advance
        return min(net, 0) if sack else net
    return min(net, max(gain, 0)) if delta > 0 else net


def _scrimmage_yards(core: str, body: str, p: ParsedDesc, posteam: str | None) -> int | None:
    """Gain credited to the offence: first gain, plus lateral legs."""
    offence = RE_INTERCEPT.split(core)[0]
    offence = re.split(r"\bRECOVERED by\b", offence)[0]
    gains = [g for g in (_gain(m) for m in RE_GAIN.finditer(offence)) if g is not None]
    if not gains:
        return None
    if p.lateral:
        return sum(gains)
    if RE_FUMBLE.search(core):
        return _fumble_yards(core, gains[0], posteam)
    return _spot_foul_yards(core, body, gains[0], p.penalty_team)


# ───────────────────────────── per-type parsers ─────────────────────────────


def _parse_two_point(core: str, p: ParsedDesc) -> None:
    p.two_point_attempt = True
    core = RE_DEF_TWO_POINT.split(core)[0]  # a defensive return is a separate try
    if RE_TWO_POINT_OK.search(core):
        p.two_point_result = "success"
    elif RE_TWO_POINT_FAIL.search(core):
        p.two_point_result = "failure"
    inner = RE_TWO_POINT.split(core, maxsplit=1)[-1].lstrip(". ")
    inner = _strip_lead(inner)
    mp = RE_TWO_POINT_PASS.match(inner)
    mr = RE_TWO_POINT_RUN.match(inner)
    if mp:
        p.play_type = "pass"
        p.passer, p.receiver = _name(mp, "passer"), _name(mp, "receiver")
        p.incomplete = mp.group("result") == "incomplete"
    elif mr:
        p.play_type = "run"
        p.rusher = _name(mr, "rusher")
    elif RE_PASS_KW.search(inner) or RE_SACK_KW.search(inner):
        p.play_type = "pass"
    else:
        p.play_type = "run"
    # nflverse convention: a converted try is recorded as a 2-yard gain, and
    # direction / completion columns are left empty on tries.
    p.yards_gained = 2 if p.two_point_result == "success" else 0
    if RE_INTERCEPT.search(inner):
        p.interception = True
        p.interceptor = _name(RE_INTERCEPT.search(inner), "interceptor")


def _parse_return(rest: str, p: ParsedDesc) -> None:
    fc = RE_FAIR_CATCH.search(rest)
    if fc:
        p.returner = _name(fc, "returner")
        p.return_yards = 0
        return
    rest = RE_CENTER_HOLDER.sub("", rest)
    m = RE_RETURN.search(RE_WS.sub(" ", RE_GROUPS.sub(" ", rest)))
    if not m:
        return
    p.returner = _name(m, "returner")
    total = 0
    for g in RE_GAIN.finditer(re.split(r"\bRECOVERED by\b", rest)[0]):
        total += _gain(g) or 0
    p.return_yards = total


def _parse_kickoff(core: str, p: ParsedDesc) -> None:
    p.play_type = "kickoff"
    m = RE_KICKOFF.search(core)
    if m:
        p.kicker = _name(m, "kicker")
        p.kick_distance = int(m.group("dist"))
        rest = core[m.end():]
        # an onside kick that is simply fallen on is a recovery, not a return
        if not (m.group("onside") and "didn't try to advance" in rest):
            _parse_return(rest, p)
    p.touchback = "Touchback" in core
    p.yards_gained = 0


def _parse_punt(core: str, p: ParsedDesc) -> None:
    p.play_type = "punt"
    m = RE_PUNT.search(core)
    if m:
        p.punter = _name(m, "punter")
        p.kick_distance = int(m.group("dist"))
        _parse_return(core[m.end():], p)
    else:
        p.punter = _name(RE_PUNT_BLOCKED.search(core), "punter")
        p.kick_distance = 0 if p.punter else None
    p.touchback = "Touchback" in core
    p.yards_gained = 0


def _parse_field_goal(core: str, p: ParsedDesc) -> None:
    p.play_type = "field_goal"
    m = RE_FG.search(core)
    if m:
        p.kicker = _name(m, "kicker")
        p.kick_distance = int(m.group("dist"))
        p.field_goal_result = _FG_RESULT[m.group("result").lower()]
    p.yards_gained = 0


def _parse_extra_point(core: str, p: ParsedDesc) -> None:
    p.play_type = "extra_point"
    m = RE_XP.search(core)
    if m:
        p.kicker = _name(m, "kicker")
        p.extra_point_result = _XP_RESULT[m.group("result").lower()]
    elif re.search(r"extra point is (?:No Good|Aborted|BLOCKED)|Aborted", core, re.I):
        p.extra_point_result = "failed"
    p.yards_gained = 0


def _parse_pass(core: str, m: re.Match[str], p: ParsedDesc) -> None:
    p.play_type = "pass"
    p.passer, p.receiver = _name(m, "passer"), _name(m, "receiver")
    p.pass_length, p.pass_location = m.group("len"), m.group("loc")
    mi = RE_INTERCEPT.search(core)
    if mi:
        p.interception = True
        p.interceptor = _name(mi, "interceptor")
        _parse_return(core[mi.end():], p)
        p.returner = None  # the interceptor, already recorded
    p.incomplete = bool(m.group("inc")) and not p.interception
    p.complete = not p.incomplete and not p.interception


def _parse_run(core: str, m: re.Match[str], p: ParsedDesc) -> None:
    p.play_type = "run"
    p.rusher = _name(m, "rusher")
    p.qb_scramble = bool(m.group("scr"))
    if m.group("mid"):
        p.run_location = "middle"
    elif m.group("dir"):
        p.run_location, p.run_gap = m.group("dir"), m.group("gap")


def _match_scrimmage(core: str, p: ParsedDesc) -> bool:
    """Classify by what the *first* actor does; later clauses are aftermath."""
    if m := RE_KNEEL.match(core):
        p.play_type, p.rusher = "qb_kneel", _name(m, "rusher")
    elif m := RE_SPIKE.match(core):
        p.play_type, p.passer, p.incomplete = "qb_spike", _name(m, "passer"), True
    elif m := RE_SACK.match(core):
        p.play_type, p.sack, p.passer = "pass", True, _name(m, "passer")
    elif m := RE_PASS.match(core):
        _parse_pass(core, m, p)
    elif m := RE_RUN.match(core):
        _parse_run(core, m, p)
    else:
        return False
    return True


def _parse_scrimmage(core: str, body: str, p: ParsedDesc, posteam: str | None) -> str:
    """Parse a scrimmage play; returns the slice of `core` that was the play."""
    snap = RE_SNAP_FUMBLE.match(core)
    if snap:  # bobbled snap, QB picks it up and throws / is sacked: a pass play
        core = core[snap.end():]
    offsets = [0] + [m.end() for m in RE_SENTENCE_START.finditer(core)][:4]
    late = RE_PLAY_START.search(core)
    if late:
        offsets.append(late.start())
    for off in offsets:
        part = _strip_lead(core[off:])
        if _match_scrimmage(part, p):
            core = part
            break
    else:
        p.play_type = _guess_type(core)
    if p.play_type == "qb_spike" or p.incomplete or p.interception:
        p.yards_gained = 0
    elif RE_ABORTED.search(core[:60]) and p.play_type == "run":
        p.yards_gained = 0  # aborted snaps: nflverse credits no rushing yards
    elif p.play_type in _SCRIMMAGE:
        p.yards_gained = _scrimmage_yards(core, body, p, posteam)
    return core


def _guess_type(text: str) -> str | None:
    """Keyword fallback when the structured patterns do not match."""
    if not text or RE_ADMIN.match(text):
        return None
    if RE_TIMEOUT.match(text):
        return "no_play"
    checks: tuple[tuple[re.Pattern[str], str], ...] = (
        (RE_KICKOFF_KW, "kickoff"),
        (RE_PUNT_KW, "punt"),
        (RE_FG_KW, "field_goal"),
        (RE_XP_KW, "extra_point"),
        (RE_KNEEL_KW, "qb_kneel"),
        (RE_SPIKE_KW, "qb_spike"),
        (RE_SACK_KW, "pass"),
        (RE_SCRAMBLE, "run"),
        (RE_PASS_KW, "pass"),
    )
    for regex, kind in checks:
        if regex.search(text):
            return kind
    if RE_NAME.search(text) and RE_GAIN.search(text):
        return "run"
    if RE_ABORTED.search(text) or RE_FUMBLE.search(text):
        return "run"
    return "no_play" if RE_PENALTY.search(text) else None


def _blank_nullified(p: ParsedDesc) -> None:
    """A No Play penalty wipes the result, exactly as nflverse records it."""
    keep = {
        "shotgun", "no_huddle", "penalty", "penalty_team", "penalty_type", "reversed",
    }
    blank = ParsedDesc()
    p.nullified_play_type = p.play_type
    for f in fields(ParsedDesc):
        if f.name not in keep and f.name != "nullified_play_type":
            setattr(p, f.name, getattr(blank, f.name))
    p.play_type = "no_play"
    p.penalty_nullified = True
    p.yards_gained = 0


# ───────────────────────────────── public API ───────────────────────────────


def _parse(desc: str, posteam: str | None, nullify: bool = True) -> ParsedDesc:
    p = ParsedDesc()
    text = RE_WS.sub(" ", desc).strip()
    if not text or RE_ADMIN.match(text):
        return p
    if RE_TIMEOUT.match(text):
        p.play_type = "no_play"
        return p
    body, p.reversed = _truth_segment(text)
    p.shotgun = bool(RE_SHOTGUN.search(body))
    p.no_huddle = bool(RE_NO_HUDDLE.search(body))
    _penalties(body, p)
    core = _strip_lead(RE_PENALTY.split(body, maxsplit=1)[0].strip())
    p.lateral = bool(RE_LATERAL.search(core))
    play = core

    if RE_TWO_POINT.search(core):
        _parse_two_point(core, p)
    elif RE_KICKOFF.search(core) or (RE_KICKOFF_KW.search(core) and not RE_PASS_KW.search(core)):
        _parse_kickoff(core, p)
    elif RE_PUNT_KW.search(core):
        _parse_punt(core, p)
    elif RE_FG_KW.search(core):
        _parse_field_goal(core, p)
    elif RE_XP_KW.search(core):
        _parse_extra_point(core, p)
    elif core:
        play = _parse_scrimmage(core, body, p, posteam)
    elif RE_PENALTY.search(body):
        p.play_type = "no_play"

    _fumbles(core, p)
    p.safety = bool(RE_SAFETY.search(body))
    if RE_TD.search(core) and p.play_type is not None and not p.two_point_attempt:
        p.touchdown = True
        p.td_scorer = _td_scorer(play)
    if p.play_type in _SCRIMMAGE or p.play_type in ("kickoff", "punt"):
        p.tacklers = _tacklers(play)
    if nullify and (RE_NO_PLAY.search(body) or (p.play_type is None and p.penalty)):
        _blank_nullified(p)
    return p


def penalty_summary(desc: str) -> tuple[str | None, str | None, bool]:
    """(team, foul, offsetting) of the penalty that decided the play — for labels and flag placement."""
    clauses = list(RE_PENALTY_CLAUSE.finditer(desc or ""))
    if not clauses:
        return None, None, False
    if any(re.search(r"\boffsetting\b", m.group(0), re.I) for m in clauses):
        return None, clauses[0].group("type").strip(), True
    live = next((m for m in clauses if not re.search(r"\bdeclined\b", m.group(0), re.I)), clauses[0])
    return live.group("team"), live.group("type").strip(), False


def parse_desc(desc: str, posteam: str | None = None, nullify: bool = True) -> ParsedDesc:
    """Parse one play description. Never raises.

    `nullify=False` keeps what happened before a No Play flag wiped it — the
    grammar replays that as the called-back play; scoring never uses it.

    `posteam` is optional and only sharpens one thing prose cannot always
    give: the direction of play, needed to net out yards on fumble plays.
    """
    try:
        return _parse(desc if isinstance(desc, str) else "", posteam, nullify)
    except Exception:  # noqa: BLE001 - the live path must survive any prose
        try:
            return ParsedDesc(play_type=_guess_type(desc if isinstance(desc, str) else ""))
        except Exception:  # noqa: BLE001
            return ParsedDesc()


_NAME_TO_ROW = {
    "passer": "passer_name", "receiver": "receiver_name", "rusher": "rusher_name",
    "interceptor": "interceptor_name", "returner": "returner_name",
    "td_scorer": "td_player_name", "fumbler": "fumbler_name",
}
_DIRECT = (
    "shotgun", "no_huddle", "qb_scramble", "pass_length", "pass_location", "run_location",
    "run_gap", "complete", "touchdown", "interception", "fumble_lost", "sack", "safety",
    "penalty", "field_goal_result", "extra_point_result", "kick_distance",
)


def apply_to_row(parsed: ParsedDesc, base: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge parsed fields into `PlayRow` kwargs.

    Returns `(row_kwargs, names)`.  `row_kwargs` extends `base` using only
    `PlayRow` field names, so `PlayRow(**row_kwargs)` works once the caller has
    supplied the identity fields (play_id, game_id, ...).  `names` holds the
    unresolved short names keyed `passer_name`, `receiver_name`, `rusher_name`,
    `kicker_name`, `interceptor_name`, `returner_name`, `td_player_name`,
    `fumbler_name` and `tackler_names` (a list) for `PlayerDirectory.by_short`.
    Values already present and non-None in `base` for yards_gained win over
    prose (ESPN delivers yards directly).
    """
    row = dict(base)
    row["play_type"] = parsed.play_type or base.get("play_type") or "no_play"
    for name in _DIRECT:
        row[name] = getattr(parsed, name)
    if base.get("yards_gained") is None or "yards_gained" not in base:
        row["yards_gained"] = parsed.yards_gained or 0
    row["two_point"] = parsed.two_point_result
    row["return_yards"] = parsed.return_yards or 0
    row.setdefault("desc", "")

    ydstogo, gained = base.get("ydstogo"), row.get("yards_gained") or 0
    if "first_down" not in base:
        row["first_down"] = bool(
            parsed.play_type in ("pass", "run")
            and not parsed.two_point_attempt
            and not parsed.interception
            and not parsed.fumble_lost
            and (parsed.touchdown or (ydstogo is not None and gained >= ydstogo))
        )
    if parsed.touchdown and "td_team" not in base:
        turnover = parsed.interception or parsed.fumble_lost
        # nflverse convention: posteam is the receiving team on kickoffs,
        # the kicking team on punts.
        if parsed.play_type == "punt":
            turnover = not turnover
        row["td_team"] = base.get("defteam") if turnover else base.get("posteam")

    names: dict[str, Any] = {row_key: getattr(parsed, attr) for attr, row_key in _NAME_TO_ROW.items()}
    names["kicker_name"] = parsed.kicker or parsed.punter
    names["tackler_names"] = list(parsed.tacklers)
    return row, names
