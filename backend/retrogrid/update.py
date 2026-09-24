"""One quiet check for a newer release, so a hand-installed package can say when it is stale.

Nothing here installs anything. `check()` asks the GitHub releases API for the
latest tag once per process, in the background, and gives up silently on any
error. `RETROGRID_NO_UPDATE_CHECK=1` skips it. The console shows the result as
one line above the key legend, with the command that fits how this copy was
installed: a package under /usr updates through pacman, anything else through
pip.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from importlib import metadata

import httpx

log = logging.getLogger("retrogrid.update")

RELEASES = "https://api.github.com/repos/jampick/retrogrid/releases/latest"
_result: dict | None = None
_done = False


def current() -> str:
    try:
        return metadata.version("retrogrid")
    except metadata.PackageNotFoundError:
        return "0"


def hint() -> str:
    """The upgrade command for this install. pacman owns anything under /usr."""
    return "omarchy-update" if __file__.startswith("/usr/") else "pip install -U retrogrid"


def _key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def newer(latest: str, installed: str) -> bool:
    return _key(latest) > _key(installed)


def result() -> dict | None:
    """What the hello frame carries: None until the check has found something newer."""
    return _result


async def check() -> dict | None:
    global _result, _done
    if _done or os.environ.get("RETROGRID_NO_UPDATE_CHECK"):
        return _result
    _done = True
    try:
        async with httpx.AsyncClient(timeout=4.0, headers={"Accept": "application/vnd.github+json"}) as c:
            r = await c.get(RELEASES)
            r.raise_for_status()
            tag = r.json().get("tag_name", "")
    except Exception as e:                                # noqa: BLE001
        log.debug("update check skipped: %s", e)
        return None
    latest = tag.lstrip("v")
    if newer(latest, current()):
        _result = {"latest": latest, "current": current(), "hint": hint()}
        log.info("v%s available (this is v%s)", latest, current())
    return _result


async def announce(hub) -> None:                          # noqa: ANN001
    """Run the check and, if it finds something, tell every console already connected."""
    await asyncio.sleep(2)                                # let the window come up first
    if await check():
        await hub.broadcast({"type": "update", **_result})
