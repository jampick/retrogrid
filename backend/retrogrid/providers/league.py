"""The fantasy layer is opt-in. RETROGRID_LEAGUE picks it:

    (unset)  a Yahoo league if one is set up (the console's [Y] screen), else none
    off      no league: the console is a plain NFL monitor
    stub     the synthetic 12-team league drafted from the slate
    yahoo    the Yahoo league, and say so loudly if it is not set up

A Yahoo league only loads onto a slate of its own season, and a Yahoo failure
(no network, a revoked token, an app Yahoo has not enabled) leaves the console
up without a league: `make_league` returns the reason instead of raising.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from .base import LeagueProvider
from .directory import NflversePlayerDirectory
from .yahoo_api import load_env
from .stub_league import USER_TEAM, SyntheticLeagueProvider

log = logging.getLogger("retrogrid.league")


def league_kind() -> str:
    load_env()                                                # RETROGRID_LEAGUE may live in .env too
    kind = os.environ.get("RETROGRID_LEAGUE", "").lower()
    if kind in ("0", "off", "none"):
        return "off"
    if kind in ("stub", "yahoo"):
        return kind
    from .yahoo_setup import configured
    return "yahoo" if configured() else "off"


def make_league(slate_dir: Path | str, directory: NflversePlayerDirectory, week: int, season: int,
                seed: int = 1) -> tuple[LeagueProvider | None, str | None]:
    """(league, why there is none). The reason is for the setup screen, in words."""
    kind = league_kind()
    if kind == "off":
        return None, None
    if kind == "stub":
        return SyntheticLeagueProvider.from_slate(seed=seed, slate_dir=slate_dir, directory=directory), None
    from .yahoo import YahooLeagueProvider
    from .yahoo_api import YahooClient
    from .yahoo_setup import chosen, explain
    picked = chosen() or {}
    if picked.get("season") not in (None, season):
        return None, None                                     # last season's sim: this season's league means nothing there
    try:
        return YahooLeagueProvider(YahooClient(), directory, week=week, season=season), None
    except Exception as e:                                    # noqa: BLE001 — the NFL monitor outranks the league
        why = explain(e)
        log.warning("yahoo league not loaded: %s", why)
        return None, why


def default_viewer(league: LeagueProvider) -> str:
    """The team a fresh session sees: the login's own, else the first."""
    teams = [t.key for t in league.league().teams]
    mine = getattr(league, "viewer", None) or USER_TEAM
    return mine if mine in teams else teams[0]
