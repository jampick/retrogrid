"""LeagueProvider over the user's real Yahoo league (DESIGN §9; docs/YAHOO.md).

Slow lane only: a refresh is two API calls (every roster with its official
points, and the scoreboard), taken at startup and every few minutes after.
Reads between refreshes are served from memory — Yahoo never sits in the
per-play path.

Player identity is the hard part. Yahoo `player_id` -> nflverse `gsis_id`
through the `yahoo_id` column of the weekly roster files, then by name + team +
position. Anything still unmatched is reported loudly: a silent miss is a
player who never scores.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from ..models import FantasyTeam, League, Matchup, Roster, RosterSlot
from .directory import NflversePlayerDirectory, def_id
from .slate import DEFAULT_NFLVERSE_DIR
from .yahoo_api import YAHOO_DIR, YahooClient, YahooError, many

log = logging.getLogger("retroffb.yahoo")
LEAGUE_FILE = YAHOO_DIR / "league.json"
TEAM_FIX = {"WSH": "WAS", "LAR": "LA", "JAC": "JAX"}          # Yahoo -> nflverse
BENCH_SLOTS = frozenset({"BN", "IR", "IR+", "IL", "IL+", "NA"})
SLOT_NAMES = {"W/R/T": "FLEX", "W/R": "FLEX", "W/T": "FLEX", "R/T": "FLEX", "Q/W/R/T": "SFLX"}

# Yahoo NFL stat_id -> our rule key (vocabulary: scoring.engine.DEFAULT_RULES).
STAT_RULES: dict[int, str] = {
    4: "pass_yd", 5: "pass_td", 6: "pass_int",
    9: "rush_yd", 10: "rush_td",
    11: "rec", 12: "rec_yd", 13: "rec_td",
    15: "ret_td", 16: "two_pt", 18: "fum_lost", 57: "off_fum_ret_td",
    19: "fg_0_39", 20: "fg_0_39", 21: "fg_0_39", 22: "fg_40_49", 23: "fg_50", 29: "xp",
    32: "def_sack", 33: "def_int", 34: "def_fum_rec", 35: "def_td", 36: "def_safety", 37: "def_block",
    49: "def_td",                 # kick/punt return TD: the engine books it as the D/ST's def_td
    50: "def_pa_0", 51: "def_pa_1_6", 52: "def_pa_7_13", 53: "def_pa_14_20",
    54: "def_pa_21_27", 55: "def_pa_28_34", 56: "def_pa_35",
}


def team_abbr(abbr: str | None) -> str:
    up = (abbr or "").upper()
    return TEAM_FIX.get(up, up)


def scoring_rules(settings: dict[str, Any]) -> tuple[dict[str, float], list[str]]:
    """Yahoo league settings -> (our rules, human-readable list of what we
    could not express). Never drops a scoring stat silently."""
    names = {int(s["stat_id"]): s.get("display_name") or s.get("name") or "?"
             for s in many((settings.get("stat_categories") or {}).get("stats"), "stat")}
    rules: dict[str, float] = {}
    source: dict[str, int] = {}
    unmapped: list[str] = []
    for mod in many((settings.get("stat_modifiers") or {}).get("stats"), "stat"):
        sid, value = int(mod["stat_id"]), float(mod.get("value") or 0)
        label = f"stat {sid} ({names.get(sid, '?')}) x{value:g}"
        if mod.get("bonuses"):
            unmapped.append(f"{label}: yardage bonuses ignored")
        key = STAT_RULES.get(sid)
        if key is None:
            if value:
                unmapped.append(f"{label}: no engine equivalent")
            continue
        if key in rules and rules[key] != value:
            unmapped.append(f"{label}: folded into {key}, which stat {source[key]} already set to {rules[key]:g}")
            continue
        rules[key], source[key] = value, sid
    return rules, unmapped


@dataclass
class JoinReport:
    by_id: int = 0
    by_name: int = 0
    unmatched: list[str] = field(default_factory=list)


class PlayerJoin:
    """Yahoo player -> our player id."""

    def __init__(self, directory: NflversePlayerDirectory, season: int,
                 data_dir: Path | str = DEFAULT_NFLVERSE_DIR) -> None:
        self.directory = directory
        self.yahoo_ids: dict[str, str] = {}
        for year in (season - 1, season):                     # this season's rows win
            path = Path(data_dir) / f"roster_weekly_{year}.parquet"
            if path.exists():
                df = pd.read_parquet(path, columns=["gsis_id", "yahoo_id", "week"]).dropna(subset=["gsis_id", "yahoo_id"])
                for r in df.sort_values("week").itertuples(index=False):
                    self.yahoo_ids[str(r.yahoo_id).split(".")[0]] = r.gsis_id
        self.report = JoinReport()
        self._memo: dict[str, str] = {}

    def resolve(self, p: dict[str, Any]) -> str:
        yid = str(p.get("player_id"))
        if yid in self._memo:
            return self._memo[yid]
        name = (p.get("name") or {}).get("full") or "?"
        team, pos = team_abbr(p.get("editorial_team_abbr")), str(p.get("display_position") or "").split(",")[0]
        if pos == "DEF":
            out = def_id(team)
            if self.directory.player(out) is None:
                out = None
        else:
            out = self.yahoo_ids.get(yid)
            if out is not None and self.directory.player(out) is not None:
                self.report.by_id += 1
            else:
                hit = self.directory.by_name(name, team or None, pos or None)
                out = hit.id if hit else None
                self.report.by_name += out is not None
        if out is None:
            out = f"yahoo:{yid}"
            self.report.unmatched.append(f"{name} ({pos} {team}, yahoo {yid})")
        self._memo[yid] = out
        return out


def pick_league(client: YahooClient, season: int) -> str:
    """The league key: $RETROFFB_YAHOO_LEAGUE, else data/yahoo/league.json,
    else the login's only NFL league for the season."""
    if os.environ.get("RETROFFB_YAHOO_LEAGUE"):
        return os.environ["RETROFFB_YAHOO_LEAGUE"]
    if LEAGUE_FILE.is_file():
        return json.loads(LEAGUE_FILE.read_text())["league_key"]
    found = list_leagues(client, season)
    if len(found) != 1:
        raise YahooError(f"{len(found)} NFL leagues for {season} — run scripts/yahoo_auth.py --league to choose one")
    return found[0][0]


def list_leagues(client: YahooClient, season: int) -> list[tuple[str, str]]:
    """[(league_key, name)] for the logged-in user. The game key is discovered, never hardcoded."""
    games = many(client.get(f"games;game_codes=nfl;seasons={season}").get("games"), "game")
    if not games:
        raise YahooError(f"Yahoo has no NFL game for season {season}")
    key = games[0]["game_key"]
    out: list[tuple[str, str]] = []
    for user in many(client.get(f"users;use_login=1/games;game_keys={key}/leagues").get("users"), "user"):
        for game in many(user.get("games"), "game"):
            out += [(lg["league_key"], lg.get("name", "?")) for lg in many(game.get("leagues"), "league")]
    return out


class YahooLeagueProvider:
    def __init__(self, client: YahooClient, directory: NflversePlayerDirectory, week: int, season: int,
                 league_key: str | None = None, data_dir: Path | str = DEFAULT_NFLVERSE_DIR) -> None:
        self.client = client
        self.week = week
        self.league_key = league_key or pick_league(client, season)
        self.join = PlayerJoin(directory, season, data_dir)
        self.viewer: str | None = None                        # the logged-in user's team
        self._official: dict[str, dict[str, float]] = {}
        self._team_official: dict[str, float] = {}
        self._load_league()
        self.refresh()
        rep = self.join.report
        log.info("yahoo league %s: %d teams, players joined %d by id + %d by name, %d unmatched",
                 self._league.name, len(self._league.teams), rep.by_id, rep.by_name, len(rep.unmatched))
        for miss in rep.unmatched:
            log.warning("UNMATCHED yahoo player, will never score: %s", miss)

    # ---------------------------------------------------------------- loading

    def _load_league(self) -> None:
        lg = self.client.get(f"league/{self.league_key}/settings").get("league") or {}
        rules, unmapped = scoring_rules(lg.get("settings") or {})
        for note in unmapped:
            log.warning("yahoo scoring not modelled — %s", note)
        self._meta, self._rules = lg, rules
        self.slots: tuple[str, ...] = tuple(                  # the league's starting lineup shape
            SLOT_NAMES.get(rp["position"], rp["position"])
            for rp in many((lg.get("settings") or {}).get("roster_positions"), "roster_position")
            if rp["position"] not in BENCH_SLOTS
            for _ in range(int(rp.get("count", 1))))

    def refresh(self) -> None:
        """Re-pull rosters, official points and matchups (two calls). Swaps
        state in only once everything parsed, so a bad poll changes nothing."""
        base = f"league/{self.league_key}/teams/roster;week={self.week}"
        try:
            teams = self.client.get(f"{base}/players/stats;type=week;week={self.week}")
        except YahooError as e:                               # rosters matter more than their points
            log.warning("yahoo roster+stats call failed (%s) — rosters only, no official points", e)
            teams = self.client.get(base)
        board = self.client.get(f"league/{self.league_key}/scoreboard;week={self.week}")

        fantasy_teams, rosters, official, viewer = [], {}, {}, None
        for t in many((teams.get("league") or {}).get("teams"), "team"):
            key = t["team_key"]
            managers = many(t.get("managers"), "manager")
            owner = next((m.get("nickname") for m in managers if m.get("nickname")), "?")
            fantasy_teams.append(FantasyTeam(key=key, name=str(t.get("name", key)), owner=str(owner)))
            if str(t.get("is_owned_by_current_login", "0")) == "1":
                viewer = key
            slots, pts = [], {}
            for p in many((t.get("roster") or {}).get("players"), "player"):
                pid = self.join.resolve(p)
                pos = str((p.get("selected_position") or {}).get("position") or "BN")
                slots.append(RosterSlot(slot="BN" if pos in BENCH_SLOTS else SLOT_NAMES.get(pos, pos), player_id=pid))
                total = (p.get("player_points") or {}).get("total")
                if total not in (None, ""):
                    pts[pid] = float(total)
            rosters[key] = Roster(team_key=key, week=self.week, slots=slots)
            official[key] = pts

        matchups, team_official = [], {}
        for m in many((board.get("league") or {}).get("scoreboard", {}).get("matchups"), "matchup"):
            pair = many(m.get("teams"), "team")
            for t in pair:
                total = (t.get("team_points") or {}).get("total")
                if total not in (None, ""):
                    team_official[t["team_key"]] = float(total)
            if len(pair) == 2:
                matchups.append(Matchup(week=self.week, a=pair[0]["team_key"], b=pair[1]["team_key"]))
        if not fantasy_teams or not matchups:
            raise YahooError(f"league {self.league_key} week {self.week}: {len(fantasy_teams)} teams, {len(matchups)} matchups")

        paired = {k for m in matchups for k in (m.a, m.b)}
        for t in fantasy_teams:
            if t.key not in paired:
                log.warning("yahoo team %s has no week-%d matchup (bye) — left out of the console", t.name, self.week)
        self._league = League(key=self.league_key, name=str(self._meta.get("name", self.league_key)),
                              scoring_rules=dict(self._rules), teams=[t for t in fantasy_teams if t.key in paired])
        self._rosters, self._official = rosters, official
        self._matchups, self._team_official = matchups, team_official
        self.viewer = viewer if viewer in paired else None

    # --------------------------------------------------------------- contract

    def league(self) -> League:
        return self._league

    def roster(self, team_key: str, week: int) -> Roster:
        r = self._rosters[team_key]
        return Roster(team_key=r.team_key, week=week, slots=list(r.slots))

    def matchup(self, team_key: str, week: int) -> Matchup:
        for m in self._matchups:
            if team_key in (m.a, m.b):
                return m
        raise KeyError(team_key)

    def matchups(self, week: int) -> list[Matchup]:
        return list(self._matchups)

    def official_points(self, team_key: str, week: int) -> dict[str, float]:
        return dict(self._official.get(team_key, {}))

    # ----------------------------------------------------------------- extras

    def official_total(self, team_key: str) -> float | None:
        """Yahoo's own starter total for the week (drift checks)."""
        return self._team_official.get(team_key)
