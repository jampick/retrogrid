"""`retrogrid` — the one command an install needs.

    retrogrid                 today's real games when ESPN lists any, else SIM SUNDAY; re-checked every 5 min
    retrogrid sim             SIM SUNDAY: the shipped slate, no downloads, no keys
    retrogrid live            today's real games off ESPN (fetches a few MB first)
    retrogrid reel            a looping highlight show of the weeks already played
    retrogrid fetch | build-slate | build-reel | sprites | yahoo-auth | find-stream
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
from importlib import import_module
from pathlib import Path

TOOLS = {"fetch": "fetch_nflverse", "build-slate": "build_slate", "build-live": "build_live", "build-reel": "build_reel",
         "sprites": "build_sprites", "yahoo-auth": "yahoo_auth", "find-stream": "find_stream"}
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


def open_window(url: str, port: int, profile: Path, server) -> None:
    """Open the app window once the server answers; when the window closes, stop the server.

    The Chromium process is ours (dedicated --user-data-dir), so it exits when its last
    window does. That lets the desktop entry run without a terminal: the app window is
    the only window, and closing it ends everything. The tab fallback has no process to
    watch, so there the server stays up until Ctrl-C."""
    for _ in range(150):                                   # wait for the server, up to ~30s
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.2)
    if exe := find_browser():
        proc = subprocess.Popen([exe, f"--user-data-dir={profile}", "--no-first-run", "--disable-session-crashed-bubble",
                                 "--autoplay-policy=no-user-gesture-required", "--class=retrogrid", f"--app={url}"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc.wait()
        server.should_exit = True
    else:
        webbrowser.open(url)


def prepare_reel(args: argparse.Namespace) -> int:
    """Best effort: every step can fail (offline, offseason) and the show still
    runs on whatever is cached, or on the shipped Sunday if nothing is."""
    from .providers.reel import load_weeks
    from .tools.build_reel import refresh
    from .watch import season_now
    for season in (season_now(), season_now() - 1):                        # last year only while this one has no games
        if not args.keep:
            try:
                refresh(season)
            except Exception as e:                                         # noqa: BLE001
                print(f"reel: could not refresh {season} ({e}); using what is on disk", file=sys.stderr)
        if load_weeks(season=season):
            break
    try:
        tool("sprites", ["--reel"])
    except Exception as e:                                                 # noqa: BLE001
        print(f"reel: sprites skipped ({e})", file=sys.stderr)
    return 0


def serve(args: argparse.Namespace) -> int:
    for flag, var in (("league", "LEAGUE"), ("favs", "FAVS"), ("speed", "SPEED"), ("start", "START"), ("chatter", "CHATTER")):
        if (v := getattr(args, flag, None)) is not None:
            os.environ[f"RETROGRID_{var}"] = str(v)
    if args.cmd in ("live", "auto"):
        from .watch import games_today, prepare_live
        if args.cmd == "live" or games_today():                # auto asks ESPN once; offline or no games = the sim for now
            rc = prepare_live(args.date, args.all_teams, args.keep)
            if rc and args.cmd == "live":
                return rc
            if not rc:
                os.environ["RETROGRID_LIVE"] = "1"
    if args.cmd != "reel":
        os.environ["RETROGRID_MODE"] = "hold" if getattr(args, "date", None) else args.cmd
    if args.cmd == "reel":
        os.environ["RETROGRID_REEL"] = "1"
        prepare_reel(args)
    from . import paths
    if not (paths.WEB / "dist" / "app.js").is_file():
        print("frontend bundle missing — in a checkout, run `npm install && npm run build`", file=sys.stderr)
        return 1
    import uvicorn
    url = f"http://{args.host}:{args.port}/"
    mode = "REEL" if args.cmd == "reel" else "LIVE" if os.environ.get("RETROGRID_LIVE") == "1" else "SIM SUNDAY"
    if args.cmd == "auto":
        mode += " (watching ESPN for today's games)"
    print(f"RETRO//GRID {mode} -> {url}   (data: {paths.DATA})")
    server = uvicorn.Server(uvicorn.Config("retrogrid.server:app", host=args.host, port=args.port,
                                           log_level="warning" if not args.verbose else "info"))
    if not args.no_window:
        paths.DATA.mkdir(parents=True, exist_ok=True)
        threading.Thread(target=open_window, args=(url, args.port, paths.DATA / "chrome-profile", server), daemon=True).start()
    server.run()
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
        argv.insert(0, "auto")

    ap = argparse.ArgumentParser(prog="retrogrid", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("auto", "sim", "live", "reel"):
        p = sub.add_parser(name, help={"auto": "today's real games if there are any, else SIM SUNDAY (default)", "sim": "SIM SUNDAY",
                                       "live": "today's real games", "reel": "highlights of finished weeks, on a loop"}[name])
        p.add_argument("--port", type=int, default=int(os.environ.get("RETROGRID_PORT", "8082")))
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--no-window", action="store_true", help="serve only; open the URL yourself")
        p.add_argument("--league", choices=("stub", "yahoo"), help="turn the fantasy layer on")
        p.add_argument("--favs", help='teams you follow, e.g. "KC BUF"')
        p.add_argument("--chatter", choices=("reddit", "stub", "off"))
        p.add_argument("-v", "--verbose", action="store_true")
        if name == "reel":
            p.add_argument("--keep", action="store_true", help="skip the nflverse check; play what is cached")
        elif name == "sim":
            p.add_argument("--speed", type=float, help="sim clock multiplier (default 4)")
            p.add_argument("--start", type=float, help="sim seconds to start at")
        else:
            p.add_argument("--keep", action="store_true", help="reuse today's slate if it is cached instead of rebuilding it")
            p.add_argument("--all-teams", action="store_true", help="draft the stub league from the whole week (automatic on thin days)")
            p.add_argument("--date", help="slate day, US Eastern, YYYY-MM-DD (live only: replays that day, no re-check)" if name == "live" else argparse.SUPPRESS,
                           default=None)
    return serve(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
