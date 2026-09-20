"""PlayProvider over the prepared slate + a SimClock (DESIGN §7, SIM SUNDAY).

Plays "arrive" when the clock passes their `sim_time`, interleaved across
games exactly as they were on the real Sunday.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import replace
from pathlib import Path
from typing import AsyncIterator

from ..models import Game, PlayRow
from ..sim.clock import SimClock
from .slate import DEFAULT_SLATE_DIR, Slate, load_slate


class SlatePlayProvider:
    def __init__(self, slate: Slate, clock: SimClock | None = None) -> None:
        self.slate = slate
        self.clock = clock or SimClock(slate.duration)
        self._plays = sorted(slate.plays, key=lambda p: (p.sim_time, p.game_id, p.seq))
        self._times = [p.sim_time for p in self._plays]
        self._by_game: dict[str, list[PlayRow]] = {g.id: [] for g in slate.games}
        for p in self._plays:
            self._by_game.setdefault(p.game_id, []).append(p)
        for plays in self._by_game.values():
            plays.sort(key=lambda p: p.seq)
        self._game_times = {gid: [p.sim_time for p in ps] for gid, ps in self._by_game.items()}

    @classmethod
    def from_dir(
        cls, slate_dir: Path | str = DEFAULT_SLATE_DIR, clock: SimClock | None = None,
    ) -> SlatePlayProvider:
        return cls(load_slate(slate_dir), clock)

    # ---------------------------------------------------------- sync helpers

    def all_plays(self) -> list[PlayRow]:
        return list(self._plays)

    def plays_until(self, t: float) -> list[PlayRow]:
        """Every play that has landed by sim time `t` (inclusive), in arrival
        order. Nothing has landed at t <= 0 — the stream delivers from there."""
        return self._plays[: self._cursor(t)]

    def plays_between(self, t0: float, t1: float) -> list[PlayRow]:
        """Plays landing in (t0, t1] — replay these to rebuild after a seek."""
        return self._plays[self._cursor(t0): self._cursor(t1)]

    def game_plays(self, game_id: str, until: float | None = None) -> list[PlayRow]:
        plays = self._by_game.get(game_id, [])
        if until is None:
            return list(plays)
        return plays[: self._game_cursor(game_id, until)]

    # -------------------------------------------------------------- contract

    def live_games(self) -> list[Game]:
        return self.games_at(self.clock.now())

    def games_at(self, t: float) -> list[Game]:
        return [self._game_at(g, t) for g in self.slate.games]

    def stream_plays(self, after: float | None = None) -> AsyncIterator[PlayRow]:
        """Yield plays as the clock passes them, forever. The start point is
        fixed when this is *called*: plays after the clock's current time (or
        `after`); state up to there comes from `plays_until`. So open the
        stream before starting a fresh clock, or pass `after=0`. After a seek
        the cursor jumps to the new time — watch `clock.epoch` to rebuild."""
        clock = self.clock
        return self._stream(clock.epoch, self._cursor(clock.now() if after is None else after))

    async def _stream(self, epoch: int, cursor: int) -> AsyncIterator[PlayRow]:
        clock = self.clock
        while True:
            if clock.epoch != epoch:        # seek, possibly while the consumer held a play
                epoch = clock.epoch
                cursor = self._cursor(clock.now())
            target = self._times[cursor] if cursor < len(self._plays) else math.inf
            if await clock.sleep_until(target) and clock.epoch == epoch:
                yield self._plays[cursor]
                cursor += 1

    # -------------------------------------------------------------- internal

    def _cursor(self, t: float) -> int:
        return 0 if t <= 0 else bisect_right(self._times, t)

    def _game_cursor(self, game_id: str, t: float) -> int:
        return 0 if t <= 0 else bisect_right(self._game_times[game_id], t)

    def _game_at(self, base: Game, t: float) -> Game:
        plays = self._by_game[base.id]
        n = self._game_cursor(base.id, t)
        if n == 0:
            return replace(base)
        last = plays[n - 1]
        game = replace(base, quarter=last.quarter, clock=last.clock,
                       home_score=last.home_score, away_score=last.away_score)
        if n == len(plays):
            game.status, game.clock = "final", "0:00"
            if base.id in self.slate.final_scores:
                game.home_score, game.away_score = self.slate.final_scores[base.id]
            return game
        upcoming = plays[n]
        if last.quarter == 2 and upcoming.quarter >= 3:
            game.status, game.clock = "half", "0:00"
        else:
            game.status, game.possession = "live", upcoming.posteam
        return game
