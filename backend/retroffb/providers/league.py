"""Which league the console runs on: RETROFFB_LEAGUE=yahoo, else the synthetic one."""
from __future__ import annotations

import os
from pathlib import Path

from .base import LeagueProvider
from .directory import NflversePlayerDirectory
from .yahoo_api import load_env
from .stub_league import USER_TEAM, SyntheticLeagueProvider


def make_league(slate_dir: Path | str, directory: NflversePlayerDirectory, week: int, season: int,
                seed: int = 1) -> LeagueProvider:
    load_env()                                                # RETROFFB_LEAGUE may live in .env too
    if os.environ.get("RETROFFB_LEAGUE", "").lower() == "yahoo":
        from .yahoo import YahooLeagueProvider
        from .yahoo_api import YahooClient
        return YahooLeagueProvider(YahooClient(), directory, week=week, season=season)
    return SyntheticLeagueProvider.from_slate(seed=seed, slate_dir=slate_dir, directory=directory)


def default_viewer(league: LeagueProvider) -> str:
    """The team a fresh session sees: the login's own, else the first."""
    teams = [t.key for t in league.league().teams]
    mine = getattr(league, "viewer", None) or USER_TEAM
    return mine if mine in teams else teams[0]
