"""Formation templates (DESIGN §8). Offsets are (dx from ball, dy from LOS);
negative dy is behind the line. Wide receivers are placed relative to the
sidelines, not the ball, so formations stay believable from either hash."""
from __future__ import annotations

import random

from . import constants as K
from .core import Actor, Key
from ..models import PlayRow

OL = [("C", 0.0, -0.5), ("LG", -1.5, -0.6), ("RG", 1.5, -0.6), ("LT", -3.1, -0.9), ("RT", 3.1, -0.9)]


def pick_formation(p: PlayRow) -> str:
    if p.play_type in ("qb_kneel",):
        return "victory"
    goal = (p.yardline_100 or 50) <= 3 or ((p.ydstogo or 10) <= 1 and p.play_type == "run" and not p.shotgun)
    if goal:
        return "heavy"
    if p.shotgun:
        if p.play_type == "run" and not p.qb_scramble:
            return "pistol"
        if (p.down or 1) >= 3 and (p.ydstogo or 0) >= 7:
            return "empty"
        return "gun11"
    if p.play_type == "run" and (p.down or 1) <= 2:
        return "iform"
    return "single"


def offense(name: str, bx: float, los: float, rng: random.Random) -> list[Actor]:
    strong = 1 if rng.random() < 0.6 else -1          # TE side
    wideL, wideR = 5.5 + rng.uniform(0, 2), K.FIELD_W - 5.5 - rng.uniform(0, 2)
    slotL, slotR = (bx + wideL) / 2 - 1, (bx + wideR) / 2 + 1
    out: list[Actor] = []

    def add(role: str, kind: str, x: float, dy: float) -> None:
        out.append(Actor(f"o-{role}", role, "off", kind, [Key(0, x, los + dy)]))

    for role, dx, dy in OL:
        add(role, "OL", bx + dx, dy)
    te_x = bx + strong * 4.7
    slot_x = slotL if strong > 0 else slotR            # slot goes away from the TE

    if name == "gun11":
        add("QB", "QB", bx, -5.0); add("RB", "RB", bx - strong * 1.9, -5.2)
        add("Y", "TE", te_x, -0.9); add("X", "WR", wideL, -0.4); add("Z", "WR", wideR, -1.2); add("H", "WR", slot_x, -1.2)
    elif name == "empty":
        add("QB", "QB", bx, -5.0); add("RB", "RB", (wideL + 3) if strong > 0 else (wideR - 3), -1.2)
        add("Y", "TE", bx + strong * 7.5, -1.0); add("X", "WR", wideL, -0.4); add("Z", "WR", wideR, -0.4); add("H", "WR", slot_x, -1.2)
    elif name == "pistol":
        add("QB", "QB", bx, -4.0); add("RB", "RB", bx, -7.0)
        add("Y", "TE", te_x, -0.9); add("X", "WR", wideL, -0.4); add("Z", "WR", wideR, -1.2); add("H", "WR", slot_x, -1.2)
    elif name == "single":
        add("QB", "QB", bx, -1.3); add("RB", "RB", bx, -7.0)
        add("Y", "TE", te_x, -0.9); add("X", "WR", wideL, -0.4); add("Z", "WR", wideR, -1.2); add("H", "WR", slot_x, -1.2)
    elif name == "iform":
        add("QB", "QB", bx, -1.3); add("FB", "RB", bx + rng.choice((-0.8, 0, 0.8)), -4.5); add("RB", "RB", bx, -7.5)
        add("Y", "TE", te_x, -0.9); add("X", "WR", wideL, -0.4); add("Z", "WR", wideR, -1.2)
    elif name == "heavy":
        add("QB", "QB", bx, -1.3); add("FB", "RB", bx, -4.2); add("RB", "RB", bx, -7.0)
        add("Y", "TE", bx + 4.6, -0.9); add("U", "TE", bx - 4.6, -0.9); add("Z", "WR", wideR if strong > 0 else wideL, -0.6)
    elif name == "victory":
        add("QB", "QB", bx, -1.3); add("FB", "RB", bx - 1.6, -3.0); add("H", "RB", bx + 1.6, -3.0); add("RB", "RB", bx, -8.0)
        add("Y", "TE", bx + 4.6, -0.9); add("U", "TE", bx - 4.6, -0.9)
    return out


def defense(off: list[Actor], bx: float, los: float, rng: random.Random, heavy: bool) -> list[Actor]:
    """Crude on purpose (§8): a front that pushes, DBs that shadow."""
    out: list[Actor] = []

    def add(role: str, kind: str, x: float, dy: float) -> Actor:
        a = Actor(f"d-{role}", role, "def", kind, [Key(0, min(K.FIELD_W - 1, max(1, x)), los + dy)])
        out.append(a)
        return a

    front = [-5.2, -1.7, 1.7, 5.2] if not heavy else [-6.5, -4, -1.4, 1.4, 4, 6.5]
    for i, dx in enumerate(front):
        add(f"DL{i + 1}", "DL", bx + dx, 1.0)
    lbs = [-3.2, 3.2] if not heavy else [-2.5, 0, 2.5]
    for i, dx in enumerate(lbs):
        add(f"LB{i + 1}", "LB", bx + dx + rng.uniform(-0.6, 0.6), 4.3 if not heavy else 2.6)
    # man defenders over every eligible split out
    wides = [a for a in off if a.kind in ("WR", "TE") and abs(a.start[0] - bx) > 6]
    wides.sort(key=lambda a: a.start[0])
    budget = 11 - len(out) - (1 if heavy else 2)
    for i, w in enumerate(wides[:budget]):
        press = rng.random() < 0.35
        inside = 0.8 if w.start[0] > bx else -0.8
        add(f"CB{i + 1}", "DB", w.start[0] - inside, 1.6 if press else rng.uniform(5.5, 7.5)).role = f"CB:{w.role}"
    deep = 11 - len(out)
    xs = {1: [0.0], 2: [-9.0, 9.0], 3: [-12.0, 0.0, 12.0]}.get(deep, [(-14 + 28 * i / max(1, deep - 1)) for i in range(deep)])
    for i, dx in enumerate(xs):
        add(f"S{i + 1}", "DB", bx + dx, (11.5 + rng.uniform(0, 3)) if not heavy else 5.5)
    return out
