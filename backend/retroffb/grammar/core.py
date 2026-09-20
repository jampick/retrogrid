"""Actors, paths and timing primitives for the play grammar (DESIGN §8)."""
from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass, field

from . import constants as K

Pt = tuple[float, float]


@dataclass
class Key:
    t: float
    x: float
    y: float
    z: float = 0.0


@dataclass
class Actor:
    id: str
    role: str                      # alignment slot: QB RB FB X Z H Y C LG ... CB1 FS ... BALL
    team: str                      # off | def | ball | flag
    kind: str                      # speed class: WR TE RB QB OL DL LB DB K
    keys: list[Key] = field(default_factory=list)
    involved: bool = False
    player_id: str | None = None

    # -- building -----------------------------------------------------------
    @property
    def start(self) -> Pt:
        return (self.keys[0].x, self.keys[0].y)

    @property
    def end_t(self) -> float:
        return self.keys[-1].t

    def at(self, t: float) -> Pt:
        ks = self.keys
        if t <= ks[0].t:
            return (ks[0].x, ks[0].y)
        for a, b in zip(ks, ks[1:]):
            if t <= b.t:
                u = (t - a.t) / max(1e-6, b.t - a.t)
                return (a.x + (b.x - a.x) * u, a.y + (b.y - a.y) * u)
        return (ks[-1].x, ks[-1].y)

    def hold(self, t: float) -> None:
        x, y = self.at(t)
        if t > self.end_t:
            self.keys.append(Key(t, x, y))

    def cut(self, t: float) -> None:
        """Drop everything after t, leaving the actor exactly where it was."""
        x, y = self.at(t)
        self.keys = [k for k in self.keys if k.t < t] + [Key(t, x, y)]

    def move(self, to: Pt, t: float) -> None:
        self.keys.append(Key(t, _clampx(to[0]), to[1]))

    def run(self, waypoints: list[Pt], t0: float, speed: float, accel: bool = True) -> float:
        """Append waypoints at roughly constant speed; returns arrival time."""
        self.hold(t0)
        t, (px, py) = max(t0, self.end_t), self.at(max(t0, self.end_t))
        first = accel
        for wx, wy in waypoints:
            d = math.hypot(wx - px, wy - py)
            if d < 0.05:
                continue
            dt = d / speed + (K.ACCEL_TIME * 0.5 if first else 0.0)
            first = False
            t += dt
            self.keys.append(Key(t, _clampx(wx), wy))
            px, py = wx, wy
        return t

    def run_until(self, waypoints: list[Pt], t0: float, t1: float) -> None:
        """Append waypoints so the last lands exactly at t1 (speed is derived)."""
        self.hold(t0)
        t0 = max(t0, self.end_t)
        px, py = self.at(t0)
        pts = [(px, py)] + waypoints
        total = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
        if total < 0.05 or t1 <= t0:
            self.hold(max(t1, t0 + 0.01))
            return
        acc = 0.0
        for a, b in zip(pts, pts[1:]):
            acc += math.hypot(b[0] - a[0], b[1] - a[1])
            u = acc / total
            u = u ** 0.85                       # a touch of ease-in off the line
            self.keys.append(Key(t0 + (t1 - t0) * u, _clampx(b[0]), b[1]))


def _clampx(x: float) -> float:
    return min(K.FIELD_W - 0.6, max(0.6, x))


def dist(a: Pt, b: Pt) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def toward(a: Pt, b: Pt, d: float) -> Pt:
    """Point d yards from a in the direction of b."""
    L = dist(a, b) or 1.0
    return (a[0] + (b[0] - a[0]) * d / L, a[1] + (b[1] - a[1]) * d / L)


def seeded(play_id: str) -> random.Random:
    """Same play -> same animation, always (re-render after a stat correction)."""
    return random.Random(zlib.crc32(play_id.encode()))


@dataclass
class Compiled:
    actors: list[Actor]
    duration: float
    los: float                     # camera anchor / LOS in field yards
    to_go: float | None
    los_line: bool = True
    template: str = ""             # "gun11/slant" — for the contact sheet
