# RETRO//GRID — Design Document

*Last updated 2026-09-20 (Week 3). Status: design settled; Phases 0–6 have a working first pass (see README).*

---

## 1. Thesis

**This is not a play viewer. It is a fantasy threat-monitoring console.**

On any given Sunday there are ~13 concurrent NFL games and you care about
perhaps 18 players across all of them — your nine starters and your
opponent's nine. Your opponent's RB breaking a 40-yard touchdown in a game
you would never otherwise watch is the single highest-value event on the
entire slate, for you, at that moment. No existing product tells you that
as it happens, in a form you can read in half a second.

RETRO//GRID does. It watches every live game, ranks every play by its impact
on *your* matchup, and renders the ones that matter as neon tactical
diagrams. Everything on screen is denominated in fantasy points, never in
the actual game score.

The cyberpunk framing is not decoration. It is the correct metaphor for the
product: you are an operator at a surveillance console, watching feeds,
tracking rivals, receiving threat alerts.

### Users

Private league, ~12 people. Multi-viewer from day one — every league member
opens the same hosted console and sees it oriented around *their* matchup.

---

## 2. The constraint that shaped everything

True X's-and-O's rendering requires NGS tracking data: 10Hz XY coordinates
for all 22 players plus the ball. **This is unobtainable.** Club/vendor
credentials are not purchasable, Genius Sports holds exclusive realtime
distribution through 2029 at enterprise pricing, and the public
`nextgenstats.nfl.com/api` blocks every coordinate route. Full research in
the memory note `nfl-tracking-data-access`.

**The retro aesthetic dissolves this problem.** Schematic routes are a
degradation against a broadcast-fidelity target and the *intended
aesthetic* against a lo-res neon target. We are not approximating something
better; low fidelity is the goal. The data we cannot buy is data we would
not use.

Two further leverage points:

- **Asymmetric fidelity.** We do not need 22 believable players. We need
  three: the ball, the primary actor, and the tackler. Everything else is
  dimmed background motion that nobody scrutinises at 8px.
- **Big Data Bowl's real job is constants, not coordinates.** We are not
  copying plays out of it. We fit ~20 numbers from it — time-to-throw by
  depth, route break depths, player speed by position, closing speed on a
  tackle — once, then never touch it again except to validate.

---

## 3. Aesthetic

Cyberpunk retro-future. Near-black, neon, scanlines, holograms. Think
operator console, not video game.

### Palette — roles, not hexes

Constrained, semantic. **The console owns eight colour *roles*; the active
theme owns the hex values.** Nothing in the renderer, the CSS or the sprite
pipeline may name a literal colour — only a role. §3a covers how roles are
resolved from an Omarchy theme.

| Role | Used for | `NEON` built-in |
|---|---|---|
| `bg` | page background | `#05060a` |
| `grid` | field grid, hash marks, rules | `#0e2a33` |
| `you` | YOU — your players, your ghost, positive identity | `#22e0f0` |
| `them` | THEM — opponent players, threats | `#ff2b8a` |
| `alert` | alerts, warnings, live indicators | `#ffb020` |
| `gain` | positive delta, points gained | `#39ff88` |
| `hot` | ball, active highlight, hot text | `#eafcff` |
| `dim` | labels, inactive text, off-ball players | `#4a6a78` |

`NEON` is the original cyberpunk palette. It is the default wherever no
Omarchy theme is available, and is always selectable.

Threat semantics: ⚠ `them` = hurting you. ▲ `you`/`gain` = helping you.
**Colour never carries meaning alone** — every ally/enemy distinction is also
a glyph (⚠ ▲) or a stroke style (solid vs hollow). That rule is what lets the
console survive monochrome themes.

### Post-processing

Applied over the whole console, not per-element (dark-mode values; light
themes invert the light model — see §3a PRINTOUT):

- scanline mask (every other row, ~15% darkening)
- bloom on bright neon elements
- chromatic aberration, 1px, increasing toward screen edges
- subtle vignette

Start with a CSS scanline overlay plus canvas-composite bloom. Upgrade to a
WebGL post pass only if it proves insufficient.

### Typography

Pixel/terminal face. Thin, wide letter-spacing, uppercase for labels.
No serifs, no rounded UI fonts. DOM type follows the Omarchy system font
when hosted on one (§3a).

### Cards are light, not boxes

There are **no bordered cards anywhere**. A "card" is a hologram bust with
type floating beside it. Separation comes from light and spacing, never
from frames. This is the single rule that keeps the console from looking
like a generic dashboard.

---

## 3a. Omarchy theme alignment

The console is a citizen of the Omarchy desktop. Change the system theme and
the console retunes with it, live, the same way the terminal, bar and btop
do. It should look like it shipped with the theme — Retro 82 gives a
teal-and-amber 1982 vector terminal, Tokyo Night gives violet neon, Matte
Black gives a monochrome radar scope, Catppuccin Latte gives a dot-matrix
tactical printout.

### Source of truth

`~/.local/state/omarchy/current/theme/colors.toml` (+ `../theme.name`). A flat
TOML of ~25 named colours plus `mode = "dark" | "light"`:

```
mode · accent · selection · muted
background · dark_background · darker_background · lighter_background
foreground · dark_foreground · light_foreground · bright_foreground
red yellow orange green cyan blue magenta brown · bright_*
```

Not every theme defines every key (`white` has no `orange`/`brown`), and
names lie: in Retro 82 `magenta` and `blue` are the same teal as each other
and nearly the same as `cyan`. **So roles are resolved, never looked up.**

### The two-family rule

The design needs far less than eight distinct hues. It needs **two opposed
hue families and a lightness ramp**:

- **ally family** — `you`, `gain`
- **enemy family** — `them`, `alert`
- **ramp** — `bg` → `grid` → `dim` → `hot`

Any theme with two separable hues can carry the whole console. Themes with
fewer fall back to stroke style.

### Role resolver

Pure function `resolve(colors.toml) -> Palette`, run in OKLCH.

1. **Ramp.** `bg = background`, `hot = bright_foreground`,
   `dim = dark_foreground`, `grid = selection` (nudged toward `bg` until it
   sits at 1.3–1.8:1 against it — a grid is felt, not read).
2. **Eligible colours** = named colours with ≥ 3:1 contrast against `bg` and
   chroma above a floor.
3. **`you` × `them`.** Candidates `you ∈ [cyan, blue, accent, green]`,
   `them ∈ [magenta, red, orange, yellow]` (bright variants included). Walk
   pairs in list order and take the **first ≥ 75° apart**, so conventional
   themes resolve conventionally (cyan vs magenta); failing that, the widest
   pair if it reaches 60°; failing that, monochrome.
4. **`alert`** from `[yellow, orange, accent]`, excluding whatever `them`
   took. If it lands within 30° of `them` it must differ in lightness by
   ≥ 0.15 — alert and threat may be cousins, never twins.
5. **`gain`** = `green` if it sits in the ally half of the wheel and is
   distinguishable from `you`; otherwise `you` lifted in lightness.
6. **Monochrome fallback.** No pair reaches 60° (`white`, `matte-black`,
   `vantablack`): `you` = solid `hot`, `them` = **hollow / dashed** `hot`,
   score bar uses hatch fill for the opponent's side. Radar-scope look.
7. **Contrast floor.** Any text role under 4.5:1 on `bg` is pushed along the
   lightness axis until it passes. Hue is never altered.

Worked example, Retro 82: `you #8cbfb8` (teal), `them #f85525` (red-orange),
`alert #faa968` (the accent), `gain #028391`→lifted, `hot #f6dcac` (cream),
`dim #3f8f8a`, `grid #134e5a`, `bg #05182e`.

The resolver ships with a **contact sheet**: every stock Omarchy theme × the
console mock, one page. Same doctrine as §8 — the eyeball is the metric.

### Hand-tuned overrides

The resolver is the floor, not the ceiling. An optional `retrogrid.toml`
beside a theme's `colors.toml` (or in `~/.config/retrogrid/themes/<slug>.toml`)
pins any role to a hex or to another key. Omarchy keeps unknown files in
theme directories, so theme authors can ship one.

### Light themes — PRINTOUT mode

Additive light does not exist on paper. When `mode = "light"` the entire
light model inverts rather than merely recolouring:

| | dark (`PHOSPHOR`) | light (`PRINTOUT`) |
|---|---|---|
| blend | additive | multiply |
| glow | bloom | ink bleed (soft darker halo, tighter radius) |
| scanlines | darken alternate rows | dot-matrix banding, faint |
| ghosts | hologram, channel split | halftone screen, misregistered 1px |
| route trails | decay to black | fade like thermal paper |
| vignette | dark corners | paper edge yellowing toward `dark_background` |

Two light models, one scene graph. The renderer asks the palette for
`blend`, never the theme for its name.

### Delivery

A fourth provider joins §7:

```
ThemeProvider
    current()  -> Theme {slug, name, mode, palette, font}
    watch()    -> AsyncIterator[Theme]
```

- **`OmarchyThemeProvider`** — used when the server runs on an Omarchy box.
  Reads the state file; watches `theme.name` for changes. An opt-in
  `theme-set.d` hook (installed via `omarchy hook install`, never silently)
  makes the change instant instead of polled. Pushes `theme` frames down the
  existing WebSocket.
- **`BundledThemeProvider`** — the other eleven league members do not run
  Omarchy. The stock themes' `colors.toml` files are vendored (they are
  ~600 bytes each), so every viewer gets a **theme picker** with the same
  set, persisted per browser. On the host, the first option is
  `FOLLOW SYSTEM`.
- The frontend holds one `Palette` object. DOM reads it as CSS custom
  properties (`--ffb-you` …); both canvases read it directly. Nothing is
  baked, so a theme change costs one repaint.

### RETUNE

A theme change is an event, not a repaint. ~250ms: horizontal hold slips,
the picture rolls once, colour channels land a frame apart, new palette
locks in. It is the console being retuned to a new frequency, and it turns
an OS housekeeping action into the most satisfying interaction in the app.

### Type

On an Omarchy host the DOM type follows the system font
(`omarchy font current`, JetBrainsMono Nerd Font by default) so the console
matches the terminal beside it; the `font-set` hook keeps it in step.
Remote viewers get the bundled face. On-canvas label tags always use the
bundled pixel face — they live on the 240×256 grid and must not reflow.

### Window

Runs as a chromeless app window (`chromium --app`, class `retrogrid`) so it
tiles like a native Omarchy app, no tabs or URL bar. Hyprland's default
window opacity lets wallpaper bleed through near-black; a one-line opacity
window rule for class `retrogrid` is documented but left to the user —
some will prefer the bleed.

---

## 4. Screen

Desktop first. Phone layout deferred (see §11).

```
┌─ RETRO//GRID ──────────────────────────────────  WK3 · SUN 16:42 ┐
│ jampick 78.4 ████████████░░░░░░░░ 91.2 DOOMSDAY_DEVICE  ▼12.8              │
├──────────────┬──────────────────────────────────────┬──────────────────────┤
│ FEEDS        │░▒▒▒░                            ░▒▒▒░│ YOU          THEM    │
│ ▸KC@BUF  ⚠Q3 │▒▒▒▒▒   · · · · · · · · · · ·    ▒▒▒▒▒│ MAHOMES 22.1 31.4 ⚠  │
│  NYJ@MIA ▲Q2 │▒░▒░▒                            ▒░▒░▒│ PACHECO  8.2 14.6 ⚠  │
│  DAL@PHI  Q1 │▒▒▒▒▒      ╭─────⬤ KELCE         ▒▒▒▒▒│ KELCE   18.2  6.0 ▲  │
│  SF@SEA   HT │░▒▒▒░      │                     ░▒▒▒░│ WORTHY   4.1 12.2 ⚠  │
│  +9 MORE     │ ▒▒▒  ⬤────╯  ⬤⬤⬤⬤⬤    ⬤         ▒▒▒ │ RICE     6.2  9.0 ⚠  │
│              │▒▒▒▒▒         △ MAHOMES          ▒▒▒▒▒│ BUTKER   3.0  4.0    │
│ THREATS      │▒▒▒▒▒                            ▒▒▒▒▒│ KC DEF   5.6  9.8 ⚠  │
│ ⚠ALLEN  +14  │░▒▒▒░   · · · · · · · · · · ·    ░▒▒▒░│                      │
│ ⚠HALL    +6  │KELCE                            ALLEN│ ◂MATCHUP · LEAGUE▸   │
│ ▲KELCE   +9  │+18.2   ══════════════════════   +31.4│                      │
├──────────────┴──────────────────────────────────────┴──────────────────────┤
│ T.KELCE  TE·KC·#87   5 REC · 62 YD · 1 TD   18.2 PTS  PROJ 11.4  +6.8 ▲    │
│ ▸ 2Q 4:12  pass short right, 9yd, 1st down      TGT 7 · aDOT 8.2  +0.9     │
└────────────────────────────────────────────────────────────────────────────┘
```

### Regions

**Status bar.** Your score vs your opponent's, as a single bar. The delta
is the most important number on the screen.

**Left rail — FEEDS.** All live games, with per-game threat markers showing
whether that game is currently helping or hurting you. Click to focus.

**Left rail — THREAT BOARD.** Recent fantasy-relevant events, ranked by
impact (§8), not chronology. Defaults to your matchup; toggles to
league-wide.

**Centre — tactical field.** §5.

**Flanking ghosts.** Your biggest mover and your opponent's, facing each
other across the field. §6.

**Right rail — LINEUP.** Slot-by-slot comparison, your starter vs theirs,
with a ⚠ on every slot you're losing. This is where "how is this hurting
me" gets answered without a single click.

**Bottom — ACTIVE CARD.** The protagonist of the play on screen, in
fantasy terms: targets, air yards, aDOT, projection, season average, and
the points delta from *this specific play*.

### Behaviour

**Alerting: alert-and-tap.** A big play in an unfocused game flashes the
threat board and slides in a banner with player and delta. It never seizes
the view. You stay in control, and nothing is missed — plays are already
~20s behind live, so the replay is always available.

**AUTO-DIRECT (optional, on by default under SIM SUNDAY).** For unattended
viewing — the demo on a side monitor, and TV cast mode (§11) — the console may
cut to an alerting play by itself. It still banners first, never interrupts
the rails, and `[A]` turns it off, restoring strict alert-and-tap.

**Idle: last play holds.** During dead air the most recent play stays
frozen with its route trails glowing and slowly decaying. The rails stay
fully live throughout.

**Audio: alert cues only.** A short synth sting on threat fire or lead
change. Nothing else — most viewers have a broadcast playing.

---

## 5. Tactical field renderer

### Layering

```
z2   DOM UI          rails, cards, banners — semi-transparent so ghost light bleeds
z1   field canvas    240×256, transparent clear, nearest-neighbour upscale
z0   ghost canvas    full-viewport, holograms bleed behind the rails
```

### Field geometry

Vertical, top-down. Internal buffer **240 × 256**.

- 53.3 yards of field width across 240px = **4.5 px/yard**
- 256px of depth = **~57 yards visible**
- Camera anchors ~15 yards behind the LOS, ~42 ahead
- **Most plays never scroll.** Only long gains pan.

Players render as glowing dots, ~5px, with floating neon label tags.
Routes are light trails that persist and fade. The label tags are what
solve readability — we never need a legible 4px jersey number.

### Motion

All 22 players run their assignment. Off-ball players are **dimmed and
untagged**; the involved players glow bright with labels. Because the
defence is permanently dimmed and unlabelled, crude coverage shells are
acceptable — which is fortunate, since defensive assignment is the data we
have least of.

### Timing constants (fit from Big Data Bowl)

Roughly twenty numbers, fit once:

- QB drop depth and duration by pass length (3/5/7 step)
- median time-to-throw by pass depth
- route break depth by template
- player top speed and acceleration by position
- ball flight time per yard of air distance
- closing speed on a tackle
- scramble duration distribution

---

## 6. The ghost system

Flanking holograms of the two most impactful players in your matchup right
now — yours on the left, your opponent's on the right — projected in the
outer margins and bleeding behind the rails.

### Source

nflverse ships `headshot_url` for every player (ESPN-hosted). These are
unusually good source material: helmet off, shoulders up, team jersey,
transparent background, and **identical framing, lighting and crop across
all ~2000 players**. That consistency means one pipeline with fixed
parameters handles the entire league with zero per-player art direction.

### Pipeline (batch, offline, re-run weekly)

1. fetch headshot
2. push contrast **before** downsampling (reverse the order and features
   mush into one tone)
3. downsample to 96×96 for ghosts, 48×48 for cards, 16×16 for rails
4. posterize on luminance to 4 tones, **keeping a hue split** so skin and
   jersey stay separable — stored as a **tone index + region mask, never as
   colour**. Sprites are palette-mapped at draw time (jersey region → the
   owning side's role colour, skin region → the `dim`→`hot` ramp), so one
   cached sprite serves every theme and both sides of a matchup
5. edge-detect pass, stroke the silhouette in a bright accent —
   **this is the single biggest recognisability win at low resolution**
6. cache as indexed PNG (tone index in one channel, region mask in another)

Fallback: generic position silhouette for players with no headshot
(practice squad, just-signed, early-season rookies). Make it visually
distinct so "no image" is distinguishable from "bad image".

### Render treatment

- 10–15% alpha, additive blend
- channel split 1px — `you` left, `them` right
- scanline mask, every other row dropped
- slow vertical roll, 2–3 frame flicker every few seconds

The channel split and flicker are what make it read as a *projection*
rather than a faded JPEG. Without them it is merely low opacity.

### Brightness is meaning

**Ghost brightness is driven by live fantasy delta.** A player having a
monster day burns brighter and flickers less; someone at zero is barely a
smudge. You read who matters off the screen before parsing any number.
This is what makes the ghosts a feature rather than decoration.

### What likeness survives

| Scale | Survives | Gone |
|---|---|---|
| Ghost, 96px @ 12% | hair silhouette, facial hair, jaw shape, skin/jersey tonal split | eyes, nose, mouth, expression, true colour |
| Card, 48px @ 100% | recognisable face, jersey number | fine detail |
| Rail, 16px | hair + skin/jersey split | everything else |

Recognition at ghost scale is **silhouette-driven, not face-driven**. Two
clean-shaven players of similar build on the same team will be
indistinguishable — which is why the name burns under each ghost and why
that label is not optional.

---

## 7. Data layer

### Provider contracts

Three interfaces. Everything interesting lives downstream of them and needs
no credentials at all — roughly 85% of the work.

```
LeagueProvider      ← Yahoo later
    league()                    -> League {key, name, scoring_rules, teams[]}
    roster(team_key, week)      -> Roster
    matchup(team_key, week)     -> Matchup
    official_points(team, week) -> {player_id: points}

PlayProvider        ← ESPN later
    live_games()                -> Game[]
    stream_plays()              -> AsyncIterator[Play]

PlayerDirectory     ← nflverse
    player(id)  -> Player {name, position, team, number, headshot}
```

### Stub strategy

**Real plays, fake league.** nflverse play-by-play and player data are
public *file downloads* — no account, no key, no OAuth — so play content,
player identities and headshots are real from day one. Only Yahoo genuinely
needs credentials, and a synthetic 12-team league is trivial to generate
deterministically from a seed.

### SIM SUNDAY

The highest-leverage piece of the stub layer. **A replay clock**, not a
static fixture: a canned slate of games whose plays fire on a simulated
timeline, with speed control and a scrubber. The console then behaves
exactly as it will live — plays arriving out of order across games, threat
board reordering, banners firing.

This lets us demo on a Tuesday in March. It is **not** thrown away when
real APIs land: it stays permanently as the test harness and as the rig for
"what happens if two games score simultaneously".

### The live/post-game asymmetry

nflfastR's ~339 columns are *derived*, not delivered. Live, ESPN gives far
less: play text prose, down/distance, start/end yardline, yards gained,
play type, names. No `shotgun`, no `pass_length`, no `air_yards`.

But NFL play-description prose is rigidly formatted and contains nearly all
of it:

```
(8:42) (Shotgun) P.Mahomes pass short right to T.Kelce to KC 45 for 9 yards (J.Smith).
        \_______/           \_________/    \______/              \_______/  \______/
        formation           depth+third     target                 gain      tackler
```

**So we port nflfastR's regex layer to the live path.** And it is
low-risk to do so, because nflverse ships both the raw `desc` string *and*
the parsed columns for millions of plays — a free labelled corpus. Get the
parser to 99%+ agreement offline, long before it faces game-day pressure.

### The one variable we cannot recover live

`air_yards` never appears in the prose; it is charted separately. We know
total yards and short-vs-deep (threshold: 15 air yards), which bounds it
but does not split air from YAC.

**Fix: a learned prior.** Fit the conditional distribution of `air_yards`
given `(pass_length, pass_location, total_yards, down, ydstogo)` from
nflverse history, sample the median. For a schematic, close is
indistinguishable from right.

---

## 8. Play grammar

The compiler from a play row to an animation. This is where the app's soul
lives and where most iterations will go.

### Boundary

```
compile(PlayRow, rng=seed(play_id)) -> Actor[]
    Actor { position, startPos, keyframes: [(t, x, y)] }
```

Pure function. No rendering dependency, fully testable. **Seeding the RNG
from `play_id` means the same play always animates identically** — which
matters for a live app that may re-render after a stat correction.

The renderer merely interpolates and quantises to the pixel grid.

### Two orthogonal axes

Keep formation and assignment independent or the template count explodes
multiplicatively.

#### Formation (7 templates)

| Template | Trigger |
|---|---|
| Shotgun 11 (3WR) | `shotgun`, default |
| Shotgun empty | `shotgun` + 3rd & 7+ |
| Pistol | `shotgun` + run |
| Singleback 11 | no shotgun, pass |
| I-form / offset | no shotgun, run, early down |
| Heavy / goal line | `yardline_100` ≤ 5 or `ydstogo` ≤ 2 |
| Kneel / spike | `play_type` |

Assign players to alignment slots **by position** — free from both Yahoo
and nflverse. TE inline or slot, WR1 outside, RB backfield. Costs nothing,
makes every downstream route more plausible.

#### Routes (~14 templates)

Depth of break from `air_yards`. Which third from `pass_location`. Break
direction from:

> **alignment × pass_location = break direction**
>
> Receiver aligned left, ball to left third → go / fade / corner (breaks out)
> Receiver aligned left, ball to middle → post / dig / seam (breaks in)

- **Short (<15 air):** flat, swing, slant, hitch, curl, out, drag, stick,
  screen (`air_yards` ≤ 0), checkdown
- **Deep (≥15):** go, post, corner, deep cross, seam, wheel

#### Runs (~8 templates + modifier)

Keyed on `run_location` × `run_gap`: dive, power, off-tackle, outside zone,
toss, draw (shotgun + middle), sneak (middle + `ydstogo` ≤ 1), scramble
(improvised path, own logic).

Outcome modifier: big gain → cut into the second level; ≤ 0 → stuffed at
the LOS.

#### Defence

Crude. A front that pushes, DBs that shadow. Pick a coverage shell that
makes the completion plausible. Permanently dimmed and untagged, so it does
not reward effort.

### Special teams

K and DEF are roster slots, so they score and must render.

- **Field goals:** snap → hold → kick → arc. Minimum viable.
- **DEF scoring events:** render the underlying play — sack, interception
  return, fumble return, defensive TD.
- **Punts:** low priority.

### Degradation

Never crash, never render nothing. Missing or unparseable fields fall back
to a generic template by `play_type`. Penalties and `no_play` need explicit
handling.

### Validation

A script that pulls N random plays from nflverse, compiles each, renders a
GIF strip, and emits an HTML contact sheet to scroll through. **Human
eyeball is the metric** — there is no loss function for "looks like
football."

Plus a Big Data Bowl overlay pass for plays present in both, to catch
systematic geometry errors the eye normalises away.

---

## 9. Scoring and threat ranking

### Points

Compute locally: ESPN stats × Yahoo scoring rules (pulled once from
`/league/{key}/settings`). This gives an **optimistic instant delta**.
Yahoo's own number is the **slow reconciliation truth** that corrects drift.

The gap between the two is itself a usable retro-ticker moment.

**Yahoo never appears in the per-play path.** Cadence split:

```
SLOW LANE (minutes)            FAST LANE (seconds)
Yahoo Fantasy API              ESPN undocumented JSON
  scoring rules  (once)          plays as they land
  rosters        (5 min)         raw stat lines
  matchups       (5 min)            ↓
  official pts   (reconcile) ←── local scoring engine
```

### Honesty about "live"

ESPN publishes a play *after it ends*. This is a **~15–30s-behind replay
companion**, not a ball-in-flight tracker. That is ideal for fantasy — you
see the play that just scored you points, right as the points land — but
the design should never pretend otherwise.

### Threat score

Rank the threat board by impact, not chronology:

```
threat = |Δ fantasy points|
       × relevance      (2.0 your/opponent starter, 1.0 league-wide, 0 else)
       × recency_decay
       + lead_change_bonus
```

Lead changes in your matchup are the highest-priority event class.

---

## 10. Build order

Phases 0–7 need **no credentials**.

| Phase | Work |
|---|---|
| **0** | Skeleton: repo, provider interfaces, fixture loader, SIM SUNDAY clock, `ThemeProvider` + role resolver + all-themes contact sheet |
| **1** | Data prep: nflverse ingest, headshot pipeline batch, synthetic league generator |
| **2** | Parser: `desc` → structured fields, validated against nflverse columns |
| **3** | Grammar: `PlayRow → Actor[]`, contact-sheet validation harness |
| **4** | Renderer: field canvas, role palette, post-FX in both light models, ghost layer, RETUNE |
| **5** | Console shell: rails, cards, threat board, alert banners |
| **6** | Scoring engine + threat ranking |
| **7** | Demo polish, alert audio |
| **8** | *(needs credentials)* Yahoo adapter, ESPN adapter, hosting, multi-viewer |

Phases 2–4 are where the weeks go. Phase 8 is a thin adapter layer.

### Stack

Python (FastAPI) backend, vanilla TypeScript canvas frontend, WebSocket
push. The NFL/fantasy ecosystem is overwhelmingly Python (`nfl_data_py`,
`yfpy`, nflverse tooling), and a self-repainting pixel canvas gains nothing
from React.

---

## 11. Open and deferred

- **Phone layout.** Deferred. Cannot be a three-column console — likely a
  scrollable threat feed with diagrams as full-screen takeovers.
- **Monitor wall.** Secondary view showing every live game as a tiny
  animating field, threat-coloured. Designed but not scheduled.
- **Active card stat set.** Currently targets / air yards / YAC / aDOT /
  projection / season average / play delta. Tentative — review against how
  the league actually thinks.
- **Waiver watch.** Flag unrostered free agents having big days. Needs
  Yahoo free-agent data, so blocked until Phase 8.
- **TV cast mode.** Same renderer with rails dropped and type scaled up.
  Cheap, and the version where the aesthetic lands hardest.
- **Punt rendering.** Low priority.
- **`retrogrid-neon` Omarchy theme.** The inverse trick: package the `NEON`
  palette as an installable Omarchy theme so the whole desktop can match the
  console on Sundays.

## 12. Constraints and gotchas

- **Yahoo auth: one token, not twelve.** You OAuth once; your token pulls
  the entire league — all teams, rosters, matchups. Viewers pick "who am I"
  from a dropdown. No per-user flow, no twelve sets of refresh tokens dying
  at 1pm on a Sunday.
- **Yahoo game key changes yearly and is unpublished.** Discover via
  `/games;game_codes=nfl;seasons=2026`. Never hardcode.
- **Yahoo redirect URI must be HTTPS.** The most likely day-one blocker in
  Phase 8.
- **Do not probe the blocked NGS routes.** That is a ToS problem, not an
  engineering one.
- **Do not pursue a Genius Sports quote.** Enterprise-only, five to six
  figures minimum, and they will not quote a personal project.
- **NGS participation data died mid-2023.** The FTN replacement publishes
  only after the postseason, so in-season formation detail is thin —
  another reason formation is inferred, not looked up.
- **Theme colour names lie.** Retro 82's `magenta` is teal; `white` has no
  hues at all. Never index a theme by colour name — go through the resolver.
- **No literal colours downstream of the palette.** One stray hex in CSS or
  canvas code and a theme swap leaves a neon-cyan orphan on a sepia console.
  Lint for it.
- **Ghost alpha above ~20% breaks the console.** The holograms must stay
  faint or they compete with everything.
- **Headshot rights.** Derived pixel art from ESPN-hosted NFL headshots on
  a private console for twelve people is fine. Public distribution is a
  different conversation.
- **Live ESPN capture is perishable.** nflverse gives the clean, settled,
  post-game version, which hides exactly the mess the live layer must
  survive: arrival order, revised plays, stat corrections, real latency
  distribution. Capturing a real Sunday feed costs nothing (open HTTP, no
  credentials) and cannot be done retroactively.
