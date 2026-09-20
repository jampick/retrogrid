# Yahoo Fantasy integration (Phase 8, league side)

**Status 2026-09-20: adapter built and tested against Yahoo-shaped fixtures;
never yet run against the real API.** The fixtures were written from the
documented response shape, so expect the first real run to surface a parsing
surprise or two — every raw response lands in `data/yahoo/raw/` for that reason.

**Blocked 2026-09-20:** app created, OAuth consent + token work, but every
Fantasy endpoint answers 403 "This application is not authorized to perform
this action". Since 2026-07-22 Yahoo requires a reviewed application at
https://sports.yahoo.com/developer/access/ (give it our Client ID) before any
app may read the Fantasy API. Nothing to fix on our side until that is approved.

## Run it

```
# .env (gitignored)
YAHOO_CLIENT_ID=...
YAHOO_CLIENT_SECRET=...
RETROFFB_LEAGUE=yahoo             # or export it per run
# YAHOO_REDIRECT_URI=https://localhost:8443/callback   (default: oob)

scripts/yahoo_auth.py             # once: sign in, pick the league
scripts/yahoo_auth.py --check --week 2   # print league, rules, lineups, join misses
scripts/live.sh                   # console on your real league
```

`RETROFFB_YAHOO_REPLAY=1` serves everything from `data/yahoo/raw/` (offline dev).
Pieces: `providers/yahoo_api.py` (flattener, token, cached GET),
`providers/yahoo.py` (provider, stat map, player join), `providers/league.py`
(factory + default viewer), `Engine.load_league / league_pump / log_drift`.

Not done: drift is only logged (no UI); yardage bonuses, return yards, IDP and
any unmapped stat are warned about at startup, not scored; players the join
cannot match show as UNKNOWN and sit at Yahoo's official points.

---

Original plan, kept for the reasoning:

Left 2026-09-20, right after LIVE mode (ESPN plays) went in. The play lane is
real now; the league is still `SyntheticLeagueProvider`. This swaps it for the
user's actual Yahoo league. DESIGN §9 and §12 are the contract — read them first.

## What the user has to do first (cannot be done for them)

1. Create an app at https://developer.yahoo.com/apps/create/ — API permission
   **Fantasy Sports: Read**, confidential client.
2. Redirect URI **must be HTTPS** (§12: the likely day-one blocker). Easiest:
   `https://localhost:8443/callback` with a throwaway self-signed cert, or the
   out-of-band flow (`redirect_uri=oob`) and paste the code into a terminal.
   Try `oob` first — no cert, no listener.
3. Hand over Client ID + Secret via `.env` (gitignored — check it is before
   writing anything there). Never commit, never log the secret or tokens.

## Build order (stubs first, as always)

1. `scripts/yahoo_auth.py` — one-time OAuth2 authorization-code flow; stores
   `data/yahoo/token.json` (access + refresh). Access tokens last 1 h; refresh
   on 401 and ahead of expiry. **One token pulls the whole league** (§12).
2. `providers/yahoo.py` — `YahooLeagueProvider` implementing `LeagueProvider`
   in `providers/base.py` exactly: `league()`, `roster()`, `matchup()`,
   `matchups()`, `official_points()`. Nothing downstream should change.
   - Base: `https://fantasysports.yahooapis.com/fantasy/v2/…?format=json`
     (the JSON is XML-shaped: lists of single-key dicts. Write one flattener.)
   - Game key: discover via `/games;game_codes=nfl;seasons=<year>` — never hardcode.
   - League pick: `/users;use_login=1/games;game_keys=<k>/leagues`; if more
     than one, ask the user once and remember it in `data/yahoo/league.json`.
   - Scoring: `/league/{key}/settings` -> `stat_categories` + `stat_modifiers`
     -> our rule keys (`YAHOO_HALF_PPR` in `stub_league.py` is the vocabulary;
     write the stat_id map against it, and LOG any stat_id we cannot map
     rather than dropping it silently — bonuses, return yards, IDP).
   - Rosters: `/team/{key}/roster;week=N` — `selected_position` gives the slot
     (BN/IR are bench). Matchups: `/league/{key}/scoreboard;week=N`.
3. **Player id join** — the real work. Yahoo `player_id` -> nflverse `gsis_id`
   via the `yahoo_id` column in `roster_weekly_<season>.parquet` (already on
   disk; `players.parquet` has it too). Fallback: name + team + position
   through `PlayerDirectory`. DEF: Yahoo team-defence players -> `DEF-<TEAM>`
   by editorial team abbr (mind WAS/WSH, LA/LAR, JAX/JAC). Report unmatched
   players loudly at startup; a silent miss is a player who never scores.
4. Wire-up: `RETROFFB_LEAGUE=yahoo` picks the provider in `console.Engine`.
   Viewer default becomes the user's own team (`is_owned_by_current_login`).
   Today `USER_TEAM = "t01"` and `Session.viewer = "t01"` are hardcoded —
   those need to come from the provider.
5. Slow lane (§9): refresh rosters + matchups every 5 min (lineup changes,
   late swaps); pull `official_points` and surface drift vs the local engine.
   Yahoo must never sit in the per-play path.
6. Cache every raw response under `data/yahoo/raw/` so tests and offline dev
   replay fixtures instead of hitting the API. Rate limits are unpublished;
   stay in the low thousands of calls per day.

## Known snags

- `build_live.py` drafts only from teams playing *today*; a real roster has
  Thursday/Monday players. Engine must tolerate rostered players with no game
  in the slate (they simply sit at their official points — seed those from
  Yahoo so the matchup total is right on Sunday morning).
- Real leagues have IR, multiple FLEX/superflex, maybe IDP. `MatchupBoard`
  pairs rows by slot; check it survives uneven lineups.
- Sprites: `build_sprites.py --live` reads the league's rosters — should work
  unchanged once the provider is swapped, but run it.
- Private league + read scope is fine; write scope is never needed.
