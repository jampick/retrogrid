"""Learned prior for `air_yards`, the one variable prose never carries (DESIGN §7).

The play text tells us short-vs-deep (threshold 15 air yards), the third of
the field, whether the ball was caught and the total gain.  That bounds air
yards but does not split air from YAC, so we look up the conditional *median*
of charted air yards in nflverse history, backing off to coarser cells when a
cell is sparse.  The table is a small JSON fitted by scripts/fit_air_yards.py;
nothing here needs pandas.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

__all__ = ["estimate_air_yards", "cell_keys", "yards_bucket", "togo_bucket", "PRIOR_PATH", "LEVELS"]

PRIOR_PATH = Path(__file__).with_name("air_yards_prior.json")

# Backoff ladder, most to least specific.  Each level names the conditioning
# variables it keeps; a level is used only if its cell had >= min_n plays.
LEVELS: tuple[tuple[str, ...], ...] = (
    ("complete", "length", "location", "yards", "down", "togo"),
    ("complete", "length", "location", "yards", "down"),
    ("complete", "length", "location", "yards"),
    ("complete", "length", "yards"),
    ("complete", "length", "location"),
    ("complete", "length"),
    ("complete", "yards"),
    ("complete",),
    (),
)

_YARD_EDGES = (25, 30, 40, 50, 60)   # above 20 yards, buckets widen
_SHORT_MAX, _DEEP_MIN = 14.0, 15.0


def yards_bucket(yards_gained: int | float | None, complete: bool) -> str:
    """Total-gain bucket. Only completions carry information in the gain."""
    if not complete or yards_gained is None:
        return "na"
    y = int(yards_gained)
    if y <= -3:
        return "<=-3"
    if y <= 20:
        return str(y)
    lo = 21
    for edge in _YARD_EDGES:
        if y <= edge:
            return f"{lo}-{edge}"
        lo = edge + 1
    return "61+"


def togo_bucket(ydstogo: int | float | None) -> str:
    if ydstogo is None:
        return "na"
    t = int(ydstogo)
    if t <= 2:
        return "1-2"
    if t <= 5:
        return "3-5"
    if t <= 9:
        return "6-9"
    if t == 10:
        return "10"
    return "11+"


def _features(
    pass_length: str | None,
    pass_location: str | None,
    yards_gained: int | float | None,
    down: int | float | None,
    ydstogo: int | float | None,
    complete: bool,
) -> dict[str, str]:
    return {
        "complete": "c" if complete else "i",
        "length": pass_length if pass_length in ("short", "deep") else "na",
        "location": pass_location if pass_location in ("left", "middle", "right") else "na",
        "yards": yards_bucket(yards_gained, complete),
        "down": str(int(down)) if down in (1, 2, 3, 4) else "na",
        "togo": togo_bucket(ydstogo),
    }


def cell_keys(
    pass_length: str | None,
    pass_location: str | None,
    yards_gained: int | float | None,
    down: int | float | None,
    ydstogo: int | float | None,
    complete: bool,
) -> list[str]:
    """Table keys for one pass, most specific first ('<level>:<v1>|<v2>...')."""
    f = _features(pass_length, pass_location, yards_gained, down, ydstogo, complete)
    return [f"{i}:" + "|".join(f[name] for name in level) for i, level in enumerate(LEVELS)]


@lru_cache(maxsize=1)
def _table() -> dict[str, Any]:
    try:
        return json.loads(PRIOR_PATH.read_text())["cells"]
    except (OSError, ValueError, KeyError):
        return {}


def _clip(value: float, pass_length: str | None, yardline_100: int | float | None) -> float:
    if pass_length == "short":
        value = min(value, _SHORT_MAX)
    elif pass_length == "deep":
        value = max(value, _DEEP_MIN)
    if yardline_100 is not None:
        value = min(value, float(yardline_100) + 9.0)  # back of the end zone
    return value


def estimate_air_yards(
    pass_length: str | None,
    pass_location: str | None,
    yards_gained: int | float | None,
    down: int | float | None,
    ydstogo: int | float | None,
    complete: bool,
    yardline_100: int | float | None = None,
) -> float:
    """Conditional median of air yards for a pass described only by prose.

    Backs off through `LEVELS` until it finds a fitted cell; with no table at
    all it degrades to the short/deep midpoints.  `yardline_100`, when known,
    only clips the answer to the field.
    """
    cells = _table()
    for key in cell_keys(pass_length, pass_location, yards_gained, down, ydstogo, complete):
        hit = cells.get(key)
        if hit is not None:
            return _clip(float(hit[0]), pass_length, yardline_100)
    fallback = {"short": 4.0, "deep": 24.0}.get(pass_length or "", 6.0)
    return _clip(fallback, pass_length, yardline_100)
