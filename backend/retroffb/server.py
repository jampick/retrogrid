"""RETRO//FFB console server: static frontend + one WebSocket per viewer."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .theme.provider import BundledThemeProvider, OmarchyThemeProvider

log = logging.getLogger("retroffb")
ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"


class Hub:
    """Fan-out of server frames to every connected viewer."""

    def __init__(self) -> None:
        self.sockets: set[WebSocket] = set()

    async def broadcast(self, frame: dict) -> None:
        data = json.dumps(frame)
        for ws in list(self.sockets):
            try:
                await ws.send_text(data)
            except Exception:
                self.sockets.discard(ws)


hub = Hub()
bundled = BundledThemeProvider()
system = OmarchyThemeProvider()


async def _theme_pump() -> None:
    async for theme in system.watch():
        log.info("system theme -> %s", theme.name)
        await hub.broadcast({"type": "theme", "system": theme.to_dict()})


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [asyncio.create_task(_theme_pump())] if system.available else []
    try:
        from .console import start as start_console      # game engine, optional until data exists
        tasks += await start_console(hub)
    except Exception as e:                                # noqa: BLE001
        log.warning("console engine not started: %s", e)
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(lifespan=lifespan)


def themes_payload() -> dict:
    return {
        "system": system.current().to_dict() if system.available else None,
        "themes": [t.to_dict() for t in bundled.all()],
    }


@app.get("/api/themes")
def api_themes() -> JSONResponse:
    return JSONResponse(themes_payload())


@app.post("/api/theme/poke")
def api_theme_poke() -> dict:
    system.poke()
    return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    hub.sockets.add(ws)
    try:
        await ws.send_text(json.dumps({"type": "hello", **themes_payload()}))
        try:
            from .console import on_connect
            await on_connect(ws)
        except ImportError:
            pass
        while True:
            msg = json.loads(await ws.receive_text())
            try:
                from .console import on_message
                await on_message(ws, msg)
            except ImportError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        hub.sockets.discard(ws)
        try:
            from .console import on_disconnect
            on_disconnect(ws)
        except ImportError:
            pass


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


@app.get("/themes")
def sheet() -> FileResponse:
    return FileResponse(FRONTEND / "sheet.html")


app.mount("/dist", StaticFiles(directory=FRONTEND / "dist", check_dir=False), name="dist")
app.mount("/static", StaticFiles(directory=FRONTEND / "static", check_dir=False), name="static")
app.mount("/sprites", StaticFiles(directory=ROOT / "data" / "sprites", check_dir=False), name="sprites")
