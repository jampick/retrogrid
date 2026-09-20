# RETRO//NFL  (née RETRO//FFB)

A cyberpunk NFL gameday console — with an opt-in fantasy threat layer.

**Base layer.** Watches every live NFL game, ranks every play by how much
football it was (scores, turnovers, 4th downs, explosives — weighted late and
close, doubled for the teams you follow), and renders the ones that matter as
lo-res neon tactical diagrams, flanked by holograms of each side's hot hand.
The right rail is the **crowd** (r/nfl + team-subreddit game threads); `M`
tunes the **radio** to the focused game's flagship station. It follows your
**Omarchy theme live**.

**Fantasy layer (opt-in, `RETROFFB_LEAGUE=stub|yahoo`).** The same console
re-denominated in *your* matchup: fantasy score bar, LINEUP rail, THREATS
ranked by impact on your week, ownership colours on the field. `X` flips it
per session; without a league configured it simply does not exist.

→ **[docs/DESIGN.md](docs/DESIGN.md)** — the full design.

## Run it (no credentials needed)

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' && npm install
.venv/bin/python scripts/fetch_nflverse.py      # public nflverse files -> data/
.venv/bin/python scripts/build_slate.py         # SIM SUNDAY slate (2025 wk 15, 14 games)
.venv/bin/python scripts/build_sprites.py       # headshots -> hologram data sprites
scripts/dev-server.sh                           # http://127.0.0.1:8082
scripts/dev-window.sh / 4                       # chromeless window on Hyprland workspace 4
```

`/themes` is the contact sheet: the console under every stock Omarchy theme.
`/plays` is the grammar contact sheet (DESIGN §8): N seeded-random slate plays
compiled and looping side by side, filterable by family. `R` reroll · `S`
finished diagrams · click to zoom · in zoom `F`/`N` flag a bad play (with a
note) into `data/grammar_flags.json` — the worklist for the next grammar pass.
Env: `RETROFFB_LEAGUE` (unset = NFL monitor · `stub` = synthetic 12-team league ·
`yahoo` = docs/YAHOO.md), `RETROFFB_FAVS="KC BUF"` (default followed teams),
`RETROFFB_CHATTER` (`stub` canned crowd, default in SIM · `reddit`, default in
LIVE · `off`), `RETROFFB_START` (sim seconds), `RETROFFB_SPEED`, `RETROFFB_SEED`,
`RETROFFB_PROSE=1` (rehearse the live path: every play rebuilt from its
description alone — parser + name resolution + air-yards prior).

**LIVE** — today's real games: `scripts/live.sh` (fetches this season's nflverse
rosters/stats, `build_live.py` drafts the synthetic league from teams playing
today, then serves with `RETROFFB_LIVE=1`). Plays come from ESPN's public
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
`scripts/find_stream.py "<station>" --set TEAM` (writes `data/audio_streams.json`,
picked up without a restart). NFL+ (paid) is the reliable source; the console
only links to it.

Keys: `T` theme · `F` follow teams · `A` auto-direct · `M` radio · `H` other
booth · `-`/`=` volume · `N` mute alert cues · `X` fantasy layer (then `V`
who-am-I · `L` threat scope · `Tab` lineup/chatter) ·
`Space` hold · `1–4` sim rate · `←/→` skip 5 min · `Enter` view alert ·
`C` replay-cycle recent plays in the dead time between snaps · `[` `]` step
through them by hand · `/` back to live.

## Status

| Phase | State |
|---|---|
| 0 Skeleton, contracts, SIM SUNDAY clock, **Omarchy ThemeProvider + role resolver** | working |
| 1 nflverse ingest, headshot sprite pipeline, synthetic league | working |
| 2 `desc` parser — ≥99.9% agreement with nflverse on 2024 + held-out 2025 | working; `RETROFFB_PROSE=1` puts it on the hot path — all 322 scoring players match nflverse-column scoring to the point |
| 3 Play grammar — all 2,175 slate plays compile, zero fallbacks | second pass (kick/punt coverage lanes + fates, reachable catch points); iterate via `/plays`; BDB constant fit open |
| 4 Renderer, PHOSPHOR + PRINTOUT light models, ghosts, RETUNE | working |
| 5 Console shell | working |
| 6 Scoring + threat ranking | working |
| 7 Polish | open |
| 8 ESPN live plays | working (first pass) |
| 8 Yahoo league adapter, hosting | needs credentials |

`pytest` — 156 tests.
