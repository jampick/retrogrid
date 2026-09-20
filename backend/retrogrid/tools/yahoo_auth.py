#!/usr/bin/env python
"""One-time Yahoo sign-in (docs/YAHOO.md). Stores yahoo/token.json in the data dir; the
server refreshes it from then on.

  retrogrid yahoo-auth            sign in, then pick the league
  retrogrid yahoo-auth --league   re-pick the league with the stored token
  retrogrid yahoo-auth --check    show what the adapter sees, change nothing

Needs YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET in .env. The redirect URI must be
the one registered on the Yahoo app: `oob` by default (Yahoo shows a code to
paste), or set YAHOO_REDIRECT_URI=https://localhost:8443/callback — nothing
listens there; the browser fails to load the page and you paste its URL here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from datetime import date
from urllib.parse import parse_qs, urlencode, urlparse

from ..providers.yahoo import LEAGUE_FILE, list_leagues
from ..providers.yahoo_api import AUTH_URL, YahooAuth, YahooClient, YahooError, credentials


def sign_in(auth: YahooAuth) -> None:
    cid, _ = credentials()
    redirect = os.environ.get("YAHOO_REDIRECT_URI", "oob")
    url = f"{AUTH_URL}?{urlencode({'client_id': cid, 'redirect_uri': redirect, 'response_type': 'code'})}"
    print(f"\nOpen this, approve read access:\n\n  {url}\n")
    webbrowser.open(url)
    pasted = input("Paste the code (or the whole URL you were sent to): ").strip()
    code = parse_qs(urlparse(pasted).query).get("code", [pasted])[0]
    auth.exchange({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect})
    print(f"token stored -> {auth.token_file}")


def choose_league(client: YahooClient, season: int) -> None:
    leagues = list_leagues(client, season)
    if not leagues:
        raise YahooError(f"this login has no NFL leagues for {season}")
    for i, (key, name) in enumerate(leagues, 1):
        print(f"  {i}. {name}  [{key}]")
    n = 1 if len(leagues) == 1 else int(input("Which league? "))
    key, name = leagues[n - 1]
    LEAGUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    LEAGUE_FILE.write_text(json.dumps({"league_key": key, "name": name, "season": season}))
    print(f"league: {name} -> {LEAGUE_FILE}")


def check(client: YahooClient, season: int, week: int) -> None:
    import logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from retrogrid.providers.directory import NflversePlayerDirectory
    from retrogrid.providers.yahoo import YahooLeagueProvider
    directory = NflversePlayerDirectory.from_data_dir(week=week, season=season)
    lg = YahooLeagueProvider(client, directory, week=week, season=season)
    print(f"\n{lg.league().name}  week {week}  you = {lg.viewer}")
    print("rules:", json.dumps(lg.league().scoring_rules))
    for m in lg.matchups(week):
        for k in (m.a, m.b):
            team = next(t for t in lg.league().teams if t.key == k)
            starters = [f"{s.slot} {p.name if (p := directory.player(s.player_id)) else s.player_id}" for s in lg.roster(k, week).starters()]
            print(f"  {team.name} ({team.owner}) official {lg.official_total(k)}: " + ", ".join(starters))
        print()
    print(f"{client.calls} API calls")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--league", action="store_true", help="only re-pick the league")
    ap.add_argument("--check", action="store_true", help="load the league and print it")
    ap.add_argument("--season", type=int, default=date.today().year if date.today().month >= 8 else date.today().year - 1)
    ap.add_argument("--week", type=int, default=1, help="week for --check")
    args = ap.parse_args(argv)
    auth = YahooAuth()
    try:
        if args.check:
            return check(YahooClient(auth), args.season, args.week)
        if not args.league:
            sign_in(auth)
        choose_league(YahooClient(auth), args.season)
    except YahooError as e:
        sys.exit(f"yahoo: {e}")


if __name__ == "__main__":
    main()
