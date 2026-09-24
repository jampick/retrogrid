"""The reel cache: one file per finished week, `<DATA>/reel/<season>_wk<NN>.json`.

A week file is self-contained: the picked plays as PlayRows, the score going
into each, the star of each play with his final line for that game, and the
handful of players the diagrams name. So the reel runs with no parquet and no
pandas: `retrogrid build-reel` writes these, the console only reads them.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .. import paths
from ..models import Player, PlayRow
from ..scoring.engine import DEFAULT_RULES, ScoringState, def_id, statline_text
from ..scoring.reel import PER_WEEK, Pick, pick_week

REEL_DIR = paths.DATA / "reel"
DEFENCE_TAGS = {"FUM", "STOP", "SAFETY", "BLOCK", "SACK"}


@dataclass
class Star:
    player_id: str
    statline: str                 # his whole game, not the game so far: the week is over
    points: float                 # fantasy points for that game
    play_points: float            # and for this play


@dataclass
class ReelWeek:
    season: int
    week: int
    games: list[dict[str, Any]]                       # id, home, away, home_score, away_score (finals)
    picks: list[Pick]                                 # best first, rank 1..n
    stars: dict[str, Star] = field(default_factory=dict)          # play_id -> the face of the play
    players: dict[str, Player] = field(default_factory=dict)      # everyone a diagram or card can name
    source: str = "nflverse"                          # or "slate": ranked without WPA

    def final(self, game_id: str) -> tuple[int, int] | None:
        g = next((g for g in self.games if g["id"] == game_id), None)
        return (int(g["home_score"]), int(g["away_score"])) if g else None


class ReelDirectory:
    """`directory.player()` over the players the week files carry."""

    def __init__(self, weeks: list[ReelWeek]) -> None:
        self._players: dict[str, Player] = {}
        for w in weeks:
            self._players.update(w.players)

    def player(self, player_id: str) -> Player | None:
        return self._players.get(player_id)


def week_path(season: int, week: int, reel_dir: Path | str = REEL_DIR) -> Path:
    return Path(reel_dir) / f"{season}_wk{week:02d}.json"


def star_id(k: Pick) -> str | None:
    """Whose play it was. Defensive plays with no single name go to the team shield."""
    p = k.play
    if p.touchdown and p.td_player_id:
        return p.td_player_id
    if k.tag == "INT":
        return p.interceptor_id or def_id(k.team)
    if k.tag in ("FG", "MISS") and p.kicker_id and k.team == p.posteam:
        return p.kicker_id
    if k.tag == "SACK" and p.tackler_ids:
        return p.tackler_ids[0]
    if k.tag in DEFENCE_TAGS or k.team != p.posteam and p.play_type in ("pass", "run"):
        return def_id(k.team) if k.team else None
    if p.play_type in ("punt", "kickoff"):
        return p.returner_id or p.kicker_id
    return p.receiver_id or p.rusher_id or p.passer_id or p.kicker_id


def _ids(p: PlayRow) -> set[str]:
    ids = {p.passer_id, p.receiver_id, p.rusher_id, p.kicker_id, p.interceptor_id, p.returner_id,
           p.td_player_id, p.fumbler_id, *p.tackler_ids}
    ids |= {def_id(t) for t in (p.posteam, p.defteam) if t}
    return {i for i in ids if i}


def make_week(season: int, week: int, by_game: dict[str, list[PlayRow]], games: list[dict[str, Any]],
              directory, n: int = PER_WEEK, source: str = "nflverse") -> ReelWeek:      # noqa: ANN001
    """Rank a week and gather what the picks need. `by_game` is every play of every game."""
    picks = pick_week(by_game, directory, n)
    out = ReelWeek(season, week, games, picks, source=source)
    wanted = {k.play.play_id: k for k in picks}
    for plays in by_game.values():
        state = ScoringState(dict(DEFAULT_RULES))
        deltas: dict[str, dict[str, float]] = {}
        for p in sorted(plays, key=lambda r: r.seq):
            ds = state.apply(p)
            if p.play_id in wanted:
                deltas[p.play_id] = {d.player_id: d.points for d in ds}
        for p in plays:
            k = wanted.get(p.play_id)
            pid = star_id(k) if k else None
            if pid:
                line = statline_text(state.statline(pid), pid.startswith("DEF-"))
                out.stars[p.play_id] = Star(pid, "" if line == "NO STATS YET" else line,       # defenders have no fantasy line
                                            round(state.points(pid), 1), round(deltas[p.play_id].get(pid, 0.0), 1))
            if k:
                for i in _ids(p) | ({pid} if pid else set()):
                    pl = directory.player(i) if directory else None
                    if pl:
                        out.players[i] = pl
    return out


def save_week(w: ReelWeek, reel_dir: Path | str = REEL_DIR) -> Path:
    path = week_path(w.season, w.week, reel_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "season": w.season, "week": w.week, "source": w.source, "games": w.games,
        "picks": [{"rank": k.rank, "score": k.score, "tag": k.tag, "team": k.team, "headline": k.headline,
                   "lead_change": k.lead_change, "before": list(k.before), "play": asdict(k.play),
                   "star": asdict(w.stars[k.play.play_id]) if k.play.play_id in w.stars else None} for k in w.picks],
        "players": [[p.id, p.name, p.short, p.position, p.team, p.number, p.headshot] for p in w.players.values()],
    }
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps(body, separators=(",", ":"), ensure_ascii=False))
    tmp.replace(path)
    return path


def load_week(path: Path | str) -> ReelWeek:
    raw = json.loads(Path(path).read_text())
    w = ReelWeek(int(raw["season"]), int(raw["week"]), raw["games"], [], source=raw.get("source", "nflverse"))
    for e in raw["picks"]:
        play = PlayRow(**e["play"])
        w.picks.append(Pick(play, e["score"], e["tag"], e["team"], e["headline"], e["lead_change"], tuple(e["before"]), e["rank"]))
        if e.get("star"):
            w.stars[play.play_id] = Star(**e["star"])
    w.players = {row[0]: Player(*row) for row in raw["players"]}
    return w


def load_weeks(reel_dir: Path | str = REEL_DIR, season: int | None = None) -> list[ReelWeek]:
    """Every cached week, newest first. With no `season`, the newest season on disk."""
    files = sorted(Path(reel_dir).glob("*_wk*.json"), reverse=True)
    if season is None and files:
        season = int(files[0].name[:4])
    out = []
    for f in files:
        if f.name.startswith(f"{season}_"):
            try:
                out.append(load_week(f))
            except (ValueError, KeyError, TypeError):      # a half-written or older-format file: skip it, the builder will redo it
                continue
    return out


def week_from_slate(slate, directory) -> ReelWeek:                # noqa: ANN001
    """Rank a prepared slate (the shipped SIM SUNDAY, or a LIVE day) as if it were a week."""
    by_game: dict[str, list[PlayRow]] = {}
    for p in slate.plays:
        by_game.setdefault(p.game_id, []).append(p)
    games = [{"id": g.id, "home": g.home, "away": g.away,
              "home_score": slate.final_scores.get(g.id, (0, 0))[0], "away_score": slate.final_scores.get(g.id, (0, 0))[1]}
             for g in slate.games]
    has_wpa = any(p.wpa is not None for p in slate.plays)
    return make_week(slate.season, slate.week, by_game, games, directory, source="nflverse" if has_wpa else "slate")
