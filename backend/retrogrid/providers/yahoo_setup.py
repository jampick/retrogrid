"""Connecting a Yahoo league from inside the console (issue #26).

Everything the setup screen needs, none of it async and none of it aware of
the engine: where the app keys live, the sign-in URL, the code exchange, the
leagues this login has, and the choice of one. The server wraps these in
/api/league/* and reloads the engine once a league is picked.

Each user brings their own Yahoo app. Since July 2026 Yahoo only serves the
Fantasy API to reviewed apps, and a client secret shipped inside a public
package would not stay secret, so there is no shared app.

Files under <DATA>/yahoo/, all chmod 600:
    app.json     client_id, client_secret, redirect_uri  (.env still wins)
    token.json   the OAuth token (YahooAuth)
    league.json  league_key, name, season, team_key, team_name
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from .yahoo_api import APP_FILE, AUTH_URL, TOKEN_FILE, YAHOO_DIR, YahooAuth, YahooClient, YahooError, app_keys, load_env, many

log = logging.getLogger("retrogrid.yahoo")
LEAGUE_FILE = YAHOO_DIR / "league.json"
REDIRECT = "https://localhost:8443/callback"     # what the setup screen tells people to register

ACCESS_URL = "https://sports.yahoo.com/developer/access/"
CREATE_URL = "https://developer.yahoo.com/apps/create/"


def write_private(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.touch(mode=0o600)
    tmp.chmod(0o600)
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


# ── the app's keys ────────────────────────────────────────────────────────

def keys_from_env() -> bool:
    load_env()
    return bool(os.environ.get("YAHOO_CLIENT_ID") and os.environ.get("YAHOO_CLIENT_SECRET"))


def save_app(client_id: str, secret: str, redirect_uri: str = REDIRECT) -> None:
    """New keys mean a new app: any token from the old one is useless, so it goes."""
    client_id, secret, redirect_uri = client_id.strip(), secret.strip(), (redirect_uri or REDIRECT).strip()
    if not re.fullmatch(r"[A-Za-z0-9+/=_\-.]{20,}", client_id):
        raise YahooError("that Client ID doesn't look right: copy the long one from the app page")
    if not re.fullmatch(r"[A-Za-z0-9]{16,}", secret):
        raise YahooError("that Client Secret doesn't look right: it is the shorter hex string under the Client ID")
    if not (redirect_uri == "oob" or redirect_uri.startswith("https://")):
        raise YahooError("Yahoo only accepts an https:// redirect URI (or oob)")
    old = read_json(APP_FILE) or {}
    if old.get("client_id") != client_id:
        TOKEN_FILE.unlink(missing_ok=True)
    write_private(APP_FILE, {"client_id": client_id, "client_secret": secret, "redirect_uri": redirect_uri})


def forget() -> None:
    """SIGN OUT: the token and the league go, the app's keys stay."""
    TOKEN_FILE.unlink(missing_ok=True)
    LEAGUE_FILE.unlink(missing_ok=True)


def forget_app() -> None:
    forget()
    APP_FILE.unlink(missing_ok=True)


# ── signing in ────────────────────────────────────────────────────────────

def auth_url() -> str:
    keys = app_keys()
    if keys is None:
        raise YahooError("save the app's Client ID and secret first")
    cid, _, redirect = keys
    return f"{AUTH_URL}?{urlencode({'client_id': cid, 'redirect_uri': redirect, 'response_type': 'code'})}"


def code_from(pasted: str) -> str:
    """The code out of whatever was pasted: the failed callback URL, or the bare code."""
    pasted = pasted.strip()
    if "code=" in pasted:
        code = parse_qs(urlparse(pasted).query).get("code", [""])[0]
    else:
        code = pasted
    if not re.fullmatch(r"[A-Za-z0-9_\-.~]{4,}", code):
        raise YahooError("paste the whole address of the page Yahoo sent you to (it has code= in it)")
    return code


def sign_in(pasted: str) -> None:
    keys = app_keys()
    if keys is None:
        raise YahooError("save the app's Client ID and secret first")
    YahooAuth().exchange({"grant_type": "authorization_code", "code": code_from(pasted), "redirect_uri": keys[2]})


# ── picking a league ──────────────────────────────────────────────────────

def leagues(client: YahooClient, season: int) -> list[dict[str, str]]:
    """This login's NFL leagues for the season, each with the login's own team in it."""
    games = many(client.get(f"games;game_codes=nfl;seasons={season}").get("games"), "game")
    if not games:
        raise YahooError(f"Yahoo has no NFL game for {season} yet")
    key = games[0]["game_key"]
    teams: dict[str, dict[str, str]] = {}
    for user in many(client.get(f"users;use_login=1/games;game_keys={key}/teams").get("users"), "user"):
        for game in many(user.get("games"), "game"):
            for t in many(game.get("teams"), "team"):
                teams[t["team_key"].split(".t.")[0]] = {"team_key": t["team_key"], "team_name": str(t.get("name", ""))}
    out = []
    for user in many(client.get(f"users;use_login=1/games;game_keys={key}/leagues").get("users"), "user"):
        for game in many(user.get("games"), "game"):
            for lg in many(game.get("leagues"), "league"):
                mine = teams.get(lg["league_key"], {})
                out.append({"league_key": lg["league_key"], "name": str(lg.get("name", "?")),
                            "teams": str(lg.get("num_teams", "")), **mine})
    return out


def choose(league: dict[str, str], season: int) -> dict[str, Any]:
    picked = {"league_key": league["league_key"], "name": league.get("name", ""), "season": season,
              "team_key": league.get("team_key"), "team_name": league.get("team_name")}
    write_private(LEAGUE_FILE, picked)
    return picked


def chosen() -> dict[str, Any] | None:
    return read_json(LEAGUE_FILE)


def configured() -> bool:
    """Enough on disk to load a league without asking anything."""
    return app_keys() is not None and TOKEN_FILE.is_file() and LEAGUE_FILE.is_file()


# ── what went wrong, in words ─────────────────────────────────────────────

def explain(e: Exception) -> str:
    """Yahoo's failures as something a person can act on."""
    text = str(e)
    if "not authorized" in text.lower() or " 403" in text:
        return ("Yahoo hasn't enabled this app for the Fantasy API yet. Apply at "
                f"{ACCESS_URL} and, once approved, send them this app's Client ID. It can take days.")
    if "invalid_client" in text or "401" in text and "token endpoint" in text:
        return "Yahoo didn't accept the Client ID / secret pair. Copy both again from the app page."
    if "invalid_grant" in text or "authorization code" in text.lower():
        return "That sign-in code was used already or has expired. Open the sign-in page again and paste the new address."
    if "redirect" in text.lower():
        return f"The redirect URI on the Yahoo app must be exactly {REDIRECT}."
    if "no Yahoo token" in text:
        return "Not signed in to Yahoo."
    if isinstance(e, YahooError):
        return text
    return f"couldn't reach Yahoo ({type(e).__name__})"


def status(season: int, error: str | None = None) -> dict[str, Any]:
    """What the setup screen shows. Never includes the secret or a token."""
    keys = app_keys()
    league = chosen()
    return {
        "app": keys is not None,
        "client_id": f"{keys[0][:6]}…{keys[0][-4:]}" if keys else None,
        "from_env": keys_from_env(),
        "redirect_uri": keys[2] if keys else REDIRECT,
        "signed_in": TOKEN_FILE.is_file(),
        "league": league if league and league.get("season") == season else None,
        "stale_league": league if league and league.get("season") != season else None,
        "season": season, "error": error,
        "links": {"access": ACCESS_URL, "create": CREATE_URL},
    }
