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
Env: `RETROFFB_START` (sim seconds), `RETROFFB_SPEED`, `RETROFFB_SEED`.

Keys: `T` theme · `V` who-am-I · `L` threat scope · `A` auto-direct ·
`Space` hold · `1–4` sim rate · `←/→` skip 5 min · `Enter` view alert · `M` mute.

## Status

| Phase | State |
|---|---|
| 0 Skeleton, contracts, SIM SUNDAY clock, **Omarchy ThemeProvider + role resolver** | working |
| 1 nflverse ingest, headshot sprite pipeline, synthetic league | working |
| 2 `desc` parser — ≥99.9% agreement with nflverse on 2024 + held-out 2025 | working (not yet on the hot path; SIM uses nflverse columns) |
| 3 Play grammar — all 2,175 slate plays compile, zero fallbacks | first pass; needs eyeball iteration + BDB constant fit |
| 4 Renderer, PHOSPHOR + PRINTOUT light models, ghosts, RETUNE | working |
| 5 Console shell | working |
| 6 Scoring + threat ranking | working |
| 7 Polish | open |
| 8 Yahoo / ESPN adapters, hosting | needs credentials |

`pytest` — 153 tests.
