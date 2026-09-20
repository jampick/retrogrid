"""Prepare the SIM SUNDAY demo slate from nflverse files (DESIGN §7).

Picks the regular-season Sunday with the most games, then writes to the data dir's slate/:

    slate.json       week, date, games (kickoff on the sim axis, final scores)
    plays.jsonl      one PlayRow per line, sim_time = real wall-clock offset
    draft_pool.json  per-player fantasy production, for the synthetic league

    retrogrid build-slate [--week N]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Any

import numpy as np
import pandas as pd

from .. import paths
from ..models import PlayRow
from ..providers.slate import PLAYS_FILE, POOL_FILE, SLATE_FILE
from ..providers.stub_league import YAHOO_HALF_PPR, pa_tier_key

SEASON = 2025
SRC = paths.NFLVERSE
OUT = paths.DATA / "slate"
TAIL_SECONDS = 120.0            # sim time kept after the last play of the slate

ADMIN_DESCS = {"GAME", "END GAME"}
TACKLER_COLS = [
    "solo_tackle_1_player_id", "solo_tackle_2_player_id",
    "tackle_with_assist_1_player_id", "tackle_with_assist_2_player_id",
    "assist_tackle_1_player_id", "assist_tackle_2_player_id",
    "assist_tackle_3_player_id", "assist_tackle_4_player_id",
    "sack_player_id", "half_sack_1_player_id", "half_sack_2_player_id",
]
PLAY_TYPES = {"pass", "run", "field_goal", "extra_point", "punt", "kickoff",
              "qb_kneel", "qb_spike", "no_play"}


# ---------------------------------------------------------------- slate choice

def pick_slate(pbp: pd.DataFrame, week: int | None) -> tuple[int, str]:
    """(week, date) of the Sunday with the most games. Ties go to the latest
    week that is not the season finale: more weeks of prior production make
    better-looking rosters, and the finale is full of rested starters."""
    games = pbp[pbp.season_type == "REG"].drop_duplicates("game_id")
    games = games[pd.to_datetime(games.game_date).dt.dayofweek == 6]
    if week is not None:
        games = games[games.week == week]
    counts = games.groupby(["week", "game_date"]).size().reset_index(name="n")
    last_week = int(pbp[pbp.season_type == "REG"].week.max())
    counts["finale"] = counts.week == last_week
    best = counts.sort_values(["n", "finale", "week"], ascending=[False, True, False]).iloc[0]
    return int(best.week), str(best.game_date)


# ------------------------------------------------------------------- play rows

def _is_admin(row: Any) -> bool:
    """Rows with no fantasy or visual meaning: game/quarter markers, timeouts,
    two-minute warnings. Penalties and other no_plays are kept."""
    desc = row.desc
    if not isinstance(desc, str) or not desc.strip():
        return True
    if desc in ADMIN_DESCS or desc.startswith("END QUARTER"):
        return True
    if row.play_type == "no_play" and row.penalty != 1:
        return desc.startswith("Timeout") or desc.startswith("Two-Minute Warning")
    return not isinstance(row.play_type, str)


def _s(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None


def _i(v: Any) -> int | None:
    return None if pd.isna(v) else int(v)


def _f(v: Any) -> float | None:
    return None if pd.isna(v) else float(v)


def _b(v: Any) -> bool:
    return (not pd.isna(v)) and bool(v)


def _clock(v: Any) -> str:
    if not isinstance(v, str) or ":" not in v:
        return "0:00"
    m, s = v.split(":", 1)
    return f"{int(m)}:{s}"


def _sim_times(game: pd.DataFrame, t0: pd.Timestamp) -> np.ndarray:
    """Wall-clock offset per play; gaps interpolated, monotonic within a game."""
    t = pd.to_datetime(game.time_of_day, format="ISO8601", utc=True)
    secs = (t - t0).dt.total_seconds()
    secs = secs.interpolate(method="linear", limit_direction="both")
    return np.maximum.accumulate(secs.to_numpy()).round(3)


def to_play_row(r: Any, seq: int, sim_time: float) -> PlayRow:
    play_type = r.play_type if r.play_type in PLAY_TYPES else "no_play"
    tacklers: list[str] = []
    for col in TACKLER_COLS:
        pid = _s(getattr(r, col))
        if pid and pid not in tacklers:
            tacklers.append(pid)
    return PlayRow(
        play_id=f"{r.game_id}:{int(r.play_id)}",
        game_id=r.game_id,
        seq=seq,
        sim_time=float(sim_time),
        quarter=int(r.qtr),
        clock=_clock(r.time),
        down=_i(r.down),
        ydstogo=_i(r.ydstogo) if not pd.isna(r.down) else None,
        yardline_100=_i(r.yardline_100),
        posteam=_s(r.posteam),
        defteam=_s(r.defteam),
        desc=r.desc,
        play_type=play_type,
        yards_gained=_i(r.yards_gained) or 0,
        shotgun=_b(r.shotgun),
        no_huddle=_b(r.no_huddle),
        qb_scramble=_b(r.qb_scramble),
        pass_length=_s(r.pass_length),
        pass_location=_s(r.pass_location),
        air_yards=_f(r.air_yards),
        yards_after_catch=_f(r.yards_after_catch),
        run_location=_s(r.run_location),
        run_gap=_s(r.run_gap),
        complete=_b(r.complete_pass),
        touchdown=_b(r.touchdown),
        td_team=_s(r.td_team),
        interception=_b(r.interception),
        fumble_lost=_b(r.fumble_lost),
        sack=_b(r.sack),
        safety=_b(r.safety),
        penalty=_b(r.penalty),
        first_down=_b(r.first_down),
        two_point=_s(r.two_point_conv_result),
        field_goal_result=_s(r.field_goal_result),
        extra_point_result=_s(r.extra_point_result),
        kick_distance=_i(r.kick_distance),
        return_yards=_i(r.return_yards) or 0,
        passer_id=_s(r.passer_player_id),
        receiver_id=_s(r.receiver_player_id),
        rusher_id=_s(r.rusher_player_id),
        kicker_id=_s(r.kicker_player_id),
        interceptor_id=_s(r.interception_player_id),
        returner_id=_s(r.punt_returner_player_id) or _s(r.kickoff_returner_player_id),
        tackler_ids=tacklers,
        td_player_id=_s(r.td_player_id),
        fumbler_id=_s(r.fumbled_1_player_id),
        home_score=_i(r.total_home_score) or 0,
        away_score=_i(r.total_away_score) or 0,
    )


def build_plays(slate: pd.DataFrame) -> tuple[list[PlayRow], list[dict[str, Any]], pd.Timestamp]:
    stamps = pd.to_datetime(slate.time_of_day, format="ISO8601", utc=True)
    t0 = stamps.min()
    rows: list[PlayRow] = []
    games: list[dict[str, Any]] = []
    for game_id, game in slate.groupby("game_id", sort=True):
        sim = _sim_times(game, t0)
        keep = [not _is_admin(r) for r in game.itertuples(index=False)]
        seq = 0
        for r, t, ok in zip(game.itertuples(index=False), sim, keep):
            if ok:
                rows.append(to_play_row(r, seq, t))
                seq += 1
        first = game.iloc[0]
        kept = sim[np.array(keep)]
        games.append({
            "id": game_id, "home": first.home_team, "away": first.away_team,
            "kickoff": float(kept[0]), "end": float(kept[-1]),
            "kickoff_utc": (t0 + pd.Timedelta(seconds=float(kept[0]))).isoformat(),
            "home_score": int(first.home_score), "away_score": int(first.away_score),
            "plays": seq,
        })
    rows.sort(key=lambda p: (p.sim_time, p.game_id, p.seq))
    games.sort(key=lambda g: (g["kickoff"], g["id"]))
    return rows, games, t0


# ------------------------------------------------------------------ draft pool

def _col(df: pd.DataFrame, name: str) -> pd.Series:
    return df[name].fillna(0) if name in df else pd.Series(0.0, index=df.index)


def player_points(st: pd.DataFrame) -> pd.Series:
    R = YAHOO_HALF_PPR
    c = lambda n: _col(st, n)  # noqa: E731
    return (
        c("passing_yards") * R["pass_yd"] + c("passing_tds") * R["pass_td"]
        + c("passing_interceptions") * R["pass_int"]
        + c("rushing_yards") * R["rush_yd"] + c("rushing_tds") * R["rush_td"]
        + c("receptions") * R["rec"] + c("receiving_yards") * R["rec_yd"]
        + c("receiving_tds") * R["rec_td"] + c("special_teams_tds") * R["ret_td"]
        + (c("passing_2pt_conversions") + c("rushing_2pt_conversions")
           + c("receiving_2pt_conversions")) * R["two_pt"]
        + (c("sack_fumbles_lost") + c("rushing_fumbles_lost")
           + c("receiving_fumbles_lost")) * R["fum_lost"]
        + (c("fg_made_0_19") + c("fg_made_20_29") + c("fg_made_30_39")) * R["fg_0_39"]
        + c("fg_made_40_49") * R["fg_40_49"]
        + (c("fg_made_50_59") + c("fg_made_60_")) * R["fg_50"]
        + c("pat_made") * R["xp"]
    )


def defence_points(tm: pd.DataFrame, allowed: pd.Series) -> pd.Series:
    R = YAHOO_HALF_PPR
    c = lambda n: _col(tm, n)  # noqa: E731
    return (
        c("def_sacks") * R["def_sack"] + c("def_interceptions") * R["def_int"]
        + c("fumble_recovery_opp") * R["def_fum_rec"]
        + (c("def_tds") + c("special_teams_tds")) * R["def_td"]
        + c("def_safeties") * R["def_safety"]
        + (c("def_punt_blocks") + c("def_pat_blocks") + c("def_fg_blocks")) * R["def_block"]
        + allowed.map(lambda pa: R[pa_tier_key(int(pa))])
    )


def build_pool(pbp: pd.DataFrame, slate: pd.DataFrame, week: int) -> list[dict[str, Any]]:
    from retrogrid.providers.directory import TEAM_NAMES

    slate_games = set(slate.game_id)
    slate_teams = set(slate.home_team) | set(slate.away_team)
    id_cols = ["passer_player_id", "receiver_player_id", "rusher_player_id", "kicker_player_id"]
    scrimmage = slate[slate.play_type.isin(["pass", "run", "field_goal", "extra_point"])]
    active_ids = set(pd.unique(scrimmage[id_cols].to_numpy().ravel())) - {None, np.nan}

    st = pd.read_parquet(SRC / f"stats_player_week_{SEASON}.parquet")
    st = st[(st.season_type == "REG") & st.position.isin(["QB", "RB", "WR", "TE", "K"])].copy()
    st["pts"] = player_points(st)
    pre = st[st.week < week].groupby("player_id").agg(pre_points=("pts", "sum"), pre_games=("pts", "size"))
    now = st[(st.week == week) & st.game_id.isin(slate_games)].set_index("player_id")

    pool: list[dict[str, Any]] = []
    for pid, row in now.iterrows():
        pool.append({
            "id": pid, "name": row.player_display_name, "position": row.position, "team": row.team,
            "pre_points": round(float(pre.pre_points.get(pid, 0.0)), 2),
            "pre_games": int(pre.pre_games.get(pid, 0)),
            "week_points": round(float(row.pts), 2),
            "active": pid in active_ids,
        })

    tm = pd.read_parquet(SRC / f"stats_team_week_{SEASON}.parquet")
    tm = tm[tm.season_type == "REG"].copy()
    finals = pbp.drop_duplicates("game_id").set_index("game_id")[["home_team", "home_score", "away_score"]]
    joined = tm.join(finals, on="game_id")
    allowed = joined.away_score.where(joined.team == joined.home_team, joined.home_score)
    tm["pts"] = defence_points(tm, allowed)
    pre_t = tm[tm.week < week].groupby("team").agg(pre_points=("pts", "sum"), pre_games=("pts", "size"))
    now_t = tm[(tm.week == week) & tm.game_id.isin(slate_games)].set_index("team")
    for team in sorted(slate_teams):
        pool.append({
            "id": f"DEF-{team}", "name": TEAM_NAMES.get(team, team), "position": "DEF", "team": team,
            "pre_points": round(float(pre_t.pre_points.get(team, 0.0)), 2),
            "pre_games": int(pre_t.pre_games.get(team, 0)),
            "week_points": round(float(now_t.pts.get(team, 0.0)), 2),
            "active": True,
        })
    pool.sort(key=lambda e: (-e["pre_points"], e["id"]))
    return pool


# ------------------------------------------------------------------------ main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--week", type=int, default=None, help="force a week instead of auto-picking")
    args = ap.parse_args(argv)

    pbp_path = SRC / f"play_by_play_{SEASON}.parquet"
    if not pbp_path.exists():
        print("missing nflverse data — run `retrogrid fetch` first", file=sys.stderr)
        return 1
    pbp = pd.read_parquet(pbp_path)
    week, date = pick_slate(pbp, args.week)
    slate = pbp[(pbp.week == week) & (pbp.game_date == date)]

    plays, games, t0 = build_plays(slate)
    pool = build_pool(pbp, slate, week)
    duration = max(g["end"] for g in games) + TAIL_SECONDS

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / SLATE_FILE).write_text(json.dumps({
        "season": SEASON, "week": week, "date": date, "start_utc": t0.isoformat(),
        "duration": round(duration, 3), "games": games,
    }, indent=2))
    with (OUT / PLAYS_FILE).open("w") as fh:
        for p in plays:
            fh.write(json.dumps(asdict(p), separators=(",", ":")) + "\n")
    (OUT / POOL_FILE).write_text(json.dumps(pool, indent=1))

    print(f"slate: week {week}, {date}, {len(games)} games, {len(plays)} plays "
          f"(of {len(slate)} raw rows), {duration / 3600:.2f} h, pool {len(pool)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
