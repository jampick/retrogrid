"""`retrogrid` — the one command an install needs.

    retrogrid                 SIM SUNDAY: the shipped slate, no downloads, no keys
    retrogrid live            today's real games off ESPN (fetches a few MB first)
    retrogrid fetch | build-slate | sprites | yahoo-auth | find-stream
                              the data tools (each takes --help)
    retrogrid paths           where data lives on this machine
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import date
from importlib import import_module
from pathlib import Path

TOOLS = {"fetch": "fetch_nflverse", "build-slate": "build_slate", "build-live": "build_live",
         "sprites": "build_sprites", "yahoo-auth": "yahoo_auth", "find-stream": "find_stream"}
STALE = 6 * 3600.0            # LIVE re-pulls this season's rosters/stats when older than this
BROWSERS = ("chromium", "chromium-browser", "google-chrome-stable", "google-chrome", "brave", "brave-browser",
            "microsoft-edge", "msedge", "chrome")
APP_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def tool(name: str, argv: list[str]) -> int:
    return int(import_module(f".tools.{TOOLS[name]}", __package__).main(argv) or 0)


def find_browser() -> str | None:
    """A Chromium-family binary, for a chromeless --app window. None: fall back to a tab."""
    for b in BROWSERS:
        if hit := shutil.which(b):
            return hit
    return next((p for p in APP_PATHS if Path(p).is_file()), None)


def open_window(url: str, port: int, profile: Path) -> None:
    for _ in range(150):                                   # wait for the server, up to ~30s
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.2)
    if exe := find_browser():
        subprocess.Popen([exe, f"--user-data-dir={profile}", "--no-first-run", "--disable-session-crashed-bubble",
                          "--autoplay-policy=no-user-gesture-required", "--class=retrogrid", f"--app={url}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(url)


def season_now() -> int:
    today = date.today()
    return today.year if today.month >= 3 else today.year - 1


def prepare_live(args: argparse.Namespace) -> int:
    from . import paths
    season = season_now()
    roster = paths.NFLVERSE / f"roster_weekly_{season}.parquet"
    stale = not roster.exists() or time.time() - roster.stat().st_mtime > STALE
    tool("fetch", ["--season", str(season - 1), "--lite"])             # last year seeds the draft pool early on
    if rc := tool("fetch", ["--season", str(season), "--lite"] + (["--force"] if stale else [])):
        return rc
    if not args.keep:
        if rc := tool("build-live", (["--date", args.date] if args.date else []) + (["--all-teams"] if args.all_teams else [])):
            return rc
    return tool("sprites", ["--live"])


def serve(args: argparse.Namespace) -> int:
    for flag, var in (("league", "LEAGUE"), ("favs", "FAVS"), ("speed", "SPEED"), ("start", "START"), ("chatter", "CHATTER")):
        if (v := getattr(args, flag, None)) is not None:
            os.environ[f"RETROGRID_{var}"] = str(v)
    if args.cmd == "live":
        os.environ["RETROGRID_LIVE"] = "1"
        if rc := prepare_live(args):
            return rc
    from . import paths
    if not (paths.WEB / "dist" / "app.js").is_file():
        print("frontend bundle missing — in a checkout, run `npm install && npm run build`", file=sys.stderr)
        return 1
    import uvicorn
    url = f"http://{args.host}:{args.port}/"
    print(f"RETRO//GRID {'LIVE' if args.cmd == 'live' else 'SIM SUNDAY'} -> {url}   (data: {paths.DATA})")
    if not args.no_window:
        paths.DATA.mkdir(parents=True, exist_ok=True)
        threading.Thread(target=open_window, args=(url, args.port, paths.DATA / "chrome-profile"), daemon=True).start()
    uvicorn.run("retrogrid.server:app", host=args.host, port=args.port, log_level="warning" if not args.verbose else "info")
    return 0


def show_paths() -> int:
    from . import paths
    for k in ("PKG", "DATA", "NFLVERSE", "LIVE_SLATE", "SPRITES"):
        print(f"{k:11s}{getattr(paths, k)}")
    print(f"{'sim slate':11s}{paths.sim_slate()}")
    print(f"{'browser':11s}{find_browser() or '(none found: opens a tab in your default browser)'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in TOOLS:
        return tool(argv[0], argv[1:])
    if argv and argv[0] == "paths":
        return show_paths()
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv.insert(0, "sim")

    ap = argparse.ArgumentParser(prog="retrogrid", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("sim", "live"):
        p = sub.add_parser(name, help="SIM SUNDAY (default)" if name == "sim" else "today's real games")
        p.add_argument("--port", type=int, default=int(os.environ.get("RETROGRID_PORT", "8082")))
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--no-window", action="store_true", help="serve only; open the URL yourself")
        p.add_argument("--league", choices=("stub", "yahoo"), help="turn the fantasy layer on")
        p.add_argument("--favs", help='teams you follow, e.g. "KC BUF"')
        p.add_argument("--chatter", choices=("reddit", "stub", "off"))
        p.add_argument("-v", "--verbose", action="store_true")
        if name == "sim":
            p.add_argument("--speed", type=float, help="sim clock multiplier (default 4)")
            p.add_argument("--start", type=float, help="sim seconds to start at")
        else:
            p.add_argument("--keep", action="store_true", help="reuse today's slate instead of rebuilding it")
            p.add_argument("--date", help="slate day, US Eastern, YYYY-MM-DD")
            p.add_argument("--all-teams", action="store_true", help="draft the stub league from the whole week")
    return serve(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
