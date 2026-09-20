"""The console engine: one play stream in, twelve viewers' worth of frames out.

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
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import WebSocket

from .grammar import compile_play
from .models import PlayRow, StatDelta
from .parser.desc import penalty_summary
from .providers.directory import NflversePlayerDirectory
from .providers.nflverse_plays import SlatePlayProvider
from .providers.slate import load_slate, slate_available
from .providers.stub_league import SyntheticLeagueProvider
from .scoring import LeagueIndex, MatchupBoard, ScoringState, ThreatEvent, ThreatHub
from .sim.clock import SimClock

log = logging.getLogger("retroffb.console")
SPRITES = Path(__file__).resolve().parents[2] / "data" / "sprites"
RATES = (1, 4, 15, 60)
RECENT_HALF_LIFE = 900.0           # sim seconds; "who matters right now"
ET = ZoneInfo("America/New_York")

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
    viewer: str = "t01"
    focus: str | None = None
    scope: str = "matchup"
    auto: bool = True               # AUTO-DIRECT: follow the action (demo / cast mode)
    last_play: PlayRow | None = None
    ghost: dict[str, str | None] = field(default_factory=lambda: {"you": None, "them": None})

    async def send(self, frame: dict) -> None:
        try:
            await self.ws.send_text(json.dumps(frame))
        except Exception:                                     # noqa: BLE001
            pass


class Engine:
    def __init__(self) -> None:
        self.slate = load_slate()
        self.directory = NflversePlayerDirectory.from_data_dir(week=self.slate.week, season=self.slate.season)
        self.league = SyntheticLeagueProvider.from_slate(seed=int(os.environ.get("RETROFFB_SEED", "1")), directory=self.directory)
        self.clock = SimClock(self.slate.duration, speed=float(os.environ.get("RETROFFB_SPEED", "4")),
                              start_at=float(os.environ.get("RETROFFB_START", "420")))
        if os.environ.get("RETROFFB_PROSE") == "1":          # rehearse the live path: prose is the only source
            from .providers.prose import reparse
            self.slate.plays[:] = [reparse(p, self.directory) for p in self.slate.plays]
            log.info("PROSE mode: %d plays rebuilt from their descriptions", len(self.slate.plays))
        self.provider = SlatePlayProvider(self.slate, self.clock)
        self.week = self.slate.week
        self.teams = {t.key: t for t in self.league.league().teams}
        self.rosters = {k: self.league.roster(k, self.week) for k in self.teams}
        self.opponent: dict[str, str] = {}
        for m in self.league.matchups(self.week):
            self.opponent[m.a], self.opponent[m.b] = m.b, m.a
        self.index = LeagueIndex.from_provider(self.league, self.week)
        self.state = ScoringState(self.league.league().scoring_rules)
        self.hub = ThreatHub(self.index, self.directory)
        self.boards = {k: MatchupBoard(self.rosters[k], self.rosters[self.opponent[k]], self.state) for k in self.teams}
        self.recent: dict[str, list[tuple[float, float]]] = {}
        self.aux: dict[str, dict[str, float]] = {}
        self.plays_by_id = {p.play_id: p for p in self.slate.plays}
        self.deltas_by_play: dict[str, list[StatDelta]] = {}
        self.last_by_game: dict[str, PlayRow] = {}
        self.sessions: set[Session] = set()
        self.air_est = _air_estimator()
        self.team_game = {t: g.id for g in self.slate.games for t in (g.home, g.away)}
        self._t0 = datetime.fromisoformat(self.slate.start_utc.replace("Z", "+00:00"))

    # ── ingest ────────────────────────────────────────────────────────────
    def ingest(self, play: PlayRow) -> dict[str, list[ThreatEvent]]:
        deltas = self.state.apply(play)
        self.deltas_by_play[play.play_id] = deltas
        self.last_by_game[play.game_id] = play
        for d in deltas:
            pts = self.event_points(d)
            if pts:
                self.recent.setdefault(d.player_id, []).append((play.sim_time, pts))
        if play.play_type == "pass" and play.receiver_id and not play.sack:
            a = self.aux.setdefault(play.receiver_id, {"tgt": 0, "air": 0.0})
            a["tgt"] += 1
            a["air"] += play.air_yards or 0.0
        flipped = {k for k, b in self.boards.items() if b.update()}
        real = [d for d in deltas if self.event_points(d)]    # no "game open +10" / "allows 3" noise
        return self.hub.ingest(play, real, play.sim_time, flipped)

    DEF_EVENTS = ("def_sack", "def_int", "def_fum_rec", "def_td", "def_safety", "def_block")

    def event_points(self, d: StatDelta) -> float:
        """Points from things that *happened*. A defence's points-allowed tier is
        bookkeeping: it moves the score but is not a play anyone made."""
        if not d.player_id.startswith("DEF-"):
            return d.points
        rules = self.league.league().scoring_rules
        return sum(rules.get(k, 0.0) * d.stats.get(k, 0.0) for k in self.DEF_EVENTS)

    def rebuild(self) -> None:
        now = self.clock.now()
        self.state.reset(); self.hub.reset()
        self.recent.clear(); self.aux.clear(); self.deltas_by_play.clear(); self.last_by_game.clear()
        for b in self.boards.values():
            b.reset()
        for g in self.slate.games:
            if g.kickoff <= now:
                self.state.open_game(g)
        for p in self.provider.plays_until(now):
            self.ingest(p)

    async def run(self) -> None:
        self.rebuild()
        self.clock.start()
        epoch = self.clock.epoch
        stream = self.provider.stream_plays(after=self.clock.now())
        async for play in stream:
            if self.clock.epoch != epoch:
                epoch = self.clock.epoch
                self.rebuild()
                for s in list(self.sessions):
                    await self.catch_up(s)
                if play.sim_time > self.clock.now() or play.play_id in self.deltas_by_play:
                    continue
            events = self.ingest(play)
            for s in list(self.sessions):
                await self.deliver(s, play, events.get(s.viewer, []))

    async def ticker(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            status = {g.id: g.status for g in self.provider.games_at(self.clock.now())}
            for s in list(self.sessions):
                if s.auto and status.get(s.focus or "") != "live" and "live" in status.values():
                    nxt = self.pick_focus(s, live_only=True)       # don't sit on a halftime feed
                    if nxt and nxt != s.focus:
                        s.focus = nxt
                        await self.catch_up(s)
                        continue
                await s.send(self.state_frame(s))

    # ── per-viewer delivery ───────────────────────────────────────────────
    async def deliver(self, s: Session, play: PlayRow, events: list[ThreatEvent]) -> None:
        board = self.hub.board(s.viewer)
        alerts = [e for e in events if e.side in ("you", "them") and board.should_alert(e)]
        top = max(alerts, key=lambda e: (e.lead_change, abs(e.delta_points)), default=None)
        if play.game_id == s.focus:
            s.last_play = play
            await s.send(self.play_frame(s, play, focus=True, alert=bool(top)))
        elif top and s.auto:
            s.focus, s.last_play = play.game_id, play
            await s.send({"type": "banner", "threat": self.threat_dict(top)})
            await s.send(self.play_frame(s, play, focus=True, alert=True))
        elif top:
            await s.send({"type": "banner", "threat": self.threat_dict(top)})
        await s.send(self.state_frame(s))

    async def catch_up(self, s: Session) -> None:
        if s.focus is None or s.focus not in self.last_by_game:
            s.focus = self.pick_focus(s)
        last = self.last_by_game.get(s.focus or "")
        s.last_play = last
        if last:
            await s.send(self.play_frame(s, last, focus=True, alert=False, settled=True))
        await s.send(self.state_frame(s))

    def pick_focus(self, s: Session, live_only: bool = False) -> str | None:
        """Start on the live game carrying the most of the viewer's matchup."""
        now, stake = self.clock.now(), {}
        mine = self.rosters[s.viewer].starters() + self.rosters[self.opponent[s.viewer]].starters()
        for slot in mine:
            p = self.directory.player(slot.player_id)
            g = self.team_game.get(p.team) if p else None
            if g:
                stake[g] = stake.get(g, 0) + 1
        live = [g for g in self.provider.games_at(now) if g.status in (("live",) if live_only else ("live", "half"))]
        pool = live or self.provider.games_at(now)
        return max(pool, key=lambda g: (stake.get(g.id, 0), -g.kickoff)).id if pool else None

    # ── frames ────────────────────────────────────────────────────────────
    def name(self, pid: str) -> str:
        p = self.directory.player(pid)
        if not p:
            return "UNKNOWN"
        if p.position == "DEF":
            return f"{p.team} DEF"
        parts = [w for w in p.name.split() if w.rstrip(".").upper() not in ("JR", "SR", "II", "III", "IV", "V")]
        return " ".join(parts[1:] or parts).upper()

    def sprite(self, pid: str) -> str | None:
        return f"/sprites/{pid}.png" if (SPRITES / f"{pid}.png").is_file() else None

    def threat_dict(self, e: ThreatEvent) -> dict:
        return {"id": f"{e.play_id}:{e.player_id}", "play_id": e.play_id, "game_id": e.game_id, "kind": e.kind,
                "name": self.name(e.player_id), "delta": round(e.delta_points, 1), "headline": e.headline,
                "lead_change": e.lead_change}

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
            side = self.index.side(s.viewer, a.player_id) if a.player_id else None
            if a.team == "def" and a.player_id and side is None:
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
        for d in self.deltas_by_play.get(p.play_id, []):
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
            "result_kind": "good" if net > 0.05 else "bad" if net < -0.05 else "neutral",
            "endzones": {"near": off, "far": other}, "deltas": deltas, "alert": alert, "focus": focus,
            "settled": settled, "template": c.template,
        }

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

    def heat_pick(self, s: Session, side: str, roster_key: str, now: float) -> dict | None:
        best, best_score, best_slot = None, -1.0, ""
        for slot in self.rosters[roster_key].starters():
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
        st = self.state.statline(pid)
        g = lambda k: int(round(st.get(k, 0)))                # noqa: E731
        parts = []
        if st.get("pass_att"):
            parts.append(f"{g('pass_cmp')}/{g('pass_att')} · {g('pass_yd')} YD · {g('pass_td')} TD" + (f" · {g('pass_int')} INT" if st.get("pass_int") else ""))
        if st.get("rush_att"):
            parts.append(f"{g('rush_att')} CAR · {g('rush_yd')} YD" + (f" · {g('rush_td')} TD" if st.get("rush_td") else ""))
        if st.get("tgt"):
            parts.append(f"{g('rec')} REC · {g('rec_yd')} YD" + (f" · {g('rec_td')} TD" if st.get("rec_td") else ""))
        if st.get("fg_att") or st.get("xp_att"):
            made = sum(g(k) for k in ("fg_0_39", "fg_40_49", "fg_50"))
            parts.append(f"{made}/{g('fg_att')} FG · {g('xp')}/{g('xp_att')} XP")
        if pid.startswith("DEF-"):
            parts.append(f"{g('pts_allowed')} PA · {g('def_sack')} SACK · {g('def_int') + g('def_fum_rec')} TO" + (f" · {g('def_td')} TD" if st.get("def_td") else ""))
        return "   ".join(parts) or "NO STATS YET"

    def active_card(self, s: Session) -> dict | None:
        p = s.last_play
        if not p:
            return None
        ranked = sorted(self.deltas_by_play.get(p.play_id, []), key=lambda d: (
            self.index.side(s.viewer, d.player_id) in ("you", "them"), abs(d.points)), reverse=True)
        pid = ranked[0].player_id if ranked else (p.receiver_id or p.rusher_id or p.passer_id or p.kicker_id)
        if not pid:
            return None
        pl = self.directory.player(pid)
        side = self.index.side(s.viewer, pid)
        aux = self.aux.get(pid)
        extra = [f"TGT {int(aux['tgt'])}", f"aDOT {aux['air'] / aux['tgt']:.1f}"] if aux and aux["tgt"] else []
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
        snap = self.boards[s.viewer].snapshot()
        opp = self.opponent[s.viewer]
        games = {g.id: g for g in self.provider.games_at(now)}
        marks = self.hub.board(s.viewer).per_game_status(now)

        def live(pid: str) -> bool:
            pl = self.directory.player(pid)
            g = games.get(self.team_game.get(pl.team, "")) if pl else None
            return bool(g and g.status in ("live", "half"))

        order = {"live": 0, "half": 0, "pre": 1, "final": 2}
        feeds = [{
            "game_id": g.id, "label": self.label(g.id), "clock": g.clock, "score": f"{g.away_score}-{g.home_score}",
            "status": {"pre": "PRE", "half": "HT", "final": "FINAL"}.get(g.status, f"Q{g.quarter}" if g.quarter <= 4 else "OT"),
            "mark": marks.get(g.id), "focused": g.id == s.focus,
        } for g in sorted(games.values(), key=lambda g: (order[g.status], g.kickoff, g.id))]
        lineup = [{
            "slot": r.slot,
            "you": {"player_id": r.you.player_id, "name": self.name(r.you.player_id), "points": round(r.you.points, 1), "live": live(r.you.player_id)},
            "them": {"player_id": r.them.player_id, "name": self.name(r.them.player_id), "points": round(r.them.points, 1), "live": live(r.them.player_id)},
            "losing": r.losing,
        } for r in snap.rows]
        wall = (self._t0 + timedelta(seconds=now)).astimezone(ET)
        return {
            "type": "state",
            "clock": {"label": f"WK{self.week} · {wall:%a %H:%M}".upper(), "sim": now, "duration": self.clock.duration,
                      "speed": self.clock.speed, "paused": not self.clock.running, "auto": s.auto},
            "viewer": {"team_key": s.viewer, "owner": self.teams[s.viewer].owner},
            "viewers": [{"team_key": t.key, "owner": t.owner, "name": t.name} for t in self.teams.values()],
            "matchup": {"you": {"name": f"{self.teams[s.viewer].owner} · {self.teams[s.viewer].name}", "points": round(snap.you_total, 1)},
                        "them": {"name": f"{self.teams[opp].name} · {self.teams[opp].owner}", "points": round(snap.them_total, 1)}},
            "feeds": feeds,
            "threats": [self.threat_dict(e) for e in self.hub.board(s.viewer).top(12, now, s.scope) if abs(e.delta_points) >= 0.5 or e.lead_change][:7],
            "scope": s.scope, "lineup": lineup,
            "ghosts": {"you": self.heat_pick(s, "you", s.viewer, now), "them": self.heat_pick(s, "them", opp, now)},
            "active": self.active_card(s),
        }

    # ── control ───────────────────────────────────────────────────────────
    async def handle(self, s: Session, m: dict) -> None:
        t = m.get("type")
        if t == "viewer" and m.get("team_key") in self.teams:
            s.viewer, s.focus, s.ghost = m["team_key"], None, {"you": None, "them": None}
            await self.catch_up(s)
        elif t == "focus_game" and m.get("game_id") in self.team_game.values():
            s.focus = m["game_id"]
            await self.catch_up(s)
        elif t == "focus_play" and m.get("play_id") in self.deltas_by_play:
            p = self.plays_by_id[m["play_id"]]
            s.focus, s.last_play = p.game_id, p
            await s.send(self.play_frame(s, p, focus=True, alert=True))
            await s.send(self.state_frame(s))
        elif t == "scope" and m.get("scope") in ("matchup", "league"):
            s.scope = m["scope"]
            await s.send(self.state_frame(s))
        elif t == "auto":
            s.auto = not s.auto
            await s.send(self.state_frame(s))
        elif t == "sim":
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
    s = Session(ws=None, viewer=viewer if viewer in engine.teams else "t01")    # type: ignore[arg-type]
    frames = []
    for p in picks:
        f = engine.play_frame(s, p, focus=False, alert=False)
        f["family"] = play_family(p)
        frames.append(f)
    return {"plays": frames, "families": dict(sorted(counts.items())), "pool": len(pool)}


engine: Engine | None = None
_by_ws: dict[WebSocket, Session] = {}


async def start(hub) -> list[asyncio.Task]:                   # noqa: ANN001
    global engine
    if not slate_available():
        raise RuntimeError("no slate — run scripts/fetch_nflverse.py then scripts/build_slate.py")
    engine = Engine()
    return [asyncio.create_task(engine.run()), asyncio.create_task(engine.ticker())]


async def on_connect(ws: WebSocket) -> None:
    if engine:
        s = Session(ws)
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
