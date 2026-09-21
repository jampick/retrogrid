<h1 align="center">RETRO//GRID</h1>

<p align="center">
  <b>A cyberpunk NFL gameday console for your second monitor.</b><br>
  Every live game, the plays that matter drawn as lo-res neon tactical diagrams, the crowd in the right rail, the radio one key away.
</p>

<p align="center">
  <a href="https://github.com/jampick/retrogrid/actions"><img alt="CI" src="https://github.com/jampick/retrogrid/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-22e0f0">
  <img alt="Linux · macOS · Windows" src="https://img.shields.io/badge/linux%20%C2%B7%20macos%20%C2%B7%20windows-f85525">
  <img alt="MIT" src="https://img.shields.io/badge/license-MIT-faa968">
</p>

<p align="center">
  <img alt="RETRO//GRID running SIM SUNDAY: plays from around the league drawn live as neon diagrams" src="https://raw.githubusercontent.com/jampick/retrogrid/main/docs/media/hero.gif" width="100%">
</p>

<p align="center"><sub>SIM SUNDAY at 15× — no editing, that is just what it does. <a href="https://github.com/jampick/retrogrid/blob/main/docs/media/gameday.mp4">Full minute, 1600×900 (mp4)</a></sub></p>

## Why

Some games I want on the TV. Most Sundays, though, I'm at my desk in the middle
of a project, or in a game with my buddies, and football is the thing happening
*beside* what I'm doing. A broadcast wants all of your attention; a box score
gives you none of the game. I wanted the thing in between: a window on the side
monitor that watches every game for me, stays quiet while nothing is
happening, and when something does happen, shows me — the play itself, not a
number changing.

So that's what this is. It is built to be glanced at.

## What you're looking at

<img alt="The NFL monitor: feeds, action, field, holograms, chatter" src="https://raw.githubusercontent.com/jampick/retrogrid/main/docs/media/monitor.png" width="100%">

- **FEEDS** (top left) — every game on the slate: score, clock, who has it, a
  bolt when it's hot, `RZ` when someone is inside the 20.
- **ACTION** — every play ranked by how much football it was: scores,
  turnovers, 4th downs, explosives, weighted late and close, doubled for the
  teams you follow (`F`).
- **The field** — the play that matters right now, rebuilt from its
  play-by-play description into a formation, routes and a ball path, and drawn
  on a phosphor grid. It is always labelled `LIVE`, `REPLAY` or `LAST PLAY`, so
  a glance never lies to you about what you are seeing.
- **Holograms** — each side's hot hand, from real headshots run through a
  scanline sprite pipeline.
- **CHATTER** (right) — the crowd: r/nfl and the team subreddits' game threads,
  bucketed per game.
- **Bottom strip** — who just did what, his line so far, and the play as the
  booth called it.

`A` lets the console direct itself, cutting to whichever game just earned it.
In the dead time between snaps it replays the latest play rather than sitting
still.

### RED ZONE

`R` rides whichever drive is inside the 20, the way the channel does.

<img alt="RED ZONE mode under the built-in NEON theme" src="https://raw.githubusercontent.com/jampick/retrogrid/main/docs/media/neon.png" width="100%">

### It wears your desktop

On [Omarchy](https://omarchy.org) the console follows the system theme **live**
— change your theme and the console collapses like a CRT and retunes. A role
resolver maps any palette onto *you / them / alert / gain* and keeps them
tellable apart, including on light and monochrome themes. Everywhere else, `T`
picks from the built-in NEON and the bundled stock themes.

<img alt="Cycling themes with T: the console retunes between palettes" src="https://raw.githubusercontent.com/jampick/retrogrid/main/docs/media/retune.gif" width="100%">

<details>
<summary><code>/themes</code> — the contact sheet: the console under every stock Omarchy theme</summary>
<br>
<img alt="Contact sheet of the console under every stock Omarchy theme" src="https://raw.githubusercontent.com/jampick/retrogrid/main/docs/media/themes.png" width="100%">
</details>

### Fantasy layer (opt-in)

Start it with `--league stub` (a synthetic 12-team league) or `--league yahoo`
and `X` re-denominates the same console in *your* matchup: a fantasy score bar
instead of the game score, a LINEUP rail, THREATS ranked by what they do to
your week, and ownership colours on the field — your colour is yours, the other one is theirs.
Without a league configured the layer simply does not exist.

<img alt="Fantasy layer: matchup score bar, lineup rail, threats; an interception that cost the opponent" src="https://raw.githubusercontent.com/jampick/retrogrid/main/docs/media/fantasy.png" width="100%">

<sub>The Yahoo adapter is written and fixture-tested but waiting on Yahoo API access — see <a href="https://github.com/jampick/retrogrid/issues/6">#6</a> and <a href="docs/YAHOO.md">docs/YAHOO.md</a>.</sub>

### Every play is drawn, not looked up

There is no tracking data behind this (real player XY is not public). Each
diagram is compiled from the play's text by a parser and a play grammar —
personnel, formation, route families, kick and punt coverage lanes. All 2,175
plays on the demo slate compile with zero fallbacks. `/plays` is the grammar
contact sheet used to find the ones that still look wrong:

<img alt="/plays: the grammar contact sheet, seeded-random plays looping side by side" src="https://raw.githubusercontent.com/jampick/retrogrid/main/docs/media/plays.png" width="100%">

→ **[docs/DESIGN.md](docs/DESIGN.md)** has the full design.

## Install

```bash
uv tool install retrogrid        # or: pipx install retrogrid   (Python 3.12+)
retrogrid                        # SIM SUNDAY — 2025 wk 15, 14 games. No downloads, no keys.
retrogrid live                   # today's real games off ESPN (pulls a few MB of rosters first)
```

> The first PyPI release is tracked in [#2](https://github.com/jampick/retrogrid/issues/2).
> Until it lands, run from a checkout — see [Develop](#develop).

Linux, macOS, Windows. It serves on `http://127.0.0.1:8082` and opens a
chromeless window if a Chromium-family browser is installed, a normal tab if
not (`--no-window` to open it yourself). `retrogrid paths` shows where data
lives; `RETROGRID_DATA` moves it. On Omarchy, for an instant retune instead of
the 1 s poll, install the optional hook:
`omarchy hook install theme-set scripts/omarchy-theme-hook.sh`.

Flags: `--league stub|yahoo` · `--favs "KC BUF"` · `--speed 15` · `--port N` ·
`--chatter reddit|stub|off`. Data tools: `retrogrid fetch | build-slate |
sprites | yahoo-auth | find-stream` (each takes `--help`).

## Keys

| | |
|---|---|
| `T` | theme |
| `F` | follow teams |
| `A` | auto-direct |
| `R` | RED ZONE |
| `M` · `H` · `-` `=` | radio · the other booth · volume |
| `N` | mute alert cues |
| `Enter` | view the alert in the banner |
| `C` · `[` `]` · `/` | auto-replay in dead time · step through recent plays · back to live |
| `X` | fantasy layer — then `V` who-am-I · `L` threat scope · `Tab` lineup/chatter |
| `Space` · `1`–`4` · `←` `→` | SIM only: hold · rate 1×/4×/15×/60× · skip 5 min |

## Develop

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' && npm install
scripts/dev-server.sh                           # esbuild watch + uvicorn --reload, :8082
scripts/dev-window.sh / 4                       # chromeless window on Hyprland workspace 4
.venv/bin/retrogrid fetch && .venv/bin/retrogrid build-slate && .venv/bin/retrogrid sprites --all
.venv/bin/python scripts/make_bundle.py         # freeze that slate into the package (what installs ship)
scripts/capture_media.py URL OUTDIR STEPS         # headless-Chromium stills + takes for docs/media (see its docstring)
```

A checkout keeps its data in `./data`; the shipped bundle lives in
`backend/retrogrid/bundled/`, the web frontend in `backend/retrogrid/web/`
(TypeScript sources in `frontend/src/`).

`/themes` is the contact sheet: the console under every stock Omarchy theme.
`/plays` is the grammar contact sheet (DESIGN §8): N seeded-random slate plays
compiled and looping side by side, filterable by family. `R` reroll · `S`
finished diagrams · click to zoom · in zoom `F`/`N` flag a bad play (with a
note) into `data/grammar_flags.json` — the worklist for the next grammar pass.
Env: `RETROGRID_LEAGUE` (unset = NFL monitor · `stub` = synthetic 12-team league ·
`yahoo` = docs/YAHOO.md), `RETROGRID_FAVS="KC BUF"` (default followed teams),
`RETROGRID_CHATTER` (`stub` canned crowd, default in SIM · `reddit`, default in
LIVE · `off`), `RETROGRID_START` (sim seconds), `RETROGRID_SPEED`, `RETROGRID_SEED`,
`RETROGRID_PROSE=1` (rehearse the live path: every play rebuilt from its
description alone — parser + name resolution + air-yards prior).

**LIVE** — today's real games: `retrogrid live` (fetches this season's nflverse
rosters/stats, `tools/build_live.py` drafts the synthetic league from teams playing
today, then serves with `RETROGRID_LIVE=1`). Plays come from ESPN's public
scoreboard + summary feeds (`providers/espn.py`, no credentials), polled every
8 s; a play lands once its text holds still for a poll, touchdown+try entries
are split in two, and booth amendments trigger a rebuild. `--keep` reuses
today's league instead of redrafting. Sim controls are inert in LIVE.

**CHATTER** — Reddit's JSON API is walled (403 without an approved OAuth app) but
its Atom feeds are not, at one request a minute per IP. So `providers/chatter.py`
makes one combined `/r/nfl+<team subs>/comments/.rss` poll every ~65 s and buckets
comments per game by thread title + subreddit. At peak that is a sample of the
thread, not all of it. In SIM a stub crowd reacts to the plays.

**RADIO** — best effort. `providers/audio_seed.json` lists each team's flagship
station and a stream URL where one was verified to open (26/32); whether a
station's web stream carries the game, swaps in talk, or geo-fences is up to the
station, game by game. The radio pins to the game in focus when switched on and
stays there while the view roams. Fix or add a station with
`retrogrid find-stream "<station>" --set TEAM` (writes `audio_streams.json` in the data dir,
picked up without a restart). NFL+ (paid) is the reliable source; the console
only links to it.

## Status

| Phase | State |
|---|---|
| 0 Skeleton, contracts, SIM SUNDAY clock, **Omarchy ThemeProvider + role resolver** | working |
| 1 nflverse ingest, headshot sprite pipeline, synthetic league | working |
| 2 `desc` parser — ≥99.9% agreement with nflverse on 2024 + held-out 2025 | working; `RETROGRID_PROSE=1` puts it on the hot path — all 322 scoring players match nflverse-column scoring to the point |
| 3 Play grammar — all 2,175 slate plays compile, zero fallbacks | second pass (kick/punt coverage lanes + fates, reachable catch points); iterate via `/plays`; BDB constant fit open |
| 4 Renderer, PHOSPHOR + PRINTOUT light models, ghosts, RETUNE | working |
| 5 Console shell | working |
| 6 Scoring + threat ranking | working |
| 7 Polish | open |
| 8 ESPN live plays | working (first pass) |
| 8 Yahoo league adapter, hosting | needs credentials |

`pytest` — 198 tests.

Work is tracked in the open as [issues](https://github.com/jampick/retrogrid/issues) — bug reports, bad-looking plays and station fixes are all welcome.

## Unofficial

Not associated with the NFL, its teams, ESPN, Yahoo or Reddit. It reads public
feeds at run time and redistributes none of them; the shipped demo slate is
derived from [nflverse](https://github.com/nflverse) data. MIT licensed.
