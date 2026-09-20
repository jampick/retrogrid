"""SIM SUNDAY replay clock (DESIGN §7). Sim time is "slate seconds": 0 at the
earliest kickoff. Pure asyncio on a monotonic clock; speed, pause and seek can
change at any moment and every sleeper wakes to re-plan.
"""
from __future__ import annotations

import asyncio
import math
import time
from typing import Any, Callable


class SimClock:
    def __init__(
        self,
        duration: float,
        speed: float = 1.0,
        start_at: float = 0.0,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if speed <= 0:
            raise ValueError("speed must be positive")
        self.duration = float(duration)
        self._time = time_fn
        self._speed = float(speed)
        self._anchor_sim = self._clamp(start_at)   # sim time at the anchor instant
        self._anchor_real = self._time()
        self._running = False
        self._epoch = 0                            # bumps on every seek
        self._changed = asyncio.Event()

    # ------------------------------------------------------------------ read

    def now(self) -> float:
        if not self._running:
            return self._anchor_sim
        elapsed = (self._time() - self._anchor_real) * self._speed
        return self._clamp(self._anchor_sim + elapsed)

    @property
    def speed(self) -> float:
        return self._speed

    @speed.setter
    def speed(self, value: float) -> None:
        self.set_speed(value)

    @property
    def running(self) -> bool:
        return self._running

    @property
    def finished(self) -> bool:
        return self.now() >= self.duration

    @property
    def epoch(self) -> int:
        """Seek generation. A consumer that sees it change must rebuild state."""
        return self._epoch

    def state(self) -> dict[str, Any]:
        return {"now": self.now(), "duration": self.duration, "speed": self._speed,
                "running": self._running, "epoch": self._epoch}

    # --------------------------------------------------------------- control

    def start(self) -> None:
        if not self._running:
            self._reanchor()
            self._running = True
            self._notify()

    resume = start

    def pause(self) -> None:
        if self._running:
            self._reanchor()
            self._running = False
            self._notify()

    def set_speed(self, speed: float) -> None:
        if speed <= 0:
            raise ValueError("speed must be positive")
        self._reanchor()
        self._speed = float(speed)
        self._notify()

    def seek(self, t: float) -> None:
        self._anchor_sim = self._clamp(t)
        self._anchor_real = self._time()
        self._epoch += 1
        self._notify()

    # ----------------------------------------------------------------- async

    async def sleep_until(self, t: float) -> bool:
        """Wait until sim time reaches `t`, honouring live speed changes and
        pauses. Returns True on arrival, False if a seek interrupted the wait
        (the caller's notion of "next" is then stale)."""
        epoch = self._epoch
        while True:
            if self._epoch != epoch:
                return False
            remaining = t - self.now()
            if remaining <= 0:
                return True
            reachable = self._running and t <= self.duration
            timeout = remaining / self._speed if reachable else None
            await self._wait_changed(timeout)

    async def wait_changed(self, timeout: float | None = None) -> bool:
        """Wait for any control change (start/pause/speed/seek). True if one
        happened, False on timeout (real seconds)."""
        return await self._wait_changed(timeout)

    # -------------------------------------------------------------- internal

    def _clamp(self, t: float) -> float:
        return min(max(float(t), 0.0), self.duration)

    def _reanchor(self) -> None:
        self._anchor_sim = self.now()
        self._anchor_real = self._time()

    def _notify(self) -> None:
        old, self._changed = self._changed, asyncio.Event()
        old.set()

    async def _wait_changed(self, timeout: float | None) -> bool:
        event = self._changed
        if timeout is not None and not math.isfinite(timeout):
            timeout = None
        try:
            await asyncio.wait_for(event.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return True
