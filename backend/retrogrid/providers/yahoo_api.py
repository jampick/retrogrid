"""Yahoo Fantasy Sports API plumbing (DESIGN §9, §12; docs/YAHOO.md).

Three things, none of which know what a league is:

* `flatten` — Yahoo's JSON is XML in disguise: records arrive as lists of
  single-key dicts, collections as `{"0": {...}, "1": {...}, "count": 2}`.
  One pass turns that into plain dicts and lists.
* `YahooAuth` — OAuth2 token file, refreshed ahead of expiry and on a 401.
* `YahooClient` — GET with every raw response cached under data/yahoo/raw/,
  so tests and offline dev replay fixtures instead of hitting the API.

Secrets and tokens are never logged.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

from .. import paths

log = logging.getLogger("retrogrid.yahoo")
API = "https://fantasysports.yahooapis.com/fantasy/v2"
AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
YAHOO_DIR = paths.DATA / "yahoo"
TOKEN_FILE = YAHOO_DIR / "token.json"
RAW_DIR = YAHOO_DIR / "raw"
EXPIRY_MARGIN = 120.0              # refresh this many seconds before the hour is up


class YahooError(RuntimeError):
    pass


# ── flattening ────────────────────────────────────────────────────────────

def flatten(node: Any) -> Any:
    """Yahoo JSON -> plain data.

    * `{"0": x, "1": y, "count": 2}` -> `[x, y]`, each unwrapped from its
      single-key envelope (`{"team": ...}` -> the team);
    * a numeric key *without* a count is a nameless wrapper
      (`roster: {"week": 2, "0": {"players": ...}}`): its contents join the parent;
    * a list of dicts with distinct keys is one record in pieces -> merged;
    * a list of same-key envelopes (`[{"stat": a}, {"stat": b}]`) -> `[a, b]`.

    A one-element list is ambiguous (record or list of one): it becomes a
    record. Read anything that may repeat through :func:`many`.
    """
    if isinstance(node, dict):
        numeric = sorted((k for k in node if k.isdigit()), key=int)
        if "count" in node and (numeric or node["count"] in (0, "0")):
            return [_unwrap(flatten(node[k])) for k in numeric]
        out = {k: flatten(v) for k, v in node.items() if not k.isdigit()}
        for k in numeric:
            inner = flatten(node[k])
            if isinstance(inner, dict):
                out.update(inner)
        return out
    if isinstance(node, list):
        parts = [p for p in (flatten(x) for x in node) if p not in ([], {}, None)]
        if parts and all(isinstance(p, dict) for p in parts):
            keys = [k for p in parts for k in p]
            if len(keys) == len(set(keys)):
                return {k: v for p in parts for k, v in p.items()}
            if len(set(keys)) == 1 and all(len(p) == 1 for p in parts):
                return [p[keys[0]] for p in parts]
        return parts
    return node


def _unwrap(item: Any) -> Any:
    if isinstance(item, dict) and len(item) == 1:
        return next(iter(item.values()))
    return item


def many(node: Any, key: str) -> list[Any]:
    """Read a repeating element that `flatten` may have collapsed:
    `[a, b]`, `{key: a}` and `a` all come back as a list."""
    if node in (None, "", [], {}):
        return []
    if isinstance(node, list):
        return [x[key] if isinstance(x, dict) and set(x) == {key} else x for x in node]
    if isinstance(node, dict) and set(node) == {key}:
        return [node[key]]
    return [node]


# ── credentials + tokens ──────────────────────────────────────────────────

def load_env(path: Path | None = None) -> None:
    """Minimal .env reader (KEY=value lines); the real environment wins."""
    for f in ([path] if path else paths.env_files()):
        if f.is_file():
            _load_env_file(f)


def _load_env_file(path: Path) -> None:
    for line in path.read_text().splitlines():
        m = re.match(r"\s*(?:export\s+)?([A-Za-z_]\w*)\s*=\s*(.*?)\s*$", line)
        if m and not line.lstrip().startswith("#"):
            os.environ.setdefault(m.group(1), m.group(2).strip("'\""))


def credentials() -> tuple[str, str]:
    load_env()
    cid, secret = os.environ.get("YAHOO_CLIENT_ID"), os.environ.get("YAHOO_CLIENT_SECRET")
    if not cid or not secret:
        raise YahooError("YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET not set — put them in .env (see docs/YAHOO.md)")
    return cid, secret


class YahooAuth:
    def __init__(self, token_file: Path = TOKEN_FILE) -> None:
        self.token_file = token_file
        self._token: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._token is None:
            if not self.token_file.is_file():
                raise YahooError("no Yahoo token — run scripts/yahoo_auth.py once")
            self._token = json.loads(self.token_file.read_text())
        return self._token

    def store(self, payload: dict[str, Any]) -> None:
        """Persist a token-endpoint response. Yahoo omits nothing on refresh,
        but keep the old refresh token if it ever does."""
        old = self._token or {}
        self._token = {
            "access_token": payload["access_token"],
            "refresh_token": payload.get("refresh_token") or old.get("refresh_token"),
            "expires_at": time.time() + float(payload.get("expires_in", 3600)),
        }
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(json.dumps(self._token))
        self.token_file.chmod(0o600)

    def exchange(self, grant: dict[str, str]) -> None:
        cid, secret = credentials()
        r = httpx.post(TOKEN_URL, data=grant, auth=(cid, secret), timeout=20.0)
        if r.status_code != 200:
            detail = r.json().get("error_description", "") if "json" in r.headers.get("content-type", "") else ""
            raise YahooError(f"token endpoint said {r.status_code} {detail}".strip())
        self.store(r.json())

    def refresh(self) -> None:
        token = self._load()
        self.exchange({"grant_type": "refresh_token", "refresh_token": token["refresh_token"],
                       "redirect_uri": os.environ.get("YAHOO_REDIRECT_URI", "oob")})
        log.info("yahoo access token refreshed")

    def access_token(self) -> str:
        if self._load().get("expires_at", 0) - time.time() < EXPIRY_MARGIN:
            self.refresh()
        return self._load()["access_token"]


# ── client ────────────────────────────────────────────────────────────────

def slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "_", path).strip("_")


class YahooClient:
    """`get(path)` -> flattened `fantasy_content`. With `offline=True` (or
    RETROGRID_YAHOO_REPLAY=1) responses come from `raw_dir` only."""

    def __init__(self, auth: YahooAuth | None = None, raw_dir: Path = RAW_DIR, offline: bool | None = None) -> None:
        self.offline = os.environ.get("RETROGRID_YAHOO_REPLAY") == "1" if offline is None else offline
        self.auth = auth or (None if self.offline else YahooAuth())
        self.raw_dir = raw_dir
        self.calls = 0

    def get(self, path: str) -> dict[str, Any]:
        cache = self.raw_dir / f"{slug(path)}.json"
        if self.offline:
            if not cache.is_file():
                raise YahooError(f"replay: no cached response for {path} ({cache.name})")
            raw = json.loads(cache.read_text())
        else:
            raw = self._fetch(path)
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(raw))
        return flatten(raw.get("fantasy_content", {}))

    def _fetch(self, path: str) -> dict[str, Any]:
        assert self.auth is not None
        url = f"{API}/{path.lstrip('/')}"
        for attempt in (1, 2):
            r = httpx.get(url, params={"format": "json"}, timeout=20.0,
                          headers={"Authorization": f"Bearer {self.auth.access_token()}"})
            self.calls += 1
            if r.status_code == 401 and attempt == 1:
                self.auth.refresh()
                continue
            if r.status_code != 200:
                raise YahooError(f"GET {path} -> {r.status_code} {r.text[:200]}")
            return r.json()
        raise YahooError(f"GET {path} -> 401 after refresh")
