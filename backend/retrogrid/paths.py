"""Where things live. Two roots:

PKG   read-only, ships in the wheel: the web frontend and the prebuilt SIM
      SUNDAY bundle (slate, sprites, player directory snapshot).
DATA  writable, per user: fetched nflverse files, today's live slate, built
      sprites, Yahoo tokens, overrides. `RETROGRID_DATA` wins; a source
      checkout keeps using its own ./data; an install gets the platform's
      user data dir (~/.local/share/retrogrid and friends).
"""
from __future__ import annotations

import os
from pathlib import Path

PKG = Path(__file__).resolve().parent
WEB = PKG / "web"
BUNDLED = PKG / "bundled"
CHECKOUT = PKG.parents[1] if (PKG.parents[1] / "pyproject.toml").is_file() else None


def _data_root() -> Path:
    if env := os.environ.get("RETROGRID_DATA"):
        return Path(env).expanduser()
    if CHECKOUT is not None:
        return CHECKOUT / "data"
    from platformdirs import user_data_dir
    return Path(user_data_dir("retrogrid", appauthor=False))


DATA = _data_root()
NFLVERSE = DATA / "nflverse"
LIVE_SLATE = DATA / "live"
SPRITES = DATA / "sprites"
HEADSHOTS = DATA / "headshots"
BUNDLED_SLATE = BUNDLED / "slate"
BUNDLED_SPRITES = BUNDLED / "sprites"
BUNDLED_DIRECTORY = BUNDLED / "directory.json"


def sim_slate() -> Path:
    """A slate you built yourself wins; otherwise the one that shipped."""
    own = DATA / "slate"
    return own if (own / "slate.json").exists() and (own / "plays.jsonl").exists() else BUNDLED_SLATE


def sprite(player_id: str) -> Path | None:
    for d in (SPRITES, BUNDLED_SPRITES):
        if (p := d / f"{player_id}.png").is_file():
            return p
    return None


def env_files() -> list[Path]:
    """.env candidates, first hit per key wins: the working dir, then DATA."""
    return [Path.cwd() / ".env", DATA / ".env"]
