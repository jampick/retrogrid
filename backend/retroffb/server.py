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


@app.middleware("http")
async def revalidate_bundles(request, call_next):             # noqa: ANN001, ANN201
    """The bundle is rebuilt constantly; make the browser revalidate (ETag) instead of guessing freshness."""
    response = await call_next(request)
    if request.url.path.startswith("/dist/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


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


@app.get("/api/plays/sample")
def api_plays_sample(n: int = 24, seed: int = 0, family: str | None = None, viewer: str = "t01",
                     play: str | None = None, grep: str | None = None) -> JSONResponse:
    from .console import sample_plays
    return JSONResponse(sample_plays(max(1, min(n, 96)), seed, family, viewer, play, grep))


FLAGS = ROOT / "data" / "grammar_flags.json"


def _flags() -> dict:
    try:
        return json.loads(FLAGS.read_text())
    except (OSError, ValueError):
        return {}


@app.get("/api/plays/flags")
def api_plays_flags() -> JSONResponse:
    return JSONResponse(_flags())


@app.post("/api/plays/flag")
def api_plays_flag(body: dict) -> JSONResponse:
    """The eyeball's worklist: plays whose animation looks wrong, with a note."""
    flags = _flags()
    pid = str(body.get("play_id", ""))
    if body.get("on") and pid:
        flags[pid] = {k: str(body.get(k, ""))[:400] for k in ("template", "desc", "note")}
    else:
        flags.pop(pid, None)
    FLAGS.parent.mkdir(parents=True, exist_ok=True)
    FLAGS.write_text(json.dumps(flags, indent=1))
    return JSONResponse(flags)


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


@app.get("/plays")
def plays_sheet() -> FileResponse:
    return FileResponse(FRONTEND / "plays.html")


app.mount("/dist", StaticFiles(directory=FRONTEND / "dist", check_dir=False), name="dist")
app.mount("/static", StaticFiles(directory=FRONTEND / "static", check_dir=False), name="static")
app.mount("/sprites", StaticFiles(directory=ROOT / "data" / "sprites", check_dir=False), name="sprites")
