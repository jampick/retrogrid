"""Connecting a Yahoo league from the console (issue #26): files, sign-in, picker, guard."""
from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from retrogrid.providers import league as league_mod
from retrogrid.providers import yahoo, yahoo_api, yahoo_setup as ys
from retrogrid.providers.yahoo_api import YahooClient, YahooError

FIXTURES = Path(__file__).parent / "fixtures" / "yahoo"
CID = "dj0yJmk9QWJjRGVmR2hpSmtsJmQ9WVdrOWRHVnpkQS0t"
SECRET = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture(autouse=True)
def yahoo_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every file the setup touches, moved into tmp; no .env, no real keys."""
    d = tmp_path / "yahoo"
    for mod in (yahoo_api, ys):
        monkeypatch.setattr(mod, "APP_FILE", d / "app.json", raising=False)
        monkeypatch.setattr(mod, "TOKEN_FILE", d / "token.json", raising=False)
    monkeypatch.setattr(ys, "LEAGUE_FILE", d / "league.json")
    monkeypatch.setattr(yahoo, "LEAGUE_FILE", d / "league.json")
    monkeypatch.setattr(yahoo_api.paths, "env_files", lambda: [])
    for var in ("YAHOO_CLIENT_ID", "YAHOO_CLIENT_SECRET", "YAHOO_REDIRECT_URI", "RETROGRID_LEAGUE"):
        monkeypatch.delenv(var, raising=False)
    return d


def test_save_app_writes_a_private_file_and_reads_back(yahoo_dir: Path):
    assert ys.app_keys() is None and not ys.configured()
    ys.save_app(CID, SECRET)
    f = yahoo_dir / "app.json"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert ys.app_keys() == (CID, SECRET, ys.REDIRECT)
    assert yahoo_api.credentials() == (CID, SECRET)


def test_env_keys_win_over_the_file(monkeypatch: pytest.MonkeyPatch):
    ys.save_app(CID, SECRET)
    monkeypatch.setenv("YAHOO_CLIENT_ID", "envid")
    monkeypatch.setenv("YAHOO_CLIENT_SECRET", "envsecret")
    assert ys.app_keys()[:2] == ("envid", "envsecret") and ys.keys_from_env()


@pytest.mark.parametrize("cid,secret", [("short", SECRET), (CID, "not hex!"), (CID + " x", SECRET)])
def test_save_app_rejects_what_was_obviously_miscopied(cid: str, secret: str):
    with pytest.raises(YahooError):
        ys.save_app(cid, secret)


def test_new_keys_drop_the_old_apps_token(yahoo_dir: Path):
    ys.save_app(CID, SECRET)
    (yahoo_dir / "token.json").write_text("{}")
    ys.save_app(CID, SECRET)                                  # same app: token kept
    assert (yahoo_dir / "token.json").exists()
    ys.save_app(CID[::-1], SECRET)
    assert not (yahoo_dir / "token.json").exists()


def test_status_never_carries_the_secret():
    ys.save_app(CID, SECRET)
    s = json.dumps(ys.status(2026))
    assert SECRET not in s and CID not in s and ys.status(2026)["app"]


def test_auth_url_and_pasted_codes():
    ys.save_app(CID, SECRET)
    assert "redirect_uri=https%3A%2F%2Flocalhost%3A8443%2Fcallback" in ys.auth_url() and CID in ys.auth_url()
    assert ys.code_from("https://localhost:8443/callback?code=abc123xyz&state=") == "abc123xyz"
    assert ys.code_from("  abc123xyz \n") == "abc123xyz"
    with pytest.raises(YahooError):
        ys.code_from("https://localhost:8443/callback?error=access_denied")


def test_leagues_carry_the_logins_own_team():
    found = ys.leagues(YahooClient(raw_dir=FIXTURES, offline=True), 2026)
    assert found == [{"league_key": "461.l.777", "name": "Neon Sunday", "teams": "4",
                      "team_key": "461.l.777.t.1", "team_name": "Gridiron Ghosts"}]


def test_choose_then_forget(yahoo_dir: Path):
    ys.save_app(CID, SECRET)
    (yahoo_dir / "token.json").write_text("{}")
    ys.choose({"league_key": "461.l.777", "name": "Neon Sunday", "team_key": "461.l.777.t.1"}, 2026)
    assert ys.configured() and ys.status(2026)["league"]["name"] == "Neon Sunday"
    assert ys.status(2027)["league"] is None and ys.status(2027)["stale_league"]
    ys.forget()
    assert not ys.configured() and ys.app_keys() is not None
    ys.forget_app()
    assert ys.app_keys() is None


def test_explain_turns_yahoo_failures_into_actions():
    assert "enabled this app" in ys.explain(YahooError('GET league/x -> 403 {"description": "This application is not authorized to perform this action"}'))
    assert "Client ID / secret" in ys.explain(YahooError("token endpoint said 401 invalid_client"))
    assert "used already or has expired" in ys.explain(YahooError("token endpoint said 400 OAuth authorization code expired or invalid"))
    assert "couldn't reach Yahoo" in ys.explain(OSError("dns"))


# ── which league the console loads ────────────────────────────────────────

def test_league_kind_follows_setup(yahoo_dir: Path, monkeypatch: pytest.MonkeyPatch):
    assert league_mod.league_kind() == "off"
    ys.save_app(CID, SECRET)
    (yahoo_dir / "token.json").write_text("{}")
    ys.choose({"league_key": "461.l.777"}, 2026)
    assert league_mod.league_kind() == "yahoo"
    monkeypatch.setenv("RETROGRID_LEAGUE", "off")
    assert league_mod.league_kind() == "off"
    monkeypatch.setenv("RETROGRID_LEAGUE", "stub")
    assert league_mod.league_kind() == "stub"


def test_a_yahoo_failure_leaves_no_league_and_a_reason(yahoo_dir: Path, monkeypatch: pytest.MonkeyPatch):
    ys.save_app(CID, SECRET)
    (yahoo_dir / "token.json").write_text("{}")
    ys.choose({"league_key": "461.l.777"}, 2026)

    def boom(*a, **k):
        raise YahooError("GET league/461.l.777/settings -> 403 This application is not authorized to perform this action")
    monkeypatch.setattr(yahoo, "YahooLeagueProvider", boom)
    lg, why = league_mod.make_league("unused", None, 6, 2026)        # type: ignore[arg-type]
    assert lg is None and "enabled this app" in why
    lg, why = league_mod.make_league("unused", None, 15, 2025)       # last season's sim: not this league's business
    assert lg is None and why is None


# ── the HTTP guard ────────────────────────────────────────────────────────

def test_setup_routes_refuse_other_origins():
    from fastapi.testclient import TestClient
    from retrogrid.server import app
    c = TestClient(app, base_url="http://127.0.0.1:8082")
    assert c.get("/api/league").status_code == 200
    assert c.get("/api/league", headers={"origin": "http://127.0.0.1:8082"}).status_code == 200
    assert c.post("/api/league/app", json={"client_id": CID, "client_secret": SECRET},
                  headers={"origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/league/signout", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert TestClient(app, base_url="http://rebind.example:8082").get("/api/league").status_code == 403
    assert ys.app_keys() is None                              # nothing was written by the refused calls
