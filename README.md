# RETRO//FFB

A cyberpunk fantasy football threat console.

Watches every live NFL game, ranks every play by its impact on *your*
fantasy matchup, and renders the ones that matter as lo-res neon tactical
diagrams — with holographic projections of the players who are currently
winning or losing you the week. It follows your **Omarchy theme live**.

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
Env: `RETROFFB_START` (sim seconds), `RETROFFB_SPEED`, `RETROFFB_SEED`.

Keys: `T` theme · `V` who-am-I · `L` threat scope · `A` auto-direct ·
`Space` hold · `1–4` sim rate · `←/→` skip 5 min · `Enter` view alert · `M` mute ·
`C` replay-cycle recent plays in the dead time between snaps · `[` `]` step
through them by hand · `/` back to live.

## Status

| Phase | State |
|---|---|
| 0 Skeleton, contracts, SIM SUNDAY clock, **Omarchy ThemeProvider + role resolver** | working |
| 1 nflverse ingest, headshot sprite pipeline, synthetic league | working |
| 2 `desc` parser — ≥99.9% agreement with nflverse on 2024 + held-out 2025 | working (not yet on the hot path; SIM uses nflverse columns) |
| 3 Play grammar — all 2,175 slate plays compile, zero fallbacks | second pass (kick/punt coverage lanes + fates, reachable catch points); iterate via `/plays`; BDB constant fit open |
| 4 Renderer, PHOSPHOR + PRINTOUT light models, ghosts, RETUNE | working |
| 5 Console shell | working |
| 6 Scoring + threat ranking | working |
| 7 Polish | open |
| 8 Yahoo / ESPN adapters, hosting | needs credentials |

`pytest` — 155 tests.
