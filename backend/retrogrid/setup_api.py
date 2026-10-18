"""/api/league/*: the console's Yahoo setup screen (issue #26).

Every route here can write keys or tokens to disk, so each one checks that
the request came from the console's own page: the Host must be the loopback
address we serve on (no DNS rebinding) and a browser's Origin, when sent,
must be that same host. A web page elsewhere can still fire a request at
127.0.0.1, but it is refused before anything is read or written.

Yahoo calls block for a second or two; they run in a thread. Picking a
league, signing out or forgetting the keys rebuilds the engine under every
open window (watch.reload).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from .providers import yahoo_setup as ys
from .providers.yahoo_api import YahooClient

log = logging.getLogger("retrogrid.setup")
router = APIRouter(prefix="/api/league")
LOOPBACK = ("127.0.0.1", "localhost", "[::1]")


def same_origin(request: Request) -> None:
    host = request.headers.get("host", "")
    if host.rsplit(":", 1)[0] not in LOOPBACK:
        raise HTTPException(403, "setup only answers on the loopback address")
    origin = request.headers.get("origin")
    if origin is not None and origin != f"{request.url.scheme}://{host}":
        raise HTTPException(403, "setup only answers the console's own page")
    if request.headers.get("sec-fetch-site") not in (None, "same-origin", "none"):
        raise HTTPException(403, "setup only answers the console's own page")


def season() -> int:
    from . import console
    from .watch import season_now
    return console.engine.slate.season if console.engine is not None and hasattr(console.engine, "slate") else season_now()


def snapshot() -> dict[str, Any]:
    from . import console
    e = console.engine
    out = ys.status(season(), getattr(e, "league_error", None))
    lg = getattr(e, "league", None)
    out["loaded"] = None if lg is None else {"name": lg.league().name, "week": e.week,
                                             "kind": "yahoo" if hasattr(lg, "league_key") else "stub"}
    from . import watch
    out["reloading"] = watch.busy == "league"
    return out


def fail(e: Exception) -> JSONResponse:
    why = ys.explain(e)
    log.warning("setup: %s", why)
    return JSONResponse({**snapshot(), "error": why}, status_code=400)


async def reload() -> None:
    from . import watch
    asyncio.create_task(watch.reload_league())


@router.get("")
def get_status(request: Request) -> dict[str, Any]:
    same_origin(request)
    return snapshot()


@router.post("/app")
async def post_app(request: Request) -> JSONResponse:
    same_origin(request)
    body = await request.json()
    try:
        ys.save_app(str(body.get("client_id", "")), str(body.get("client_secret", "")),
                    str(body.get("redirect_uri") or ys.REDIRECT))
        return JSONResponse({**snapshot(), "auth_url": ys.auth_url()})
    except Exception as e:                                    # noqa: BLE001
        return fail(e)


@router.get("/signin")
def get_signin(request: Request) -> JSONResponse:
    same_origin(request)
    try:
        return JSONResponse({"auth_url": ys.auth_url()})
    except Exception as e:                                    # noqa: BLE001
        return fail(e)


@router.post("/signin")
async def post_signin(request: Request) -> JSONResponse:
    """Exchange the pasted code, then list the leagues straight away: that list is
    the first real Fantasy API call, so an app Yahoo has not enabled shows up here."""
    same_origin(request)
    body = await request.json()
    try:
        await asyncio.to_thread(ys.sign_in, str(body.get("pasted", "")))
        found = await asyncio.to_thread(ys.leagues, YahooClient(), season())
        return JSONResponse({**snapshot(), "leagues": found})
    except Exception as e:                                    # noqa: BLE001
        return fail(e)


@router.get("/leagues")
async def get_leagues(request: Request) -> JSONResponse:
    same_origin(request)
    try:
        return JSONResponse({**snapshot(), "leagues": await asyncio.to_thread(ys.leagues, YahooClient(), season())})
    except Exception as e:                                    # noqa: BLE001
        return fail(e)


@router.post("/choose")
async def post_choose(request: Request) -> JSONResponse:
    same_origin(request)
    body = await request.json()
    try:
        found = await asyncio.to_thread(ys.leagues, YahooClient(), season())
        pick = next((lg for lg in found if lg["league_key"] == body.get("league_key")), None)
        if pick is None:
            raise ys.YahooError("that league isn't on this Yahoo login")
        ys.choose(pick, season())
    except Exception as e:                                    # noqa: BLE001
        return fail(e)
    await reload()
    return JSONResponse({**snapshot(), "reloading": True})


@router.post("/signout")
async def post_signout(request: Request) -> JSONResponse:
    same_origin(request)
    ys.forget()
    await reload()
    return JSONResponse({**snapshot(), "reloading": True})


@router.post("/forget")
async def post_forget(request: Request) -> JSONResponse:
    same_origin(request)
    ys.forget_app()
    await reload()
    return JSONResponse({**snapshot(), "reloading": True})


@router.post("/retry")
async def post_retry(request: Request) -> JSONResponse:
    """Load the league again after Yahoo said no (network back, app enabled)."""
    same_origin(request)
    await reload()
    return JSONResponse({**snapshot(), "reloading": True})
