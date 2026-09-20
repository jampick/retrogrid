from __future__ import annotations

import pytest

from retrogrid.providers.slate import DEFAULT_NFLVERSE_DIR, POOL_FILE, DEFAULT_SLATE_DIR, slate_available

needs_slate = pytest.mark.skipif(
    not (slate_available() and (DEFAULT_SLATE_DIR / POOL_FILE).exists()),
    reason="data/slate missing — run `retrogrid fetch` and `retrogrid build-slate`",
)
needs_nflverse = pytest.mark.skipif(
    not (DEFAULT_NFLVERSE_DIR / "players.parquet").exists(),
    reason="data/nflverse missing — run `retrogrid fetch`",
)


class FakeTime:
    """Manually advanced monotonic clock for deterministic SimClock tests."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt
