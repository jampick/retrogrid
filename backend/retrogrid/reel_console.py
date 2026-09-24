"""REEL: a looping highlight show of the weeks already played.

Same field, same frames, no clock and no scoring: the week is over, so the
engine is a rundown and a timer. One loop is

    TOP 10 · WK n        countdown, each play shown twice
    YOUR TEAMS           followed teams' plays from the latest week
    AROUND THE LEAGUE    every game of the latest week, two or three plays each, game order shuffled
    WK n-1 REWIND …      the top five of a couple of older weeks, different ones each loop

Every item runs card -> play -> result (-> replay). The card is the context a
highlight loses when it leaves its game: week, score going in, clock, down
and distance. Picks come from the reel cache (providers/reel.py); with no
cache the shipped SIM SUNDAY slate is ranked on the spot, so `retrogrid reel`
works with no downloads.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from dataclasses import dataclass

from . import paths
from .console import _JERSEY, _PREFIX, FAVS, Engine, Session, _air_estimator
from .models import PlayRow
from .providers.chatter import TEAMS
from .providers.directory import NflversePlayerDirectory
from .providers.reel import ReelDirectory, ReelWeek, load_weeks, week_from_slate
from .providers.slate import load_slate
from .scoring.reel import Pick

log = logging.getLogger("retrogrid.reel")
_PLAIN = re.compile(r"^([+-]\d+|PUNT|RETURN|INCOMPLETE)\b")
CARD = 4.0                         # seconds the context card holds the screen
SNAP = 0.9                         # the field's own pre-snap beat, before the animation clock starts
LINGER = 5.0                       # the result sits there
REPLAY_LINGER = 3.0
TOP_N = 10
REWIND_N = 5
REWIND_WEEKS = 2                   # older weeks per loop
PER_GAME = 3
REFRESH = 3600.0                   # look for a new week this often


@dataclass
class Item:
    pick: Pick
    week: ReelWeek
    segment: int
    number: str = ""               # "#7" in a countdown
    replay: bool = False


@dataclass
class Segment:
    title: str
    start: int = 0
    count: int = 0
    countdown: bool = False        # rows stay hidden until they air


def build_rundown(weeks: list[ReelWeek], favs: set[str], rng: random.Random) -> tuple[list[Segment], list[Item]]:
    """One loop of the show. `weeks` newest first."""
    segments: list[Segment] = []
    items: list[Item] = []
    if not weeks:
        return segments, items

    def add(title: str, picks: list[tuple[Pick, ReelWeek]], countdown: bool = False) -> None:
        if not picks:
            return
        seg = Segment(title, len(items), len(picks), countdown)
        for i, (k, w) in enumerate(picks):
            items.append(Item(k, w, len(segments), f"#{len(picks) - i}" if countdown else "", replay=countdown))
        segments.append(seg)

    def teams(k: Pick) -> set[str]:
        return set(k.play.game_id.split("_")[2:])

    latest = weeks[0]
    top = latest.picks[:TOP_N]
    add(f"TOP {len(top)} · WK {latest.week}", [(k, latest) for k in reversed(top)], countdown=True)
    aired = {k.play.play_id for k in top}
    mine = [k for k in latest.picks if teams(k) & favs and k.play.play_id not in aired][:6]
    add("YOUR TEAMS", [(k, latest) for k in sorted(mine, key=lambda k: (k.play.game_id, k.play.seq))])
    aired |= {k.play.play_id for k in mine}
    by_game: dict[str, list[Pick]] = {}
    for k in latest.picks:
        if k.play.play_id not in aired:
            by_game.setdefault(k.play.game_id, []).append(k)
    order = list(by_game)
    rng.shuffle(order)
    around = [k for g in order for k in sorted(by_game[g][:PER_GAME], key=lambda k: k.play.seq)]
    add(f"AROUND THE LEAGUE · WK {latest.week}", [(k, latest) for k in around])
    older = weeks[1:]
    for w in sorted(rng.sample(older, min(REWIND_WEEKS, len(older))), key=lambda w: -w.week):
        add(f"WK {w.week} REWIND", [(k, w) for k in reversed(w.picks[:REWIND_N])], countdown=True)
    return segments, items


def load_reel() -> list[ReelWeek]:
    weeks = load_weeks()
    if weeks:
        return weeks
    slate = load_slate(paths.sim_slate())                  # nothing built yet: the shipped Sunday is the show
    if paths.sim_slate() == paths.BUNDLED_SLATE:
        directory = NflversePlayerDirectory.from_snapshot(paths.BUNDLED_DIRECTORY)
    else:
        directory = NflversePlayerDirectory.from_data_dir(week=slate.week, season=slate.season)
    return [week_from_slate(slate, directory)]


class ReelEngine(Engine):
    def __init__(self, weeks: list[ReelWeek] | None = None, seed: int | None = None) -> None:   # no super(): no slate, no clock, no league
        self.weeks = weeks if weeks is not None else load_reel()
        self.directory = ReelDirectory(self.weeks)
        self.league, self.index, self.default_viewer = None, None, None
        self.deltas_by_play: dict = {}
        self.air_est = _air_estimator()
        self.sessions: set[Session] = set()
        self.reel = None               # a live Engine's pregame reel is one of these; this one is the whole show
        self.favs: set[str] = set(FAVS)
        self.rng = random.Random(seed)
        self.segments, self.items = build_rundown(self.weeks, self.favs, self.rng)
        self.i = 0
        self.phase = "card"            # card | play | result | replay
        self.paused = False
        self._jump: int | None = None
        self._wake = asyncio.Event()
        self._checked = time.monotonic()

    # ── the show ──────────────────────────────────────────────────────────
    @property
    def item(self) -> Item | None:
        return self.items[self.i] if 0 <= self.i < len(self.items) else None

    async def run(self) -> None:
        while True:
            if not self.items:
                await asyncio.sleep(5.0)
                self.reload()
                continue
            await self.air(self.items[self.i])
            if self._jump is not None:
                self.i, self._jump = max(0, min(len(self.items) - 1, self._jump)), None
            elif self.i + 1 < len(self.items):
                self.i += 1
            else:                                          # top of the show: new shuffle, maybe a new week
                self.reload()
                self.i = 0

    async def air(self, it: Item) -> None:
        self.phase = "card"
        await self.broadcast(lambda s: [self.card_frame(it), self.state_frame(s)])
        if await self.wait(CARD):
            return
        frame = None
        for phase in ("play", "replay") if it.replay else ("play",):
            self.phase = phase
            await self.broadcast(lambda s: [self.play_frame(s, it.pick.play, focus=True, alert=True), self.state_frame(s)])
            frame = frame or self.play_frame(Session(ws=None), it.pick.play, focus=True, alert=True)   # type: ignore[arg-type]
            if await self.wait(SNAP + frame["duration"]):
                return
            if phase == "play":
                self.phase = "result"                      # the scoreboard turns over when the play does
                await self.broadcast(lambda s: [self.state_frame(s)])
            if await self.wait(LINGER if phase == "play" else REPLAY_LINGER):
                return

    async def wait(self, seconds: float) -> bool:
        """Sleep, honouring HOLD. True: a jump cut the wait short."""
        end = time.monotonic() + seconds
        while True:
            if self._jump is not None:
                return True
            left = end - time.monotonic()
            if left <= 0 and not self.paused:
                return False
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), None if self.paused else left)
            except asyncio.TimeoutError:
                pass
            if self.paused:
                end = time.monotonic() + max(left, 0.0)    # the hold does not eat the item's time

    def jump(self, index: int) -> None:
        self._jump = index
        self._wake.set()

    async def broadcast(self, frames) -> None:             # noqa: ANN001
        for s in list(self.sessions):
            for f in frames(s):
                await s.send(f)

    async def ticker(self) -> None:
        """Hourly: has the builder (or the CLI's refresh thread) put a newer week on disk?"""
        while True:
            await asyncio.sleep(60.0)
            if time.monotonic() - self._checked >= REFRESH:
                self._checked = time.monotonic()
                await asyncio.to_thread(refresh_cache)

    async def league_pump(self, every: float = 300.0) -> None:
        return

    def reload(self) -> None:
        try:
            weeks = load_reel()
        except Exception as e:                             # noqa: BLE001 — keep the show we have
            log.warning("reel reload failed: %s", e)
            weeks = self.weeks
        if weeks:
            self.weeks, self.directory = weeks, ReelDirectory(weeks)
        self.segments, self.items = build_rundown(self.weeks, self.favs, self.rng)

    # ── frames ────────────────────────────────────────────────────────────
    async def catch_up(self, s: Session) -> None:
        it = self.item
        if it is None:
            return
        if self.phase == "card":
            await s.send(self.card_frame(it))
        else:
            await s.send(self.play_frame(s, it.pick.play, focus=True, alert=False, settled=True))
        await s.send(self.state_frame(s))

    def side(self, s: Session, pid: str | None, game_id: str | None = None) -> str | None:
        if pid and pid.startswith("DEF-") and game_id:
            _, _, away, home = game_id.split("_")
            return "you" if pid[4:] == away else "them" if pid[4:] == home else None
        return super().side(s, pid, game_id)

    def result_kind(self, s: Session, p: PlayRow, off: str) -> str:
        k = next((k for w in self.weeks for k in w.picks if k.play.play_id == p.play_id), None)
        if k is None:
            return "neutral"
        kind = "good" if k.team == off else "bad"
        other = {p.posteam, p.defteam} - {off}
        if other & self.favs and off not in self.favs:
            kind = "bad" if kind == "good" else "good"
        return kind

    def play_frame(self, s: Session, p: PlayRow, focus: bool, alert: bool, settled: bool = False) -> dict:
        f = super().play_frame(s, p, focus, alert, settled)
        it = next((x for x in self.items if x.pick.play.play_id == p.play_id), None)
        if it:
            f["reel"] = self.tag(it) | {"replay": self.phase == "replay"}
            if _PLAIN.match(f["result"]):                  # "+21 1ST DOWN" / "PUNT 51" undersell why this play is in the show
                f["result"] = it.pick.headline
        return f

    @staticmethod
    def tag(it: Item) -> dict:
        k = it.pick
        return {"week": it.week.week, "number": it.number, "rank": k.rank, "score": k.score, "tag": k.tag,
                "headline": k.headline, "wpa": k.play.wpa}

    @staticmethod
    def score_line(p: PlayRow, home: int, away: int) -> str:
        _, _, a, h = p.game_id.split("_")
        return f"{a} {away} · {h} {home}"

    def card_frame(self, it: Item) -> dict:
        p = it.pick.play
        q = f"Q{p.quarter}" if p.quarter <= 4 else "OT"
        return {"type": "reel_card", "segment": self.segments[it.segment].title, "number": it.number, "week": it.week.week,
                "label": self.label(p.game_id), "score": self.score_line(p, *it.pick.before),
                "clock": f"{q} {p.clock}", "situation": self.situation(p), "seconds": CARD}

    def star_card(self, it: Item) -> dict | None:
        p, star = it.pick.play, it.week.stars.get(it.pick.play.play_id)
        if star is None:
            return None
        pl = self.directory.player(star.player_id)
        final = it.week.final(p.game_id)
        text = _JERSEY.sub("", _PREFIX.sub("", p.desc)).strip()
        extra = [f"FINAL {self.score_line(p, *final)}"] if final else []
        if p.wpa is not None and it.pick.team:
            extra.append(f"{it.pick.team} WIN PROB +{abs(p.wpa) * 100:.0f}%")
        return {
            "player_id": star.player_id, "side": self.side(Session(ws=None), star.player_id, p.game_id),   # type: ignore[arg-type]
            "title": (pl.short if pl else star.player_id).upper(),
            "meta": "·".join(x for x in ((pl.position if pl else ""), (pl.team if pl else ""), (f"#{pl.number}" if pl and pl.number else "")) if x),
            "statline": star.statline or "DEFENCE", "points": star.points, "play_delta": star.play_points,
            "play_text": f"{p.quarter}Q {p.clock}  {text[:96]}", "extra": " · ".join(extra), "sprite": self.sprite(star.player_id),
        }

    def ghost(self, it: Item) -> dict:
        """One hologram: the star, on his team's flank."""
        out: dict = {"you": None, "them": None}
        star = it.week.stars.get(it.pick.play.play_id)
        if star is None or self.phase == "card":
            return out
        side = self.side(Session(ws=None), star.player_id, it.pick.play.game_id)     # type: ignore[arg-type]
        pl = self.directory.player(star.player_id)
        if side in out:
            meta = [pl.position if pl else "", pl.team if pl else "", f"#{pl.number}" if pl and pl.number else ""]
            out[side] = {"player_id": star.player_id, "name": self.name(star.player_id), "points": star.points,
                         "meta": " · ".join(m for m in meta if m), "heat": max(0.0, min(1.0, star.points / 24.0)),
                         "sprite": self.sprite(star.player_id)}
        return out

    def state_frame(self, s: Session) -> dict:
        it = self.item
        if it is None:
            return {"type": "state", "mode": "nfl", "ffb_available": False, "feeds": [], "threats": [], "lineup": [], "viewer": None,
                    "viewers": [], "scope": "action", "matchup": None, "favs": sorted(self.favs), "teams": sorted(TEAMS), "chatter": [],
                    "audio": None, "active": None, "ghosts": {"you": None, "them": None},
                    "clock": {"label": "REEL · NOTHING BUILT", "sim": 0, "duration": 1, "speed": 1, "paused": self.paused, "auto": False, "live": True}}
        p, seg = it.pick.play, self.segments[it.segment]
        _, _, away, home = p.game_id.split("_")
        after = self.phase in ("result", "replay")
        hs, as_ = (p.home_score, p.away_score) if after else it.pick.before
        q = f"Q{p.quarter}" if p.quarter <= 4 else "OT"

        def team(abbr: str) -> str:
            return f"{abbr} · {TEAMS[abbr][1].upper()}" if abbr in TEAMS else abbr

        feeds = [{"game_id": g["id"], "label": self.label(g["id"]), "clock": "", "score": f"{g['away_score']}-{g['home_score']}",
                  "status": "FINAL", "mark": None, "focused": g["id"] == p.game_id,
                  "fav": bool({g["home"], g["away"]} & self.favs)} for g in it.week.games]
        lo = max(seg.start, min(self.i - 3, seg.start + seg.count - 8))
        rows = []
        for j in range(lo, min(seg.start + seg.count, lo + 8)):
            x = self.items[j]
            hidden = seg.countdown and j > self.i or (j == self.i and self.phase in ("card", "play"))
            rows.append({"id": f"{x.week.week}:{x.pick.play.play_id}", "play_id": x.pick.play.play_id, "game_id": x.pick.play.game_id,
                         "kind": "fav" if set(x.pick.play.game_id.split("_")[2:]) & self.favs else "neutral",
                         "name": f"{x.number} {x.pick.tag}".strip() if not hidden else f"{x.number} · · ·".strip(),
                         "team": "" if hidden else x.pick.team, "delta": None, "label": "" if hidden else self.label(x.pick.play.game_id),
                         "headline": "" if hidden else x.pick.headline, "lead_change": x.pick.lead_change and not hidden, "current": j == self.i})
        return {
            "type": "state", "mode": "nfl", "ffb_available": False,
            "clock": {"label": f"WK{it.week.week} · {seg.title.split(' · ')[0]}", "sim": self.i + 1, "duration": len(self.items), "speed": 1,
                      "paused": self.paused, "auto": False, "live": True, "redzone": False, "riding": False},
            "reel": {"title": seg.title, "number": it.number, "index": self.i, "total": len(self.items), "phase": self.phase,
                     "week": it.week.week, "season": it.week.season, "at": f"{self.i - seg.start + 1}/{seg.count}",
                     "segments": [{"title": g.title, "count": g.count, "start": g.start, "current": g is seg} for g in self.segments]},
            "feeds": feeds, "threats": rows, "active": self.star_card(it) if self.phase != "card" else None,
            "favs": sorted(self.favs), "teams": sorted(TEAMS), "chatter": [], "audio": None,
            "viewer": None, "viewers": [], "scope": "action", "lineup": [],
            "matchup": {"you": {"name": team(away), "points": as_}, "them": {"name": team(home), "points": hs}, "status": f"{q} {p.clock}"},
            "ghosts": self.ghost(it),
        }

    # ── control ───────────────────────────────────────────────────────────
    async def handle(self, s: Session, m: dict) -> None:
        t = m.get("type")
        if t == "favs" and isinstance(m.get("teams"), list):
            self.favs = {x for x in m["teams"] if x in TEAMS}  # one show for every viewer; YOUR TEAMS changes with the next loop
        elif t == "reel_jump" and isinstance(m.get("index"), int):
            self.jump(m["index"])
        elif t == "focus_play":
            hit = next((j for j, x in enumerate(self.items) if x.pick.play.play_id == m.get("play_id")), None)
            if hit is not None:
                self.jump(hit)
        elif t == "focus_game":
            week = self.item.week if self.item else None
            hit = next((j for j, x in enumerate(self.items) if x.pick.play.game_id == m.get("game_id") and x.week is week and not x.number), None)
            if hit is not None:
                self.jump(hit)
        elif t == "sim" and self.items:
            a, v = m.get("action"), m.get("value")
            if a == "pause":
                self.paused = not self.paused
                self._wake.set()
            elif a == "skip" and isinstance(v, (int, float)):
                self.jump((self.i + (1 if v > 0 else -1)) % len(self.items))
            elif a == "seek" and isinstance(v, (int, float)):
                self.jump(int(max(0.0, min(1.0, float(v))) * (len(self.items) - 1)))
        await s.send(self.state_frame(s))


class PregameReel(ReelEngine):
    """The same show, filling a live console's wait for kickoff. Only finished
    weeks on disk (never the shipped sim ranked on the spot: a 2025 Sunday is
    not "last week"), and today's feeds stay in the rail so one click, or [B],
    brings the real game back. The host Engine decides who is watching."""

    def __init__(self, host: Engine, weeks: list[ReelWeek]) -> None:
        super().__init__(weeks=weeks)
        self.host = host

    @classmethod
    def build(cls, host: Engine) -> PregameReel | None:
        """Blocking: refresh the cache if nflverse moved (offline is fine), then
        load it. None when no finished week of this season is on disk."""
        refresh_cache()
        weeks = load_weeks(season=host.slate.season)
        if not weeks:
            return None
        try:
            from .tools.build_sprites import main as sprites
            sprites(["--reel"])
        except Exception as e:                             # noqa: BLE001 — busts are a nicety
            log.warning("pregame reel: sprites skipped (%s)", e)
        return cls(host, weeks)

    async def ticker(self) -> None:
        """Hourly while the wait is on; once the day is under way the cache can wait for next time."""
        while True:
            await asyncio.sleep(60.0)
            if time.monotonic() - self._checked >= REFRESH and self.host.waiting():
                self._checked = time.monotonic()
                await asyncio.to_thread(refresh_cache)

    def reload(self) -> None:
        weeks = load_weeks(season=self.host.slate.season) or self.weeks
        self.weeks, self.directory = weeks, ReelDirectory(weeks)
        self.segments, self.items = build_rundown(self.weeks, self.favs, self.rng)

    def state_frame(self, s: Session) -> dict:
        f = super().state_frame(s)
        f["feeds"] = self.host.feed_rows(s)                # today's games, PRE, where the viewer can click back to them
        f["clock"]["label"] = self.host.wall_label(self.host.clock.now())
        if "reel" in f:
            f["reel"]["pregame"] = self.host.kickoff_label()
        return f


def refresh_cache() -> None:
    """Blocking: ask nflverse whether the season file moved, and rank whatever is new.
    Offline is fine: the show goes on with what is on disk."""
    if os.environ.get("RETROGRID_REEL_REFRESH", "1") == "0":
        return
    from .watch import season_now
    from .tools.build_reel import refresh
    try:
        built = refresh(season_now())
        if built:
            log.info("reel: built weeks %s", built)
    except Exception as e:                                 # noqa: BLE001
        log.warning("reel refresh failed: %s", e)
