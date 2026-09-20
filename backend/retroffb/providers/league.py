"""The fantasy layer is opt-in. RETROFFB_LEAGUE picks it:

    (unset)  no league — the console is a plain NFL monitor
    stub     the synthetic 12-team league drafted from the slate
    yahoo    a real Yahoo league (docs/YAHOO.md)
"""
from __future__ import annotations

import os
from pathlib import Path

from .base import LeagueProvider
from .directory import NflversePlayerDirectory
from .yahoo_api import load_env
from .stub_league import USER_TEAM, SyntheticLeagueProvider


def make_league(slate_dir: Path | str, directory: NflversePlayerDirectory, week: int, season: int,
                seed: int = 1) -> LeagueProvider | None:
    load_env()                                                # RETROFFB_LEAGUE may live in .env too
    kind = os.environ.get("RETROFFB_LEAGUE", "").lower()
    if kind in ("", "0", "off", "none"):
        return None
    if kind == "yahoo":
        from .yahoo import YahooLeagueProvider
        from .yahoo_api import YahooClient
        return YahooLeagueProvider(YahooClient(), directory, week=week, season=season)
    return SyntheticLeagueProvider.from_slate(seed=seed, slate_dir=slate_dir, directory=directory)


def default_viewer(league: LeagueProvider) -> str:
    """The team a fresh session sees: the login's own, else the first."""
    teams = [t.key for t in league.league().teams]
    mine = getattr(league, "viewer", None) or USER_TEAM
    return mine if mine in teams else teams[0]
