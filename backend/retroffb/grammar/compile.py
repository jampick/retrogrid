"""compile(PlayRow) -> Actor[]   (DESIGN §8)

Pure and seeded: the same play always animates identically. Asymmetric
fidelity is the governing idea — the ball, the primary actor and the tackler
are choreographed with care; the other nineteen are plausible background.
"""
from __future__ import annotations

import math
import random
import re
from typing import Callable

from . import constants as K
from .core import Actor, Compiled, Key, Pt, dist, seeded, toward
from .formations import defense, offense, pick_formation
from ..models import PlayRow

PosOf = Callable[[str], str | None]
DUMMY_ROUTES = ("go", "hitch", "dig", "out", "post", "flat", "corner")


# ── helpers ────────────────────────────────────────────────────────────────
def _los(p: PlayRow) -> float:
    return 110.0 - float(p.yardline_100 if p.yardline_100 is not None else 50)


def _ball_x(rng: random.Random) -> float:
    return rng.choice((K.HASH_L + 0.4, K.CENTER_X, K.CENTER_X, K.HASH_R - 0.4))


def _by_role(actors: list[Actor], *roles: str) -> Actor | None:
    for r in roles:
        for a in actors:
            if a.role == r:
                return a
    return None


def _follow(ball: Actor, who: Actor, t0: float, t1: float) -> None:
    x, y = who.at(t0)
    ball.keys.append(Key(t0, x, y))
    for k in who.keys:
        if t0 < k.t < t1:
            ball.keys.append(Key(k.t, k.x, k.y))
    x, y = who.at(t1)
    ball.keys.append(Key(t1, x, y))


def _fly(ball: Actor, a: Pt, b: Pt, t0: float, t1: float, apex: float) -> None:
    ball.keys.append(Key(t0, a[0], a[1], 0.6))
    for u in (0.25, 0.5, 0.75):
        ball.keys.append(Key(t0 + (t1 - t0) * u, a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u,
                             0.6 + apex * 4 * u * (1 - u)))
    ball.keys.append(Key(t1, b[0], b[1], 0.6))


def _snap(ball: Actor, bx: float, los: float, qb: Actor) -> float:
    ball.keys.append(Key(0, bx, los - 0.3))
    t = 0.12 + dist((bx, los), qb.start) / 22
    ball.keys.append(Key(t, *qb.start))
    return t


def _weave(start: Pt, end: Pt, rng: random.Random) -> list[Pt]:
    """Ball-carrier path: long runs bend at the second level — arcs, not zig-zags."""
    d = end[1] - start[1]
    if abs(d) < 8:
        return [end]
    cut = rng.choice((-1, 1)) * rng.uniform(2.5, 5.5)
    ctrl = [start, (start[0] + cut, start[1] + d * 0.45)]
    if abs(d) > 25:
        ctrl.append((ctrl[1][0] - cut * 1.4, start[1] + d * 0.75))
    ctrl.append(end)
    pts: list[Pt] = []
    for a, b, c in zip(ctrl, ctrl[1:], ctrl[2:]):           # quadratic through the midpoints
        p0 = a if a is start else ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        p2 = c if c is end else ((b[0] + c[0]) / 2, (b[1] + c[1]) / 2)
        for u in (0.25, 0.5, 0.75, 1.0):
            pts.append(((1 - u) ** 2 * p0[0] + 2 * u * (1 - u) * b[0] + u * u * p2[0],
                        (1 - u) ** 2 * p0[1] + 2 * u * (1 - u) * b[1] + u * u * p2[1]))
    return pts


_OB = re.compile(r"\b(?:pushed|ran) ob\b")


def _out_of_bounds(p: PlayRow) -> bool:
    return bool(_OB.search(p.desc))


def _to_sideline(start: Pt, side: int, end_y: float) -> list[Pt]:
    """Carrier forced out: win the edge first, then turn up the boundary and step on the stripe."""
    end = (K.FIELD_W - 0.2 if side > 0 else 0.2, end_y)
    ctrl = (start[0] + (end[0] - start[0]) * 0.7, start[1] + (end_y - start[1]) * 0.2)
    return [((1 - u) ** 2 * start[0] + 2 * u * (1 - u) * ctrl[0] + u * u * end[0],
             (1 - u) ** 2 * start[1] + 2 * u * (1 - u) * ctrl[1] + u * u * end[1]) for u in (0.25, 0.5, 0.75, 1.0)]


def _cap_y(y: float, td: bool, down: bool = False) -> float:
    if td:
        return -1.5 + 10 if down else 111.5
    return min(109.6, max(10.4, y))


# ── routes ─────────────────────────────────────────────────────────────────
def route_to(start: Pt, catch: Pt, los: float, air: float, from_backfield: bool) -> tuple[list[Pt], str]:
    sx, sy = start
    cx, cy = catch
    dx = cx - sx
    inward = abs(cx - K.CENTER_X) < abs(sx - K.CENTER_X)
    s = 1 if dx >= 0 else -1
    if from_backfield:
        if air <= 0.5:
            return [(sx + s * 3, sy - 0.5), catch], "swing" if abs(dx) > 5 else "screen"
        if air >= 12:
            return [(sx + s * 6, los + 1), (cx, los + air * 0.5), catch], "wheel"
        return [(sx + s * 2, los - 1), catch], "flat" if abs(dx) > 7 else "checkdown"
    if air <= 0.5:
        return [(sx - s * 0.5, sy - 1.2), catch], "screen"
    if air < 15:
        if abs(dx) < 2.5:
            return [(sx, cy + 1.8), catch], ("curl" if air >= 9 else "hitch")
        if air <= 4:
            return [(sx + s, los + 1.2), catch], ("drag" if inward else "flat")
        if inward and air <= 9 and abs(dx) >= air * 0.6:
            return [(sx, los + 1.6), catch], "slant"
        if air <= 7 and not inward:
            return [(sx, cy - 0.2), catch], "out"
        return [(sx, cy - 0.2), catch], ("dig" if inward else "out")
    if abs(dx) < 3.5:
        return [(sx + dx * 0.3, los + air * 0.5), catch], ("seam" if abs(sx - K.CENTER_X) < 12 else "go")
    if abs(dx) > 18:
        return [(sx, los + air * 0.45), catch], "deep cross"
    return [(sx, cy - min(9.0, air * 0.4)), catch], ("post" if inward else "corner")


def _dummy_route(a: Actor, los: float, rng: random.Random, t_end: float, from_backfield: bool) -> None:
    sx, _ = a.start
    inside = 1 if sx < K.CENTER_X else -1
    if from_backfield:
        side = rng.choice((-1, 1))
        a.run([(sx + side * 2.5, los - 1.5), (sx + side * 7, los + 1.5)], 0.9, K.SPEED["RB"] * 0.8)
    else:
        kind = rng.choice(DUMMY_ROUTES)
        d = rng.uniform(5, 8) if kind in ("hitch", "out", "flat") else rng.uniform(10, 14)
        wp = {
            "go": [(sx, los + 28)], "hitch": [(sx, los + d + 1.5), (sx + inside, los + d)],
            "dig": [(sx, los + d), (sx + inside * 12, los + d)], "out": [(sx, los + d), (sx - inside * 5, los + d)],
            "post": [(sx, los + d), (sx + inside * 9, los + d + 12)], "corner": [(sx, los + d), (sx - inside * 5, los + d + 10)],
            "flat": [(sx - inside * 2, los + 1), (sx - inside * 5, los + 2.5)],
        }[kind]
        a.run(wp, 0.05, K.SPEED[a.kind] * 0.92)
    if a.end_t > t_end:
        a.cut(t_end)
    a.hold(t_end)


# ── defence reactions ──────────────────────────────────────────────────────
def _shadow(d: Actor, r: Actor, deep: bool, t_end: float) -> None:
    gap0 = d.start[1] - r.start[1]
    side = 0.8 if r.start[0] < K.CENTER_X else -0.8
    for k in r.keys[1:]:
        if k.t > t_end:
            break
        cushion = max(1.3, gap0 * max(0.0, 1 - k.t / 2.4))
        if deep and k.t > 2.0:
            cushion = -1.4                                  # beaten: trailing
        d.move((k.x + side, k.y + cushion), k.t + 0.28)


def _pursue(defs: list[Actor], spot: Pt, t0: float, t1: float, tackler: Actor | None) -> None:
    for i, d in enumerate(defs):
        if d is tackler:
            continue
        d.cut(t0) if d.end_t > t0 else d.hold(t0)
        here = d.at(t0)
        reach = K.SPEED[d.kind] * 0.85 * max(0.0, t1 - t0)
        gap = 1.6 + (i % 4) * 0.9
        go = min(reach, max(0.0, dist(here, spot) - gap))
        if go > 0.3:
            d.move(toward(here, spot, go), t1)
        else:
            d.hold(t1)


def _assign_tackler(p: PlayRow, defs: list[Actor], spot: Pt, t_from: float, t_hit: float,
                    prefer: Actor | None = None) -> Actor | None:
    if not p.tackler_ids or not defs:
        return None
    pool = [d for d in defs if d.kind != "DL"] or defs
    best = min(pool, key=lambda d: dist(d.at(t_from), spot) - (2.5 if d is prefer else 0))
    best.cut(t_from) if best.end_t > t_from else best.hold(t_from)
    here = best.at(t_from)
    need = dist(here, spot)
    lead = max(0.0, t_hit - need / K.CLOSING_SPEED)
    if lead > t_from + 0.05:                                # angle, then close
        best.move(toward(here, spot, need * 0.25), lead)
    best.move(spot, t_hit)
    best.involved, best.player_id = True, p.tackler_ids[0]
    return best


def _line_play(off: list[Actor], defs: list[Actor], qb_spot: Pt, t_end: float, run: bool,
               hole_x: float, stuffed: bool, rng: random.Random, sacker: Actor | None = None) -> None:
    for a in off:
        if a.kind != "OL":
            continue
        x, y = a.start
        if run:
            a.move((x + (0.9 if hole_x > x else -0.9) * rng.uniform(0.3, 1), y + (0.4 if stuffed else rng.uniform(1.4, 2.4))), 1.3)
        else:
            a.move((x * 0.97 + qb_spot[0] * 0.03, y - rng.uniform(1.0, 1.8)), 1.0)
        a.move((a.at(9)[0] + rng.uniform(-0.5, 0.5), a.at(9)[1] + rng.uniform(-0.4, 0.4)), max(1.6, t_end * 0.7))
    for d in defs:
        if d.kind != "DL" or d is sacker:
            continue
        x, y = d.start
        if run:
            d.move((x + (hole_x - x) * 0.25, y + (-1.2 if stuffed else rng.uniform(0.3, 1.2))), 1.4)
        else:
            d.move(toward(d.start, qb_spot, dist(d.start, qb_spot) * rng.uniform(0.3, 0.5)), max(1.5, t_end * 0.6))


# ── play families ──────────────────────────────────────────────────────────
def _scrimmage(p: PlayRow, rng: random.Random) -> tuple[list[Actor], list[Actor], Actor, float, float, str]:
    los, bx = _los(p), _ball_x(rng)
    name = pick_formation(p)
    off = offense(name, bx, los, rng)
    defs = defense(off, bx, los, rng, heavy=name in ("heavy", "victory"))
    ball = Actor("ball", "BALL", "ball", "WR")
    return off, defs, ball, los, bx, name


def _pick_target(off: list[Actor], pos: str | None, loc: str | None, rng: random.Random) -> Actor:
    if pos == "RB":
        a = _by_role(off, "RB", "FB")
        if a:
            return a
    if pos == "TE":
        a = _by_role(off, "Y", "U")
        if a:
            return a
    wrs = sorted([a for a in off if a.kind == "WR"], key=lambda a: a.start[0])
    if not wrs:
        return _by_role(off, "Y", "U", "RB") or off[-1]
    if loc == "left":
        return wrs[0] if rng.random() < 0.7 or len(wrs) < 2 else wrs[1]
    if loc == "right":
        return wrs[-1] if rng.random() < 0.7 or len(wrs) < 2 else wrs[-2]
    return _by_role(off, "H") if _by_role(off, "H") and rng.random() < 0.6 else rng.choice(wrs)


def _pass(p: PlayRow, rng: random.Random, pos_of: PosOf, air_est: Callable[[PlayRow], float] | None) -> Compiled:
    off, defs, ball, los, bx, form = _scrimmage(p, rng)
    qb = _by_role(off, "QB")
    assert qb
    air = p.air_yards
    if air is None:
        air = air_est(p) if air_est else (22.0 if p.pass_length == "deep" else min(6.0, max(1.0, float(p.yards_gained))))
    if not p.complete and p.pass_length == "deep":
        air = max(air, 16.0)
    air = min(air, 119.0 - los)
    concept = "screen" if air <= 0.5 else ("deep" if air >= 15 else "short")

    target = _pick_target(off, pos_of(p.receiver_id) if p.receiver_id else None, p.pass_location, rng)
    back = target.role in ("RB", "FB")
    cx = K.THIRD_X.get(p.pass_location or "middle", K.CENTER_X) + rng.uniform(-3, 3)
    if p.pass_location in (None, "middle"):
        cx = bx + (cx - K.CENTER_X) * 0.6
    if p.complete and _out_of_bounds(p) and p.pass_location in ("left", "right"):
        cx = rng.uniform(3.0, 7.0) if p.pass_location == "left" else K.FIELD_W - rng.uniform(3.0, 7.0)
    # A receiver can only be where his alignment lets him get to by the throw:
    # keep the ball in the charted third when possible, never drag him across the field.
    reach = 13.0 if back else 5.0 + 0.7 * max(0.0, air) if air < 15 else 8.0 + 0.4 * air
    cx = min(target.start[0] + reach, max(target.start[0] - reach, cx))
    if air <= 0.5 and not back:
        cx = target.start[0] - (1.5 if target.start[0] > bx else -1.5)
    catch: Pt = (min(K.FIELD_W - 1.5, max(1.5, cx)), los + air)
    wps, rname = route_to(target.start, catch, los, air, back)

    # QB drop
    snap_t = _snap(ball, bx, los, qb)
    gun = qb.start[1] < los - 3
    depth = K.GUN_DRIFT if gun else K.DROP_DEPTH[concept]
    set_pt = (bx + rng.uniform(-1.2, 1.2), qb.start[1] - depth)
    t_set = min(K.T_THROW[concept] - 0.35, 0.5 + depth * 0.22)
    qb.move(set_pt, max(snap_t + 0.2, t_set))

    # timing: receiver must be able to get there
    L = sum(dist(a, b) for a, b in zip([target.start] + wps, wps))
    t_arrive = (0.9 if back else 0.05) + L / K.SPEED[target.kind] + K.ACCEL_TIME * 0.5
    flight = max(K.BALL_MIN_FLIGHT, dist(set_pt, catch) / K.BALL_SPEED)
    t_catch = max(K.T_THROW[concept] + flight, t_arrive)
    t_throw = t_catch - flight
    qb.hold(t_throw)
    qb.move((set_pt[0] + rng.uniform(-0.6, 0.6), set_pt[1] + 0.8), t_throw + 0.5)
    target.run_until(wps, 0.9 if back else 0.05, t_catch)
    target.involved, target.player_id = True, p.receiver_id
    qb.involved, qb.player_id = True, p.passer_id
    _follow(ball, qb, snap_t, t_throw)

    cover = next((d for d in defs if d.role == f"CB:{target.role}"), None)
    t_end = t_catch
    tackler = None
    if p.interception:
        thief = cover or min(defs, key=lambda d: dist(d.start, catch))
        thief.run_until([catch], min(t_throw, t_catch - 0.6), t_catch)
        thief.involved, thief.player_id, thief.role = True, p.interceptor_id, "INT"
        _fly(ball, qb.at(t_throw), catch, t_throw, t_catch, 1.0 + dist(set_pt, catch) * 0.09)
        end = (catch[0] + rng.uniform(-6, 6), max(8.5, catch[1] - max(0, p.return_yards)))
        if p.touchdown:
            end = (end[0], 8.0)
        t_end = thief.run(_weave(catch, end, rng), t_catch, K.SPEED["DB"] * K.CARRY_FACTOR, accel=False)
        _follow(ball, thief, t_catch, t_end)
        target.move(toward(catch, end, 2.0), t_catch + 0.8)
        cover = thief
    elif p.complete:
        _fly(ball, qb.at(t_throw), catch, t_throw, t_catch, 1.0 + dist(set_pt, catch) * 0.09)
        end_y = _cap_y(los + p.yards_gained, p.touchdown)
        yac = end_y - catch[1]
        drift = rng.uniform(-1, 1) * min(8.0, abs(yac) * 0.4)
        end: Pt = (min(K.FIELD_W - 1, max(1, catch[0] + drift)), end_y)
        if _out_of_bounds(p):
            t_end = target.run(_to_sideline(catch, 1 if catch[0] > K.CENTER_X else -1, end_y), t_catch,
                               K.SPEED[target.kind] * K.CARRY_FACTOR, accel=False)
        elif abs(yac) > 0.4:
            t_end = target.run(_weave(catch, end, rng), t_catch, K.SPEED[target.kind] * K.CARRY_FACTOR, accel=False)
        else:
            t_end = t_catch + 0.25
            target.hold(t_end)
        _follow(ball, target, t_catch, t_end)
    else:
        miss = (catch[0] + rng.uniform(-2.5, 2.5), catch[1] + rng.uniform(1.5, 4.5))
        _fly(ball, qb.at(t_throw), miss, t_throw, t_catch + 0.15, 1.0 + dist(set_pt, catch) * 0.09)
        ball.keys.append(Key(t_catch + 0.55, miss[0] + rng.uniform(-1, 1), miss[1] + 1.5, 0.0))
        target.move(toward(catch, miss, 1.2), t_catch + 0.5)
        t_end = t_catch + 0.6

    for a in off:
        if a.kind in ("WR", "TE", "RB") and a is not target:
            _dummy_route(a, los, rng, t_end, a.role in ("RB", "FB"))
    for d in defs:
        if d.role.startswith("CB:") and d.player_id is None:
            r = _by_role(off, d.role[3:])
            if r:
                _shadow(d, r, air >= 15 and r is target, t_catch)
        elif d.kind == "DB" and d.player_id is None:
            d.move((d.start[0], d.start[1] + 3), 1.5)
            d.hold(t_throw)
            d.move(toward(d.at(t_throw), catch, min(dist(d.at(t_throw), catch) - 2.5, K.SPEED["DB"] * flight)), t_catch)
        elif d.kind == "LB":
            d.move((d.start[0] + rng.uniform(-2, 2), los + rng.uniform(5.5, 8)), 1.7)
    _line_play(off, defs, set_pt, t_end, False, bx, False, rng)

    if p.complete and not p.interception:
        carrier_end = target.at(t_end)
        if not p.touchdown:
            tackler = _assign_tackler(p, defs, carrier_end, t_catch, t_end, prefer=cover)
        _pursue(defs, carrier_end, t_catch, t_end, tackler)
    elif p.interception:
        chasers = [a for a in off if a.kind != "OL"]
        _pursue(chasers, cover.at(t_end), t_catch, t_end, None)     # type: ignore[union-attr]
    return Compiled(off + defs + [ball], t_end + 0.5, los, _to_go(p, los), template=f"{form}/{rname}")


def _to_go(p: PlayRow, los: float) -> float | None:
    return los + p.ydstogo if p.ydstogo and p.down else None


def _run(p: PlayRow, rng: random.Random, pos_of: PosOf) -> Compiled:
    off, defs, ball, los, bx, form = _scrimmage(p, rng)
    qb = _by_role(off, "QB")
    assert qb
    snap_t = _snap(ball, bx, los, qb)
    pos = pos_of(p.rusher_id) if p.rusher_id else None
    side = {"left": -1, "right": 1}.get(p.run_location or "", 0) or rng.choice((-1, 1))
    gap = p.run_gap if p.run_location in ("left", "right") else None
    hole_x = bx + (side * {"guard": 2.3, "tackle": 4.3, "end": 8.5}[gap] if gap else rng.uniform(-0.9, 0.9))
    yards = float(p.yards_gained)
    end_y = _cap_y(los + yards, p.touchdown)
    stuffed = yards <= 0
    sneak = pos == "QB" and not p.qb_scramble and (p.ydstogo or 10) <= 2 and not p.shotgun

    if p.qb_scramble or (pos == "QB" and not sneak):
        carrier, tname = qb, "scramble" if p.qb_scramble else "qb keep"
        set_pt = (bx + rng.uniform(-1, 1), qb.start[1] - (K.GUN_DRIFT if qb.start[1] < los - 3 else 5.0))
        t_break = K.T_SCRAMBLE_BREAK if p.qb_scramble else 1.2
        qb.move(set_pt, min(1.3, t_break - 0.3))
        qb.hold(t_break)
        edge = (bx + side * rng.uniform(7, 11), los - 1.0)
        end = (min(K.FIELD_W - 1, max(1, edge[0] + side * rng.uniform(0, 4))), end_y)
        t_los = qb.run([edge], t_break, K.SPEED["QB"])
        t_end = qb.run(_to_sideline(edge, side, end_y) if _out_of_bounds(p) else _weave(edge, end, rng),
                       t_los, K.SPEED["QB"] * K.CARRY_FACTOR, accel=False)
        _follow(ball, qb, snap_t, t_end)
        for a in off:
            if a.kind in ("WR", "TE", "RB"):
                _dummy_route(a, los, rng, t_end, a.role in ("RB", "FB"))
    elif sneak:
        carrier, tname = qb, "sneak"
        t_los = 0.5
        t_end = qb.run([(bx + rng.uniform(-0.5, 0.5), max(end_y, los + 0.3) if not stuffed else end_y)], 0.35, 3.2)
        _follow(ball, qb, snap_t, t_end)
        hole_x = bx
    else:
        wr = pos in ("WR", "TE")
        carrier = (_by_role(off, "H", "Z", "X") if wr else None) or _by_role(off, "RB") or qb
        toss = gap == "end" and qb.start[1] > los - 3
        t_hand = K.T_HANDOFF["toss" if toss else ("gun" if qb.start[1] < los - 3 else "under")]
        tname = "end around" if wr else ("toss" if toss else {None: "dive", "guard": "power", "tackle": "off tackle", "end": "outside zone"}[gap])
        if form == "gun11" and gap is None:
            tname = "draw"
        if wr:
            mesh = (bx - side * 1.0, los - 4.5)
            t_hand = carrier.run([mesh], 0.0, K.SPEED["WR"]) if dist(carrier.start, mesh) / K.SPEED["WR"] > t_hand else t_hand
            hole_x = bx + side * 11
        else:
            mesh = (qb.start[0] + side * 0.9, min(qb.start[1], los - 3.2) - (0 if qb.start[1] < los - 3 else 2.2))
        if carrier is not qb:
            if not wr:
                carrier.run_until([mesh] if not toss else [(carrier.start[0] + side * 3, carrier.start[1])], 0.1, t_hand)
            qb.move((mesh[0] - side * 0.9, mesh[1]), t_hand)
            qb.move((mesh[0] - side * 3.5, mesh[1] - 2.0), t_hand + 1.2)        # carry out the fake
            _follow(ball, qb, snap_t, t_hand - (0.25 if toss else 0))
            if toss:
                ball.keys.append(Key(t_hand, *carrier.at(t_hand), 0.8))
        crease = (hole_x, los - 0.4)
        t_los = carrier.run([crease], t_hand, K.SPEED["RB"] * 0.92, accel=False)
        drift = side * rng.uniform(0, 1) * min(9.0, max(0.0, yards) * 0.35)
        end = (min(K.FIELD_W - 1, max(1, hole_x + drift)), end_y)
        path = [(hole_x + rng.uniform(-0.5, 0.5), los + 2.5)] + _weave((hole_x, los + 2.5), end, rng) if yards >= 10 else [end]
        if _out_of_bounds(p):
            path = _to_sideline(crease, side if p.run_location in ("left", "right") else (1 if hole_x > K.CENTER_X else -1), end_y)
        t_end = carrier.run(path, t_los, K.SPEED["RB"] * K.CARRY_FACTOR * (0.75 if stuffed else 1), accel=False)
        _follow(ball, carrier, t_hand, t_end)
        fb = _by_role(off, "FB")
        if fb and fb is not carrier:
            fb.run([(hole_x, los + 1.8)], 0.15, K.SPEED["RB"] * 0.85)
        for a in off:
            if a.kind in ("WR", "TE") and a is not carrier:
                a.run([(a.start[0], los + rng.uniform(3.5, 6))], 0.1, K.SPEED[a.kind] * 0.7)
    carrier.involved, carrier.player_id = True, p.rusher_id

    spot = carrier.at(t_end)
    for d in defs:
        if d.kind == "LB":
            d.move((hole_x + rng.uniform(-2, 2), los + rng.uniform(1.5, 3)), max(1.2, t_los + 0.2))
        elif d.role.startswith("CB:"):
            r = _by_role(off, d.role[3:])
            if r and r is not carrier:
                _shadow(d, r, False, t_los)
        elif d.kind == "DB":
            d.move((d.start[0] + (hole_x - d.start[0]) * 0.3, d.start[1] - 3), max(1.4, t_los))
    _line_play(off, defs, qb.at(1.0), t_end, True, hole_x, stuffed, rng)
    tackler = None if p.touchdown else _assign_tackler(p, defs, spot, min(t_los, t_end - 0.4), t_end)
    _pursue(defs, spot, min(t_los, t_end - 0.3), t_end, tackler)
    return Compiled(off + defs + [ball], t_end + 0.5, los, _to_go(p, los), template=f"{form}/{tname}")


def _sack(p: PlayRow, rng: random.Random) -> Compiled:
    off, defs, ball, los, bx, form = _scrimmage(p, rng)
    qb = _by_role(off, "QB")
    assert qb
    snap_t = _snap(ball, bx, los, qb)
    spot = (bx + rng.uniform(-4, 4), los + min(-1.0, float(p.yards_gained)))
    set_pt = (bx + rng.uniform(-1, 1), min(qb.start[1] - 1.5, spot[1] + 1.5))
    qb.move(set_pt, 1.2)
    qb.hold(K.T_SACK - 1.0)
    qb.move(spot, K.T_SACK)
    qb.involved, qb.player_id = True, p.passer_id
    _follow(ball, qb, snap_t, K.T_SACK)
    rusher = min((d for d in defs if d.kind == "DL"), key=lambda d: abs(d.start[0] - spot[0]) + rng.random())
    rusher.move((rusher.start[0] + (2.5 if rusher.start[0] > bx else -2.5), los - 2.5), 1.7)      # win the edge
    rusher.move(spot, K.T_SACK)
    if p.tackler_ids:
        rusher.involved, rusher.player_id = True, p.tackler_ids[0]
    for a in off:
        if a.kind in ("WR", "TE", "RB"):
            _dummy_route(a, los, rng, K.T_SACK, a.role in ("RB", "FB"))
    for d in defs:
        if d.role.startswith("CB:"):
            r = _by_role(off, d.role[3:])
            if r:
                _shadow(d, r, False, K.T_SACK)
    _line_play(off, defs, set_pt, K.T_SACK, False, bx, False, rng, sacker=rusher)
    return Compiled(off + defs + [ball], K.T_SACK + 0.6, los, _to_go(p, los), template=f"{form}/sack")


def _dead(p: PlayRow, rng: random.Random, kind: str) -> Compiled:
    """Kneel, spike, and penalties that killed the play: formation, a beat, done."""
    off, defs, ball, los, bx, form = _scrimmage(p, rng)
    qb = _by_role(off, "QB")
    assert qb
    snap_t = _snap(ball, bx, los, qb)
    actors = off + defs + [ball]
    if kind == "kneel":
        qb.move((bx, qb.start[1] - 1.2), 1.0)
        qb.involved, qb.player_id = True, p.rusher_id or p.passer_id
        _follow(ball, qb, snap_t, 1.0)
        dur = 1.6
    elif kind == "spike":
        qb.involved, qb.player_id = True, p.passer_id
        ball.keys.append(Key(0.7, qb.start[0] + 0.8, qb.start[1] + 0.5))
        dur = 1.3
    else:
        for a in off + defs:
            a.move((a.start[0] + rng.uniform(-0.4, 0.4), a.start[1] + rng.uniform(-0.5, 0.5)), 0.9)
        fx, fy = bx + rng.uniform(-7, 7), los + rng.uniform(-1, 3)
        actors.append(Actor("flag", "FLAG", "flag", "K", [Key(0.5, fx - 2, fy - 1), Key(0.9, fx, fy)]))
        ball.keys.append(Key(0.9, *qb.start))
        dur = 1.9
    return Compiled(actors, dur, los, _to_go(p, los), template=f"{form}/{kind}")


def _placekick(p: PlayRow, rng: random.Random) -> Compiled:
    los, bx = _los(p), K.CENTER_X + rng.choice((-2.5, 0, 2.5))
    if p.play_type == "extra_point":
        los = 95.0
    off = [Actor(f"o-L{i}", f"L{i}", "off", "OL", [Key(0, bx + (i - 4) * 1.35, los - 0.6)]) for i in range(9)]
    holder = Actor("o-H", "HOLD", "off", "QB", [Key(0, bx, los - 7.5)])
    kicker = Actor("o-K", "K", "off", "K", [Key(0, bx - 1.8, los - 10.0)])
    kicker.involved, kicker.player_id = True, p.kicker_id
    kicker.move((bx - 0.4, los - 7.9), K.FG_KICK)
    kicker.move((bx + 0.3, los - 6.5), K.FG_KICK + 0.6)
    defs = [Actor(f"d-R{i}", f"R{i}", "def", "DL", [Key(0, bx + (i - 3.5) * 1.5, los + 0.9)]) for i in range(8)]
    defs += [Actor(f"d-B{i}", f"B{i}", "def", "DB", [Key(0, bx + dx, los + 5)]) for i, dx in enumerate((-7, 0, 7))]
    for i, d in enumerate(defs[:8]):
        if i in (0, 7):                                     # edge rushers bend the corner and dive
            d.move((d.start[0] + (-1.2 if i == 0 else 1.2), los - 0.8), K.FG_KICK * 0.55)
            d.move((bx + (-2.2 if i == 0 else 2.2), los - 5.0), K.FG_KICK + 0.25)
        else:                                               # the interior is a stalemate at the line
            d.move((d.start[0] * 0.92 + bx * 0.08, los + rng.uniform(0.1, 0.5)), K.FG_KICK + 0.2)
    for a in off:
        a.move((a.start[0], a.start[1] - rng.uniform(0.2, 0.7)), K.FG_KICK)
    ball = Actor("ball", "BALL", "ball", "WR", [Key(0, bx, los - 0.3), Key(K.FG_SNAP, bx, los - 7.5), Key(K.FG_KICK, bx, los - 7.5)])
    result = p.field_goal_result or {"good": "made", "failed": "missed", "blocked": "blocked"}.get(p.extra_point_result or "good", "made")
    spot = (bx, los - 7.5)
    if result == "blocked":
        hit = (bx + rng.uniform(-1, 1), los + 0.5)
        _fly(ball, spot, hit, K.FG_KICK, K.FG_KICK + 0.35, 1.5)
        ball.keys.append(Key(K.FG_KICK + 1.2, hit[0] + rng.uniform(-3, 3), los - 4, 0))
        t_end = K.FG_KICK + 1.3
    else:
        wide = 0.0 if result == "made" else rng.choice((-1, 1)) * rng.uniform(4.2, 7)
        goal = (K.CENTER_X + wide, 121.0)
        d = dist(spot, goal)
        t_end = K.FG_KICK + 1.0 + d / 28
        _fly(ball, spot, goal, K.FG_KICK, t_end, 6 + d * 0.16)
    return Compiled(off + [holder, kicker] + defs + [ball], t_end + 0.5, los, None, los_line=False,
                    template=f"{p.play_type}/{result}")


def _punt_or_kickoff(p: PlayRow, rng: random.Random) -> Compiled:
    kd = float(p.kick_distance or (45 if p.play_type == "punt" else 63))
    if p.play_type == "punt":
        los, bx = _los(p), _ball_x(rng)
        origin = (bx, los - 14.0)
        off = [Actor(f"o-L{i}", f"L{i}", "off", "OL", [Key(0, bx + (i - 3) * 1.6, los - 0.6)]) for i in range(7)]
        off += [Actor("o-G1", "G1", "off", "WR", [Key(0, 4.0, los - 0.5)]), Actor("o-G2", "G2", "off", "WR", [Key(0, K.FIELD_W - 4, los - 0.5)]),
                Actor("o-PP", "PP", "off", "RB", [Key(0, bx + 1.5, los - 6)])]
        kicker = Actor("o-P", "P", "off", "K", [Key(0, *origin)])
        defs = [Actor(f"d-R{i}", f"R{i}", "def", "LB", [Key(0, bx + (i - 3.5) * 1.8, los + 1)]) for i in range(8)]
        defs += [Actor("d-J1", "J1", "def", "DB", [Key(0, 4.5, los + 1.5)]), Actor("d-J2", "J2", "def", "DB", [Key(0, K.FIELD_W - 4.5, los + 1.5)])]
        t_kick, hang, cam = K.PUNT_KICK, K.PUNT_HANG, los
        ball = Actor("ball", "BALL", "ball", "WR", [Key(0, bx, los - 0.3), Key(K.PUNT_SNAP, *origin), Key(t_kick, *origin)])
        land_y = los + kd
    else:
        origin = (K.CENTER_X, 45.0)
        kicker = Actor("o-P", "K", "off", "K", [Key(0, K.CENTER_X - 1.5, 41.0)])
        kicker.move(origin, 0.9)
        off = [Actor(f"o-C{i}", f"C{i}", "off", "LB", [Key(0, 3.5 + i * (K.FIELD_W - 7) / 9, 70.0)]) for i in range(10)]
        defs = [Actor(f"d-R{i}", f"R{i}", "def", "LB", [Key(0, 5 + i * (K.FIELD_W - 10) / 8, 75.0 + (i % 2) * 4)]) for i in range(9)]
        t_kick, hang, cam = 0.9, K.KICK_HANG, 60.0
        ball = Actor("ball", "BALL", "ball", "WR", [Key(0, *origin), Key(t_kick, *origin)])
        land_y = 45.0 + kd
    desc = p.desc.lower()
    kickoff = p.play_type == "kickoff"
    returned = bool(p.return_yards > 0 or (p.returner_id and p.touchdown))
    fate = "return" if returned else next((v for k, v in (
        ("blocked", "blocked"), ("touchback", "touchback"), ("fair catch", "fair catch"),
        ("out of bounds", "oob"), ("downed", "downed")) if k in desc), "dead")
    land_x = K.CENTER_X + rng.uniform(-13, 13)
    if fate == "oob":
        land_x = rng.choice((0.7, K.FIELD_W - 0.7))
    land: Pt = (land_x, min(118.0, land_y))
    if fate == "touchback":
        land = (land_x, max(land[1], 112.0))
    ret = Actor("d-PR", "RET", "def", "WR", [Key(0, K.CENTER_X + (land[0] - K.CENTER_X) * 0.5 + rng.uniform(-3, 3),
                                                  min(108.0, land[1] - rng.uniform(0, 3)))])
    if kickoff:
        defs.append(Actor("d-PR2", "RET2", "def", "WR", [Key(0, K.FIELD_W - ret.start[0], ret.start[1])]))
    t_land = t_kick + hang
    cover = [a for a in off if a is not kicker]
    blockers = [d for d in defs if not d.role.startswith("RET")]

    if fate == "blocked":
        hit = (bx + rng.uniform(-2, 2), los - 3.0)
        t_land = t_kick + 0.35
        _fly(ball, origin, hit, t_kick, t_land, 1.5)
        ball.keys.append(Key(t_land + 1.1, hit[0] + rng.uniform(-4, 4), los - rng.uniform(5, 11), 0))
        blocker = min(blockers, key=lambda d: abs(d.start[0] - hit[0]))
        blocker.run_until([hit], 0.3, t_land)
        for d in blockers:
            if d is not blocker:
                d.move((d.start[0] * 0.85 + bx * 0.15, los - rng.uniform(0.5, 4)), t_kick)
        kicker.move((origin[0] + rng.uniform(-2, 2), origin[1] + 1.5), t_land + 1.0)
        return Compiled(off + [kicker] + defs + [ret, ball], t_land + 1.7, cam, None, template="punt/blocked")

    _fly(ball, origin, land, t_kick, t_land, 7 + kd * 0.12)
    t_end = t_land + 0.5
    if returned:
        ret.run_until([land], t_kick, t_land)
        ret.involved, ret.player_id = True, p.returner_id
        end = (min(K.FIELD_W - 1.5, max(1.5, land[0] + rng.uniform(-8, 8))), 8.0 if p.touchdown else max(10.5, land[1] - p.return_yards))
        t_end = ret.run(_weave(land, end, rng), t_land, K.SPEED["WR"] * K.CARRY_FACTOR, accel=False)
        _follow(ball, ret, t_land, t_end)
    elif fate == "fair catch":
        ret.run_until([land], t_kick, t_land - 0.5)                     # camped under it
        ret.involved, ret.player_id = True, p.returner_id
        ball.keys.append(Key(t_end, *land))
    else:
        bounce = 0.0 if fate == "oob" else rng.uniform(2, 6)
        rest = (min(K.FIELD_W - 0.3, max(0.3, land[0] + rng.uniform(-1.5, 1.5))), min(119.5 if fate in ("touchback", "dead") else 109.4, land[1] + bounce))
        t_end = t_land + 0.4 + bounce * 0.18
        ball.keys.append(Key(t_end, *rest, 0))
        away = (K.CENTER_X + (land[0] - K.CENTER_X) * 0.6, min(land[1] - 4, 108.0))      # lets it go
        ret.run_until([away], t_kick, t_land)

    # coverage: fan out into lanes, then squeeze toward the ball. Under the dynamic
    # kickoff nobody on the line may move until the ball is fielded or lands.
    t_go = t_land if kickoff else t_kick - 0.25
    spot = ret.at(t_end) if returned else ball.at(t_end)
    n = len(cover)
    for i, a in enumerate(sorted(cover, key=lambda a: a.start[0])):
        lane = 3.5 + i * (K.FIELD_W - 7) / max(1, n - 1)
        if a.role in ("G1", "G2"):                          # gunners win outside, then hunt
            lane = a.start[0]
        speed = K.SPEED["WR" if a.role in ("G1", "G2") else "LB"] * rng.uniform(0.84, 0.97)
        release = t_go + (0 if a.role in ("G1", "G2") or kickoff else rng.uniform(0.3, 0.9))   # linemen hold their block
        squeeze = (land[0] + (lane - land[0]) * 0.55, land[1] - 7 - abs(i - (n - 1) / 2) * 1.2)
        mid = (a.start[0] + (lane - a.start[0]) * 0.8, a.start[1] + (squeeze[1] - a.start[1]) * 0.45)
        if squeeze[1] > a.start[1] + 1:
            a.run([mid, squeeze], release, speed)
    # return team: drop with the coverage, set a wall in front of the returner
    for j, d in enumerate(blockers):
        wall_y = land[1] - rng.uniform(9, 20)
        tx = d.start[0] + (land[0] - d.start[0]) * rng.uniform(0.15, 0.45)
        if wall_y > d.start[1] + 1 and not kickoff:
            d.run([(tx, wall_y)], t_go + rng.uniform(0.1, 0.6), K.SPEED["LB"] * rng.uniform(0.7, 0.85))
        if d.end_t > t_land:
            d.cut(t_land)
        if returned:                                        # lead the return a few steps
            here = d.at(t_land)
            d.hold(t_land)
            dy = rng.uniform(1, 4) if kickoff else -min(6.0, p.return_yards * 0.4)      # kickoff: step up and engage
            d.move((here[0] + (spot[0] - here[0]) * 0.25, here[1] + dy), t_end)

    tackler = None
    if returned and not p.touchdown and p.tackler_ids:
        tackler = min(cover, key=lambda a: dist(a.at(t_land), spot))
        tackler.cut(t_land) if tackler.end_t > t_land else tackler.hold(t_land)
        tackler.move(toward(tackler.at(t_land), spot, dist(tackler.at(t_land), spot) * 0.5), (t_land + t_end) / 2)
        tackler.move(spot, t_end)
        tackler.involved, tackler.player_id = True, p.tackler_ids[0]
    if returned or fate in ("downed", "fair catch"):
        ranked = sorted((a for a in cover if a is not tackler), key=lambda a: dist(a.at(t_land), spot))
        for rank, a in enumerate(ranked):                   # nearest close in; the rest keep leverage
            a.cut(t_land) if a.end_t > t_land else a.hold(t_land)
            here = a.at(t_land)
            reach = K.SPEED["LB"] * 0.85 * (t_end - t_land)
            go = min(reach, max(0.0, dist(here, spot) - (2.0 + rank * 1.6)))
            a.move(toward(here, spot, go), t_end) if go > 0.3 else a.hold(t_end)
    return Compiled(off + [kicker] + defs + [ret, ball], t_end + 0.5, cam, None, los_line=not kickoff,
                    template=f"{p.play_type}/{fate}")


# ── entry point ────────────────────────────────────────────────────────────
def compile_play(p: PlayRow, pos_of: PosOf = lambda _id: None,
                 air_est: Callable[[PlayRow], float] | None = None) -> Compiled:
    """Never crash, never render nothing (§8 Degradation)."""
    rng = seeded(p.play_id)
    try:
        c = _dispatch(p, rng, pos_of, air_est)
    except Exception:                                   # noqa: BLE001 — degrade, don't die
        c = _dead(p, seeded(p.play_id + "!"), "hold")
        c.template = "fallback"
    for a in c.actors:
        if not a.keys:
            a.keys = [Key(0, K.CENTER_X, c.los)]
        a.keys.sort(key=lambda k: k.t)
        if a.end_t > c.duration:                        # the whistle stops everyone
            a.cut(c.duration)
        a.hold(c.duration)
        for k in a.keys:
            k.y = min(121.5, max(-1.5, k.y))
    return c


def _dispatch(p: PlayRow, rng: random.Random, pos_of: PosOf, air_est) -> Compiled:
    t = p.play_type
    if t == "no_play" or (p.penalty and "no play" in p.desc.lower()):
        return _dead(p, rng, "flag")
    if t == "qb_kneel":
        return _dead(p, rng, "kneel")
    if t == "qb_spike":
        return _dead(p, rng, "spike")
    if t in ("field_goal", "extra_point"):
        return _placekick(p, rng)
    if t in ("punt", "kickoff"):
        return _punt_or_kickoff(p, rng)
    if p.two_point and not (p.passer_id or p.rusher_id):
        return _dead(p, rng, "hold")
    if p.sack:
        return _sack(p, rng)
    if t == "pass" and not p.qb_scramble:
        return _pass(p, rng, pos_of, air_est)
    if t == "run" or p.qb_scramble:
        return _run(p, rng, pos_of)
    return _dead(p, rng, "hold")
