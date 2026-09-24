"""The console engine: one play stream in, every viewer's frames out.

The base layer is an NFL monitor: plays ranked by how much football they were
(scoring.action), a crowd (providers.chatter), a radio (providers.audio). The
fantasy layer — matchup bar, LINEUP, THREATS, ownership colours — sits on top
and exists only when a league is configured *and* the session has it on.

Everything here is downstream of the provider seams (DESIGN §7), so it is
identical whether plays come from SIM SUNDAY or, later, from ESPN.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import WebSocket

from .grammar import compile_play
from .models import SLOTS, PlayRow, StatDelta
from .parser.desc import penalty_summary
from .providers.audio import AudioTable
from .providers.chatter import TEAMS, ChatterBox, RedditChatter, StubChatter
from .providers.directory import NflversePlayerDirectory
from .providers.nflverse_plays import SlatePlayProvider
from . import paths
from .providers.slate import DEFAULT_SLATE_DIR, load_slate
from .providers.league import default_viewer, make_league
from .scoring import DEFAULT_RULES, ActionBoard, ActionEvent, LeagueIndex, MatchupBoard, ScoringState, ThreatEvent, ThreatHub
from .scoring.action import HOT_THRESHOLD
from .scoring.engine import statline_text
from .scoring.drive import DriveTracker
from .sim.clock import SimClock

log = logging.getLogger("retrogrid.console")
RATES = (1, 4, 15, 60)
RZ_LINGER = 5.0                    # wall seconds a resolved drive keeps the screen after its last play ends
RECENT_HALF_LIFE = 900.0           # sim seconds; "who matters right now"
ET = ZoneInfo("America/New_York")
LIVE = os.environ.get("RETROGRID_LIVE") == "1"          # start on today's real games off ESPN (watch.py may switch later)
REEL = os.environ.get("RETROGRID_REEL") == "1"          # the highlight show of finished weeks (reel_console.py)
CHATTER = os.environ.get("RETROGRID_CHATTER", "").lower()      # reddit | stub | off; default reddit when live, else stub
FAVS = [t for t in os.environ.get("RETROGRID_FAVS", "").upper().replace(",", " ").split() if t in TEAMS]

_PREFIX = re.compile(r"^(\(\d*:?\d+\)\s*)?(\((Shotgun|No Huddle|No Huddle, Shotgun)\)\s*)*", re.I)
_JERSEY = re.compile(r"\b\d{1,2}-(?=[A-Z][\w']*\.)")


def _air_estimator():
    try:
        from .parser.air_yards import estimate_air_yards      # learned prior (§7)
    except Exception:                                         # noqa: BLE001
        return None

    def est(p: PlayRow) -> float:
        return float(estimate_air_yards(
            pass_length=p.pass_length, pass_location=p.pass_location, yards_gained=p.yards_gained,
            down=p.down, ydstogo=p.ydstogo, complete=p.complete))
    return est


@dataclass(eq=False)
class Session:
    ws: WebSocket
    viewer: str | None = None       # fantasy team key; None when there is no league
    ffb: bool = False               # fantasy layer on for this session
    favs: set[str] = field(default_factory=lambda: set(FAVS))     # NFL teams this viewer follows
    focus: str | None = None
    scope: str = "matchup"
    auto: bool = True               # AUTO-DIRECT: follow the action (demo / cast mode)
    redzone: bool = False           # RED ZONE: ride whichever drive is inside the 20 until it resolves
    rz_lock: tuple[str, int] | None = None      # (game, drive seq) being ridden — or pinned by hand
    rz_hold: float = 0.0            # loop time before which the screen may not be taken away
    last_play: PlayRow | None = None
    ghost: dict[str, str | None] = field(default_factory=lambda: {"you": None, "them": None})

    async def send(self, frame: dict) -> None:
        try:
            await self.ws.send_text(json.dumps(frame))
        except Exception:                                     # noqa: BLE001
            pass


class Engine:
    """One slate, played out: the shipped Sunday on the sim clock, or one real
    day (live=True) on the wall clock with ESPN filling the plays in."""

    def __init__(self, live: bool | None = None) -> None:
        self.live = LIVE if live is None else live
        self.slate_dir = paths.LIVE_SLATE if self.live else DEFAULT_SLATE_DIR
        self.chatter_kind = CHATTER or ("reddit" if self.live else "stub")
        self.slate = load_slate(self.slate_dir)
        if self.slate_dir == paths.BUNDLED_SLATE:             # the shipped sim carries its own directory: no downloads
            self.directory = NflversePlayerDirectory.from_snapshot(paths.BUNDLED_DIRECTORY)
        else:
            self.directory = NflversePlayerDirectory.from_data_dir(week=self.slate.week, season=self.slate.season)
        self.league = make_league(self.slate_dir, self.directory, self.slate.week, self.slate.season,
                                  seed=int(os.environ.get("RETROGRID_SEED", "1")))
        self.clock = SimClock(self.slate.duration, speed=float(os.environ.get("RETROGRID_SPEED", "4")),
                              start_at=float(os.environ.get("RETROGRID_START", "420")))
        if os.environ.get("RETROGRID_PROSE") == "1":          # rehearse the live path: prose is the only source
            from .providers.prose import reparse
            self.slate.plays[:] = [reparse(p, self.directory) for p in self.slate.plays]
            log.info("PROSE mode: %d plays rebuilt from their descriptions", len(self.slate.plays))
        self.provider = SlatePlayProvider(self.slate, self.clock)
        if self.live:                                        # wall-clock time, plays as ESPN posts them
            from .providers.espn import EspnPlayProvider
            t0 = datetime.fromisoformat(self.slate.start_utc)
            self.clock = SimClock(self.slate.duration, speed=1.0, start_at=(datetime.now(t0.tzinfo) - t0).total_seconds())
            self.provider = EspnPlayProvider(self.slate, self.clock, self.directory)
        self.week = self.slate.week
        self.team_game = {t: g.id for g in self.slate.games for t in (g.home, g.away)}
        self.rules = self.league.league().scoring_rules if self.league else dict(DEFAULT_RULES)
        self.state = ScoringState(self.rules)
        self.action = ActionBoard(self.directory)
        self.drives = DriveTracker()
        self.chatter = ChatterBox()
        self.crowd = StubChatter(self.chatter, self.directory) if self.chatter_kind == "stub" else None
        self.audio = AudioTable()
        self.by_team: dict[str, set[str]] = {}               # NFL team -> players seen making plays
        self.hub: ThreatHub | None = None
        self.boards: dict[str, MatchupBoard] = {}
        self.load_league()
        self.recent: dict[str, list[tuple[float, float]]] = {}
        self.aux: dict[str, dict[str, float]] = {}
        self.plays_by_id = {p.play_id: p for p in self.slate.plays}
        self.deltas_by_play: dict[str, list[StatDelta]] = {}
        self.last_by_game: dict[str, PlayRow] = {}
        self.sessions: set[Session] = set()
        self.air_est = _air_estimator()
        self._t0 = datetime.fromisoformat(self.slate.start_utc.replace("Z", "+00:00"))

    # ── league (slow lane, §9) ────────────────────────────────────────────
    def load_league(self) -> None:
        """Read teams, rosters and matchups off the provider. Called again after
        every slow-lane refresh; boards keep their lead memory."""
        if self.league is None:
            self.teams, self.rosters, self.opponent, self.index, self.default_viewer = {}, {}, {}, None, None
            return
        self.teams = {t.key: t for t in self.league.league().teams}
        self.default_viewer = default_viewer(self.league)
        self.rosters = {k: self.league.roster(k, self.week) for k in self.teams}
        self.opponent = {}
        for m in self.league.matchups(self.week):
            self.opponent[m.a], self.opponent[m.b] = m.b, m.a
        self.index = LeagueIndex(self.rosters.values(), self.league.matchups(self.week))
        if self.hub is None:
            self.hub = ThreatHub(self.index, self.directory)
        else:
            self.hub.set_index(self.index)
        for k in self.teams:
            you, them = self.rosters[k], self.rosters[self.opponent[k]]
            if k in self.boards:
                self.boards[k].set_rosters(you, them)
            else:
                self.boards[k] = MatchupBoard(you, them, self.state, getattr(self.league, "slots", SLOTS))
        self.state.set_base(self.off_slate_points())

    def off_slate_points(self) -> dict[str, float]:
        """Official points of rostered players whose game is not in the slate."""
        base: dict[str, float] = {}
        for k in self.teams:
            for pid, pts in self.league.official_points(k, self.week).items():
                pl = self.directory.player(pid)
                if pts and (pl is None or pl.team not in self.team_game):
                    base[pid] = pts
        return base

    async def league_pump(self, every: float = 300.0) -> None:
        """Lineup changes and late swaps; official points for drift."""
        refresh = getattr(self.league, "refresh", None) if self.league else None
        while refresh is not None:
            await asyncio.sleep(every)
            try:
                await asyncio.to_thread(refresh)
            except Exception as e:                            # noqa: BLE001 — keep the last good league
                log.warning("league refresh failed: %s", e)
                continue
            self.load_league()
            self.log_drift()

    def log_drift(self, tolerance: float = 1.0) -> None:
        """Local engine vs Yahoo's own numbers. Yahoo lags live play by minutes,
        so this is a diagnostic, never a correction."""
        for k in self.teams:
            for pid, theirs in self.league.official_points(k, self.week).items():
                ours = self.state.points(pid)
                if abs(ours - theirs) >= tolerance:
                    log.info("drift %-14s ours %6.2f  yahoo %6.2f  (%+.2f)", self.name(pid), ours, theirs, ours - theirs)

    # ── ingest ────────────────────────────────────────────────────────────
    def ingest(self, play: PlayRow) -> tuple[ActionEvent | None, dict[str, list[ThreatEvent]]]:
        deltas = self.state.apply(play)
        act = self.action.ingest(play)
        self.drives.ingest(play)
        if self.crowd:
            self.crowd.react(play, act.tag if act else None, act.team if act else None)
        self.plays_by_id[play.play_id] = play                 # live: the slate grows as we go
        self.deltas_by_play[play.play_id] = deltas
        self.last_by_game[play.game_id] = play
        for d in deltas:
            pts = self.event_points(d)
            if pts:
                self.recent.setdefault(d.player_id, []).append((play.sim_time, pts))
                pl = self.directory.player(d.player_id)
                if pl:
                    self.by_team.setdefault(pl.team, set()).add(d.player_id)
        if play.play_type == "pass" and play.receiver_id and not play.sack:
            a = self.aux.setdefault(play.receiver_id, {"tgt": 0, "air": 0.0})
            a["tgt"] += 1
            a["air"] += play.air_yards or 0.0
        if self.hub is None:
            return act, {}
        flipped = {k for k, b in self.boards.items() if b.update()}
        real = [d for d in deltas if self.event_points(d)]    # no "game open +10" / "allows 3" noise
        return act, self.hub.ingest(play, real, play.sim_time, flipped)

    DEF_EVENTS = ("def_sack", "def_int", "def_fum_rec", "def_td", "def_safety", "def_block")

    def event_points(self, d: StatDelta) -> float:
        """Points from things that *happened*. A defence's points-allowed tier is
        bookkeeping: it moves the score but is not a play anyone made."""
        if not d.player_id.startswith("DEF-"):
            return d.points
        return sum(self.rules.get(k, 0.0) * d.stats.get(k, 0.0) for k in self.DEF_EVENTS)

    def rebuild(self) -> None:
        now = self.clock.now()
        self.state.reset(); self.action.reset(); self.drives.reset()
        if self.hub:
            self.hub.reset()
        if self.crowd:
            self.chatter.reset()                              # the stub crowd is re-derived from the plays
        self.by_team.clear()
        self.recent.clear(); self.aux.clear(); self.deltas_by_play.clear(); self.last_by_game.clear()
        for b in self.boards.values():
            b.reset()
        for g in self.slate.games:
            if g.kickoff <= now:
                self.state.open_game(g)
        for p in self.provider.plays_until(now):
            self.ingest(p)

    async def run(self) -> None:
        poll: asyncio.Task | None = None
        try:
            if self.live:
                await self.provider.prime()                   # whatever has already been played today
                poll = asyncio.get_running_loop().create_task(self.provider.run())
            self.rebuild()
            self.clock.start()
            epoch = self.clock.epoch
            stream = self.provider.stream_plays(after=self.clock.now())
            async for play in stream:
                if self.clock.epoch != epoch:
                    epoch = self.clock.epoch
                    self.rebuild()
                    for s in list(self.sessions):
                        s.rz_lock, s.rz_hold = None, 0.0      # drive numbering restarted with the rebuild
                        await self.catch_up(s)
                    if play.sim_time > self.clock.now() or play.play_id in self.deltas_by_play:
                        continue
                act, events = self.ingest(play)
                for s in list(self.sessions):
                    await self.deliver(s, play, act, events.get(s.viewer or "", []))
        finally:
            if poll:                                          # the day is over for this engine: stop polling ESPN
                poll.cancel()

    async def ticker(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            status = {g.id: g.status for g in self.provider.games_at(self.clock.now())}
            for s in list(self.sessions):
                if await self.rz_direct(s, status):
                    continue
                if s.auto and not self.rz_riding(s) and status.get(s.focus or "") != "live" and "live" in status.values():
                    nxt = self.pick_focus(s, live_only=True)       # don't sit on a halftime feed
                    if nxt and nxt != s.focus:
                        s.focus = nxt
                        await self.catch_up(s)
                        continue
                await s.send(self.state_frame(s))

    # ── per-viewer delivery ───────────────────────────────────────────────
    async def deliver(self, s: Session, play: PlayRow, act: ActionEvent | None, events: list[ThreatEvent]) -> None:
        if s.ffb:
            board = self.hub.board(s.viewer)
            alerts = [e for e in events if e.side in ("you", "them") and board.should_alert(e)]
            best = max(alerts, key=lambda e: (e.lead_change, abs(e.delta_points)), default=None)
            top = self.threat_dict(best) if best else None
        else:
            top = self.action_dict(act, s) if act and self.action.should_alert(act, s.favs) else None
        if play.game_id == s.focus:
            s.last_play = play
            frame = self.play_frame(s, play, focus=True, alert=bool(top))
            if s.redzone and (top or (s.rz_lock and s.rz_lock[0] == s.focus)):    # let a ridden drive's play be watched before cutting away
                s.rz_hold = asyncio.get_running_loop().time() + 0.9 + frame["duration"] + RZ_LINGER
            await s.send(frame)
        elif top and s.auto and not self.rz_riding(s):
            s.focus, s.last_play = play.game_id, play
            await s.send({"type": "banner", "threat": top})
            await s.send(self.play_frame(s, play, focus=True, alert=True))
        elif top:
            await s.send({"type": "banner", "threat": top})
        await s.send(self.state_frame(s))

    async def catch_up(self, s: Session) -> None:
        if s.focus is None or s.focus not in self.last_by_game:
            s.focus = self.pick_focus(s)
        last = self.last_by_game.get(s.focus or "")
        s.last_play = last
        if last:
            await s.send(self.play_frame(s, last, focus=True, alert=False, settled=True))
        await s.send(self.state_frame(s))

    # ── RED ZONE ──────────────────────────────────────────────────────────
    def rz_riding(self, s: Session) -> bool:
        """Is the session committed to a drive (or a hand-picked feed) right now?"""
        if not (s.redzone and s.rz_lock):
            return False
        game, seq = s.rz_lock
        return game == s.focus and self.drives.get(game).seq == seq

    async def rz_direct(self, s: Session, status: dict[str, str]) -> bool:
        """Stay on a red-zone drive until it resolves, then cut to the next one.
        Followed teams first, then whoever is closest to the goal line."""
        if not s.redzone:
            return False
        if self.rz_riding(s) and status.get(s.focus or "") == "live":
            return False
        if asyncio.get_running_loop().time() < s.rz_hold:
            return False
        s.rz_lock = None
        hot = [(g, d) for g, d in self.drives.drives.items() if d.red_zone and status.get(g) == "live"]
        if not hot:
            return False
        game, d = min(hot, key=lambda gd: (gd[0] != s.focus, not ({*gd[0].split("_")[2:]} & s.favs), gd[1].spot, gd[1].since))
        s.rz_lock = (game, d.seq)
        if game == s.focus:
            return False
        s.focus = game
        await self.catch_up(s)
        return True

    def pick_focus(self, s: Session, live_only: bool = False) -> str | None:
        """Start on the live game carrying the most of the viewer's matchup — or,
        with no fantasy layer, a followed team's game, else wherever the action is."""
        now, stake = self.clock.now(), {}
        if s.ffb:
            mine = self.rosters[s.viewer].starters() + self.rosters[self.opponent[s.viewer]].starters()
            for slot in mine:
                p = self.directory.player(slot.player_id)
                g = self.team_game.get(p.team) if p else None
                if g:
                    stake[g] = stake.get(g, 0) + 1
        else:
            stake = dict(self.action.heat(now))
            for t in s.favs:
                if t in self.team_game:
                    stake[self.team_game[t]] = stake.get(self.team_game[t], 0) + 1000.0
        live = [g for g in self.provider.games_at(now) if g.status in (("live",) if live_only else ("live", "half"))]
        pool = live or self.provider.games_at(now)
        return max(pool, key=lambda g: (stake.get(g.id, 0), -g.kickoff)).id if pool else None

    # ── frames ────────────────────────────────────────────────────────────
    def name(self, pid: str | None) -> str:
        if pid is None:
            return "EMPTY"                                    # an unfilled lineup slot
        p = self.directory.player(pid)
        if not p:
            return "UNKNOWN"
        if p.position == "DEF":
            return f"{p.team} DEF"
        parts = [w for w in p.name.split() if w.rstrip(".").upper() not in ("JR", "SR", "II", "III", "IV", "V")]
        return " ".join(parts[1:] or parts).upper()

    def sprite(self, pid: str) -> str | None:
        return f"/sprites/{pid}.png" if paths.sprite(pid) else None

    def threat_dict(self, e: ThreatEvent) -> dict:
        return {"id": f"{e.play_id}:{e.player_id}", "play_id": e.play_id, "game_id": e.game_id, "kind": e.kind,
                "name": self.name(e.player_id), "delta": round(e.delta_points, 1), "headline": e.headline,
                "lead_change": e.lead_change}

    def action_dict(self, e: ActionEvent, s: Session) -> dict:
        """An ActionEvent in the THREATS wire shape: no points, a tag and the team it favoured."""
        _, _, away, home = e.game_id.split("_")
        return {"id": e.play_id, "play_id": e.play_id, "game_id": e.game_id,
                "kind": "fav" if (away in s.favs or home in s.favs) else "neutral",
                "name": e.tag, "team": e.team, "delta": None, "headline": e.headline, "lead_change": e.lead_change}

    def side(self, s: Session, pid: str | None, game_id: str | None = None) -> str | None:
        """The colour a player wears. Fantasy: whose roster. NFL: away is the
        left-hand colour, home the right — same two lights, different meaning."""
        if not pid:
            return None
        if s.ffb:
            return self.index.side(s.viewer, pid)
        pl = self.directory.player(pid)
        if not pl or not game_id:
            return None
        _, _, away, home = game_id.split("_")
        return "you" if pl.team == away else "them" if pl.team == home else None

    def label(self, game_id: str) -> str:
        _, _, away, home = game_id.split("_")
        return f"{away}@{home}"

    def play_frame(self, s: Session, p: PlayRow, focus: bool, alert: bool, settled: bool = False) -> dict:
        def pos_of(pid: str) -> str | None:
            pl = self.directory.player(pid)
            return pl.position if pl else None

        c = compile_play(p, pos_of, self.air_est)
        actors = []
        for a in c.actors:
            side = self.side(s, a.player_id, p.game_id) if a.involved else None
            if s.ffb and a.team == "def" and a.player_id and side is None:
                pl = self.directory.player(a.player_id)      # defender scoring for a rostered DEF
                side = self.index.side(s.viewer, f"DEF-{pl.team}") if pl else None
            actors.append({
                "id": a.id, "role": a.role, "team": a.team, "involved": a.involved,
                "label": self.name(a.player_id) if a.involved and a.player_id else None,
                "side": side if side in ("you", "them") else None,
                "keys": [{"t": round(k.t, 3), "x": round(k.x, 2), "y": round(k.y, 2), **({"z": round(k.z, 2)} if k.z else {})} for k in a.keys],
            })
        deltas = []
        net = 0.0
        for d in self.deltas_by_play.get(p.play_id, []) if s.ffb else ():
            side = self.index.side(s.viewer, d.player_id)
            if side in ("you", "them") and abs(d.points) >= 0.05:
                net += d.points if side == "you" else -d.points
                deltas.append({"player_id": d.player_id, "name": self.name(d.player_id), "points": round(d.points, 1), "side": side})
        _, _, away, home = p.game_id.split("_")
        off = p.posteam or home
        if p.play_type == "kickoff":
            off = p.defteam or away
        other = away if off == home else home
        return {
            "type": "play", "play_id": p.play_id, "game_id": p.game_id, "label": self.label(p.game_id),
            "situation": self.situation(p), "desc": p.desc, "los": c.los, "to_go": c.to_go, "los_line": c.los_line,
            "duration": round(c.duration, 2), "actors": actors, "result": self.result(p),
            "result_kind": ("good" if net > 0.05 else "bad" if net < -0.05 else "neutral") if s.ffb else self.result_kind(s, p, off),
            "endzones": {"near": off, "far": other}, "deltas": deltas, "alert": alert, "focus": focus,
            "settled": settled, "template": c.template,
        }

    def result_kind(self, s: Session, p: PlayRow, off: str) -> str:
        """Good or bad for the offence — flipped when the viewer follows the defence."""
        hit = next((e for e in reversed(self.action.events) if e.play_id == p.play_id), None)
        kind = ("good" if hit.team == off else "bad") if hit else "good" if p.first_down else "neutral"
        other = {p.posteam, p.defteam} - {off}
        if kind != "neutral" and other & s.favs and off not in s.favs:
            kind = "bad" if kind == "good" else "good"
        return kind

    @staticmethod
    def situation(p: PlayRow) -> str:
        if p.play_type in ("kickoff", "extra_point") or not p.down:
            return {"kickoff": "KICKOFF", "extra_point": "PAT"}.get(p.play_type, "2-PT TRY" if p.two_point else "")
        yl = p.yardline_100 or 50
        spot = "MIDFIELD" if yl == 50 else f"{p.posteam if yl > 50 else p.defteam} {100 - yl if yl > 50 else yl}"
        togo = "GOAL" if p.ydstogo and p.ydstogo >= yl else str(p.ydstogo)
        return f"{('1ST', '2ND', '3RD', '4TH')[p.down - 1]} & {togo} · {spot}"

    @staticmethod
    def result(p: PlayRow) -> str:
        if p.play_type == "no_play" or (p.penalty and "no play" in p.desc.lower()):
            team, foul, offsetting = penalty_summary(p.desc)
            if offsetting:
                return "OFFSETTING FLAGS - NO PLAY"
            if not foul:
                return "FLAG - NO PLAY"
            short = foul.upper().replace("DEFENSIVE ", "DEF ").replace("OFFENSIVE ", "OFF ").replace("UNNECESSARY ", "")
            return f"FLAG - {team + ' ' if team else ''}{short}"
        if p.touchdown:
            return "TOUCHDOWN"
        if p.interception:
            return "INTERCEPTED"
        if p.fumble_lost:
            return "FUMBLE LOST"
        if p.safety:
            return "SAFETY"
        if p.sack:
            return f"SACK {p.yards_gained}"
        if p.two_point:
            return "2-PT GOOD" if p.two_point == "success" else "2-PT FAILS"
        if p.play_type == "field_goal":
            return f"{p.kick_distance or ''} YD FG GOOD".strip() if p.field_goal_result == "made" else f"FG {(p.field_goal_result or 'missed').upper()}"
        if p.play_type == "extra_point":
            return "PAT GOOD" if p.extra_point_result == "good" else "PAT NO GOOD"
        if p.play_type == "punt":
            return f"PUNT {p.kick_distance or ''}".strip()
        if p.play_type == "kickoff":
            return "TOUCHBACK" if "touchback" in p.desc.lower() else f"RETURN {p.return_yards}"
        if p.play_type == "qb_kneel":
            return "KNEEL"
        if p.play_type == "qb_spike":
            return "SPIKE"
        if p.play_type == "pass" and not p.complete:
            return "INCOMPLETE"
        return f"{p.yards_gained:+d}" + (" 1ST DOWN" if p.first_down else "")

    def heat_pick(self, s: Session, side: str, key: str | None, now: float) -> dict | None:
        """The hologram for one flank. `key` is a fantasy roster — or, with no
        fantasy layer, an NFL team: that side's hottest hand in the focused game."""
        from .models import RosterSlot
        if key is None:
            return None
        pool = self.rosters[key].starters() if s.ffb else [RosterSlot("", pid) for pid in sorted(self.by_team.get(key, ()))]
        best, best_score, best_slot = None, -1.0, ""
        for slot in pool:
            score = sum(abs(pts) * 0.5 ** ((now - t) / RECENT_HALF_LIFE) for t, pts in self.recent.get(slot.player_id, []) if t <= now)
            if slot.player_id.startswith("DEF-"):
                score *= 0.5                                  # a shield is a poor ghost; faces first
            else:
                score += self.state.points(slot.player_id) * 0.04
            if slot.player_id == s.ghost[side]:
                score *= 1.3                                  # hysteresis: ghosts should not flap
            if score > best_score:
                best, best_score, best_slot = slot.player_id, score, slot.slot
        if best is None:
            return None
        s.ghost[side] = best
        pts = self.state.points(best)
        pl = self.directory.player(best)
        pos = pl.position if pl else ""
        meta = [best_slot if best_slot != pos else "", pos, pl.team if pl else "", f"#{pl.number}" if pl and pl.number else ""]
        return {"player_id": best, "name": self.name(best), "points": round(pts, 1), "meta": " · ".join(m for m in meta if m),
                "heat": max(0.0, min(1.0, pts / 24.0)), "sprite": self.sprite(best)}

    def statline(self, pid: str) -> str:
        return statline_text(self.state.statline(pid), pid.startswith("DEF-"))

    def active_card(self, s: Session) -> dict | None:
        p = s.last_play
        if not p:
            return None
        ranked = sorted(self.deltas_by_play.get(p.play_id, []), key=lambda d: (
            s.ffb and self.index.side(s.viewer, d.player_id) in ("you", "them"),
            not d.player_id.startswith("DEF-"), abs(d.points)), reverse=True)
        pid = ranked[0].player_id if ranked else (p.receiver_id or p.rusher_id or p.passer_id or p.kicker_id)
        if not pid:
            return None
        pl = self.directory.player(pid)
        side = self.side(s, pid, p.game_id)
        aux = self.aux.get(pid)
        extra = [f"TGT {int(aux['tgt'])}", f"aDOT {aux['air'] / aux['tgt']:.1f}"] if aux and aux["tgt"] else []
        if s.ffb:
            owner = next((self.teams[k].name for k, r in self.rosters.items() if any(sl.player_id == pid for sl in r.slots)), None)
            extra.append(f"ROSTERED · {owner}" if owner else "UNROSTERED")
        text = _JERSEY.sub("", _PREFIX.sub("", p.desc)).strip()
        return {
            "player_id": pid, "side": side if side in ("you", "them") else None,
            "title": (pl.short if pl else pid).upper(),
            "meta": "·".join(x for x in ((pl.position if pl else ""), (pl.team if pl else ""), (f"#{pl.number}" if pl and pl.number else "")) if x),
            "statline": self.statline(pid), "points": round(self.state.points(pid), 1),
            "play_delta": round(next((d.points for d in ranked if d.player_id == pid), 0.0), 1),
            "play_text": f"{p.quarter}Q {p.clock}  {text[:96]}", "extra": " · ".join(extra), "sprite": self.sprite(pid),
        }

    def state_frame(self, s: Session) -> dict:
        now = self.clock.now()
        games = {g.id: g for g in self.provider.games_at(now)}
        if s.ffb:
            marks = self.hub.board(s.viewer).per_game_status(now)
        else:
            marks = {g: "hot" for g, h in self.action.heat(now).items() if h >= HOT_THRESHOLD}

        def live(pid: str | None) -> bool:
            pl = self.directory.player(pid) if pid else None
            g = games.get(self.team_game.get(pl.team, "")) if pl else None
            return bool(g and g.status in ("live", "half"))

        order = {"live": 0, "half": 0, "pre": 1, "final": 2}
        feeds = [{
            "game_id": g.id, "label": self.label(g.id), "clock": g.clock, "score": f"{g.away_score}-{g.home_score}",
            "status": {"pre": "PRE", "half": "HT", "final": "FINAL"}.get(g.status, f"Q{g.quarter}" if g.quarter <= 4 else "OT"),
            "mark": marks.get(g.id), "focused": g.id == s.focus, "fav": g.home in s.favs or g.away in s.favs,
            "rz": g.status == "live" and self.drives.get(g.id).red_zone,
        } for g in sorted(games.values(), key=lambda g: (order[g.status], g.kickoff, g.id))]
        wall = (self._t0 + timedelta(seconds=now)).astimezone(ET)
        frame = {
            "type": "state", "mode": "ffb" if s.ffb else "nfl", "ffb_available": self.league is not None,
            "clock": {"label": f"WK{self.week} · {wall:%a %H:%M}".upper(), "sim": now, "duration": self.clock.duration,
                      "speed": self.clock.speed, "paused": not self.clock.running, "auto": s.auto, "live": self.live,
                      "redzone": s.redzone, "riding": self.rz_riding(s) and self.drives.get(s.focus or "").red_zone},
            "feeds": feeds, "active": self.active_card(s),
            "favs": sorted(s.favs), "teams": sorted(self.team_game),
            "chatter": self.chatter.recent(s.focus), "audio": self.audio.for_game(s.focus),
        }
        return {**frame, **(self.ffb_layer(s, now, live) if s.ffb else self.nfl_layer(s, now, games.get(s.focus or "")))}

    def nfl_layer(self, s: Session, now: float, g) -> dict:                       # noqa: ANN001
        """Status bar = the focused game's real scoreboard; board = ACTION; ghosts = each side's hot hand."""
        def team(abbr: str) -> str:
            return f"{abbr} · {TEAMS[abbr][1].upper()}" if abbr in TEAMS else abbr
        status = {"pre": "PRE", "half": "HALF", "final": "FINAL"}.get(g.status, f"Q{g.quarter} {g.clock}" if g.quarter <= 4 else f"OT {g.clock}") if g else ""
        return {
            "viewer": None, "viewers": [], "scope": "action", "lineup": [],
            "matchup": {"you": {"name": team(g.away), "points": g.away_score}, "them": {"name": team(g.home), "points": g.home_score},
                        "status": status} if g else None,
            "threats": [self.action_dict(e, s) for e in self.action.top(7, now, s.favs)],
            "ghosts": {"you": self.heat_pick(s, "you", g.away, now), "them": self.heat_pick(s, "them", g.home, now)} if g else {"you": None, "them": None},
        }

    def ffb_layer(self, s: Session, now: float, live) -> dict:                    # noqa: ANN001
        snap = self.boards[s.viewer].snapshot()
        opp = self.opponent[s.viewer]
        lineup = [{
            "slot": r.slot,
            "you": {"player_id": r.you.player_id, "name": self.name(r.you.player_id), "points": round(r.you.points, 1), "live": live(r.you.player_id)},
            "them": {"player_id": r.them.player_id, "name": self.name(r.them.player_id), "points": round(r.them.points, 1), "live": live(r.them.player_id)},
            "losing": r.losing,
        } for r in snap.rows]
        return {
            "viewer": {"team_key": s.viewer, "owner": self.teams[s.viewer].owner},
            "viewers": [{"team_key": t.key, "owner": t.owner, "name": t.name} for t in self.teams.values()],
            "matchup": {"you": {"name": f"{self.teams[s.viewer].owner} · {self.teams[s.viewer].name}", "points": round(snap.you_total, 1)},
                        "them": {"name": f"{self.teams[opp].name} · {self.teams[opp].owner}", "points": round(snap.them_total, 1)},
                        "status": ""},
            "threats": [self.threat_dict(e) for e in self.hub.board(s.viewer).top(12, now, s.scope) if abs(e.delta_points) >= 0.5 or e.lead_change][:7],
            "scope": s.scope, "lineup": lineup,
            "ghosts": {"you": self.heat_pick(s, "you", s.viewer, now), "them": self.heat_pick(s, "them", opp, now)},
        }

    # ── control ───────────────────────────────────────────────────────────
    async def handle(self, s: Session, m: dict) -> None:
        t = m.get("type")
        if t == "viewer" and m.get("team_key") in self.teams:
            s.viewer, s.focus, s.ghost = m["team_key"], None, {"you": None, "them": None}
            await self.catch_up(s)
        elif t == "ffb" and self.league is not None:          # the fantasy layer, on or off
            s.ffb = bool(m["on"]) if "on" in m else not s.ffb
            s.ghost = {"you": None, "them": None}
            await self.catch_up(s)
        elif t == "favs" and isinstance(m.get("teams"), list):
            s.favs = {x for x in m["teams"] if x in TEAMS}
            if s.focus is None or not s.ffb and s.auto:
                s.focus = None
            await self.catch_up(s)
        elif t == "focus_game" and m.get("game_id") in self.team_game.values():
            s.focus = m["game_id"]
            s.rz_lock = (s.focus, self.drives.get(s.focus).seq)   # a hand-picked feed is kept until its drive resolves
            await self.catch_up(s)
        elif t == "focus_play" and m.get("play_id") in self.deltas_by_play:
            p = self.plays_by_id[m["play_id"]]
            s.focus, s.last_play = p.game_id, p
            s.rz_lock = (s.focus, self.drives.get(s.focus).seq)
            await s.send(self.play_frame(s, p, focus=True, alert=True))
            await s.send(self.state_frame(s))
        elif t == "scope" and m.get("scope") in ("matchup", "league"):
            s.scope = m["scope"]
            await s.send(self.state_frame(s))
        elif t == "redzone":
            s.redzone = bool(m["on"]) if "on" in m else not s.redzone
            s.rz_lock, s.rz_hold = None, 0.0
            await s.send(self.state_frame(s))
        elif t == "auto":
            s.auto = not s.auto
            await s.send(self.state_frame(s))
        elif t == "sim" and not self.live:                    # nobody fast-forwards a real Sunday
            a, v = m.get("action"), m.get("value")
            if a == "pause":
                self.clock.pause() if self.clock.running else self.clock.start()
            elif a == "speed" and v in RATES:
                self.clock.set_speed(float(v))
            elif a == "seek" and isinstance(v, (int, float)):
                self.clock.seek(max(0.0, min(1.0, float(v))) * self.clock.duration)
            elif a == "skip" and isinstance(v, (int, float)):
                self.clock.seek(self.clock.now() + float(v))
            await s.send(self.state_frame(s))


# ── grammar contact sheet (DESIGN §8 Validation) ───────────────────────────
def play_family(p: PlayRow) -> str:
    """Coarse bucket for the contact sheet's filter — one per choreography."""
    t = p.play_type
    if t == "no_play" or (p.penalty and "no play" in p.desc.lower()):
        return "flag"
    if t in ("punt", "kickoff", "qb_kneel", "qb_spike"):
        return t.removeprefix("qb_")
    if t in ("field_goal", "extra_point"):
        return "placekick"
    if p.sack:
        return "sack"
    if p.interception:
        return "int"
    if p.qb_scramble:
        return "scramble"
    if t == "pass":
        air = p.air_yards if p.air_yards is not None else (20 if p.pass_length == "deep" else 5)
        return "screen" if air <= 0.5 else "deep" if air >= 15 else "short"
    if t == "run":
        return "run"
    return "other"


def sample_reel(n: int = 40, week: int | None = None) -> dict:
    """The reel's picks for one cached week (default: the newest), best first:
    the contact sheet as a check on the ranker rather than the grammar."""
    from .reel_console import ReelEngine, load_reel
    weeks = engine.weeks if isinstance(engine, ReelEngine) else load_reel()
    w = next((x for x in weeks if x.week == week), weeks[0] if weeks else None)
    if w is None:
        return {"plays": [], "families": {}, "pool": 0}
    e = ReelEngine([w])
    s = Session(ws=None)                                      # type: ignore[arg-type]
    frames, counts = [], {}
    for k in w.picks[:n]:
        f = Engine.play_frame(e, s, k.play, focus=False, alert=False)
        f["family"] = play_family(k.play)
        f["reel"] = {"week": w.week, "number": f"#{k.rank}", "rank": k.rank, "score": k.score, "tag": k.tag,
                     "headline": k.headline, "wpa": k.play.wpa, "replay": False}
        counts[k.tag] = counts.get(k.tag, 0) + 1
        frames.append(f)
    return {"plays": frames, "families": dict(sorted(counts.items())), "pool": len(w.picks), "weeks": [x.week for x in weeks]}


def sample_plays(n: int = 24, seed: int = 0, family: str | None = None, viewer: str = "t01",
                 only: str | None = None, grep: str | None = None) -> dict:
    """N seeded-random compiled plays from the slate, as ordinary PlayFrames.
    `grep` narrows the pool to descriptions containing that text (case-insensitive)."""
    import random
    if not engine:
        return {"plays": [], "families": {}}
    counts: dict[str, int] = {}
    pool = []
    needle = (grep or "").lower()
    for p in engine.slate.plays:
        if needle and needle not in p.desc.lower():
            continue
        f = play_family(p)
        if p.touchdown:
            counts["td"] = counts.get("td", 0) + 1
        counts[f] = counts.get(f, 0) + 1
        if only:
            if p.play_id == only:
                pool.append(p)
        elif family in (None, "", "all", f) or (family == "td" and p.touchdown):
            pool.append(p)
    rng = random.Random(seed)
    picks = pool if len(pool) <= n else rng.sample(pool, n)
    s = Session(ws=None, viewer=viewer if viewer in engine.teams else engine.default_viewer,    # type: ignore[arg-type]
                ffb=engine.league is not None)
    frames = []
    for p in picks:
        f = engine.play_frame(s, p, focus=False, alert=False)
        f["family"] = play_family(p)
        frames.append(f)
    return {"plays": frames, "families": dict(sorted(counts.items())), "pool": len(pool)}


engine: Engine | None = None
_by_ws: dict[WebSocket, Session] = {}


def launch(e: Engine) -> list[asyncio.Task]:
    """The tasks that play one engine out; cancel them all to retire it."""
    tasks = [asyncio.create_task(e.run()), asyncio.create_task(e.ticker()), asyncio.create_task(e.league_pump())]
    if e.chatter_kind == "reddit":
        tasks.append(asyncio.create_task(RedditChatter(e.chatter, lambda: e.provider.games_at(e.clock.now())).run()))
    return tasks


async def start(hub) -> list[asyncio.Task]:                   # noqa: ANN001
    global engine
    if REEL:
        from .reel_console import ReelEngine
        engine = ReelEngine()
        return [asyncio.create_task(engine.run()), asyncio.create_task(engine.ticker())]
    from .watch import run as watch                            # which day's engine is up, and when it changes
    return [asyncio.create_task(watch())]


async def on_connect(ws: WebSocket) -> None:
    if engine:
        s = Session(ws, engine.default_viewer, ffb=engine.league is not None)
        _by_ws[ws] = s
        engine.sessions.add(s)
        await engine.catch_up(s)


async def on_message(ws: WebSocket, msg: dict) -> None:
    s = _by_ws.get(ws)
    if engine and s:
        await engine.handle(s, msg)


def on_disconnect(ws: WebSocket) -> None:
    s = _by_ws.pop(ws, None)
    if engine and s:
        engine.sessions.discard(s)
