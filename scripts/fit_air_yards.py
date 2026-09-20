"""Fit the air-yards prior (DESIGN §7) from nflverse play-by-play.

    PYTHONPATH=backend .venv/bin/python scripts/fit_air_yards.py \
        --train 2019 2020 2021 2022 2023 2024 --holdout 2025

Writes backend/retrogrid/parser/air_yards_prior.json: for every backoff cell
with at least --min-n plays, the median charted air yards and the cell count.
Missing seasons are downloaded into data/nflverse/.  Reports MAE on the
held-out season against two baselines.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from retrogrid.parser import air_yards as ay  # noqa: E402

URL = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{y}.parquet"
COLS = ["play_type", "two_point_attempt", "sack", "air_yards", "pass_length", "pass_location",
        "yards_gained", "down", "ydstogo", "complete_pass", "yardline_100", "qb_spike"]


def load(season: int) -> pd.DataFrame:
    path = ROOT / "data" / "nflverse" / f"play_by_play_{season}.parquet"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(URL.format(y=season), path)
    df = pd.read_parquet(path, columns=COLS)
    keep = (
        (df["play_type"] == "pass") & (df["sack"] != 1) & (df["two_point_attempt"] != 1)
        & df["air_yards"].notna()
    )
    return df[keep].reset_index(drop=True)


def _none(v: object) -> object:
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else v


def keys_for(df: pd.DataFrame) -> list[list[str]]:
    return [
        ay.cell_keys(_none(pl), _none(loc), _none(y), _none(d), _none(t), c == 1)
        for pl, loc, y, d, t, c in zip(df["pass_length"], df["pass_location"], df["yards_gained"],
                                       df["down"], df["ydstogo"], df["complete_pass"])
    ]


def fit(df: pd.DataFrame, min_n: int) -> dict[str, list[float]]:
    keys = keys_for(df)
    air = df["air_yards"].to_numpy(dtype=float)
    cells: dict[str, list[float]] = {}
    for level in range(len(ay.LEVELS)):
        grouped = pd.Series(air).groupby([k[level] for k in keys])
        stats = grouped.agg(["median", "count"])
        floor = 1 if level == len(ay.LEVELS) - 1 else min_n
        for key, row in stats[stats["count"] >= floor].iterrows():
            cells[str(key)] = [round(float(row["median"]), 1), int(row["count"])]
    return cells


def evaluate(df: pd.DataFrame, train: pd.DataFrame) -> dict[str, float]:
    truth = df["air_yards"].to_numpy(dtype=float)
    est = np.array([
        ay.estimate_air_yards(_none(pl), _none(loc), _none(y), _none(d), _none(t), c == 1, _none(yl))
        for pl, loc, y, d, t, c, yl in zip(df["pass_length"], df["pass_location"], df["yards_gained"],
                                           df["down"], df["ydstogo"], df["complete_pass"],
                                           df["yardline_100"])
    ])
    by_len = train.groupby(train["pass_length"].fillna("na"))["air_yards"].median()
    base_len = df["pass_length"].fillna("na").map(by_len).fillna(train["air_yards"].median()).to_numpy()
    err = np.abs(est - truth)
    comp = (df["complete_pass"] == 1).to_numpy()
    return {
        "n": float(len(df)),
        "mae": float(err.mean()),
        "mae_complete": float(err[comp].mean()),
        "mae_incomplete": float(err[~comp].mean()),
        "median_abs_err": float(np.median(err)),
        "within_2": float((err <= 2).mean()),
        "within_5": float((err <= 5).mean()),
        "baseline_global_median_mae": float(np.abs(train["air_yards"].median() - truth).mean()),
        "baseline_pass_length_mae": float(np.abs(base_len - truth).mean()),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--train", type=int, nargs="+", default=[2019, 2020, 2021, 2022, 2023, 2024])
    ap.add_argument("--holdout", type=int, default=2025)
    ap.add_argument("--min-n", type=int, default=25)
    a = ap.parse_args()

    train = pd.concat([load(y) for y in a.train], ignore_index=True)
    cells = fit(train, a.min_n)
    payload = {
        "meta": {"train_seasons": a.train, "n_train": len(train), "min_n": a.min_n,
                 "levels": [list(level) for level in ay.LEVELS],
                 "value": "[median air_yards, n]"},
        "cells": dict(sorted(cells.items())),
    }
    ay.PRIOR_PATH.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    ay._table.cache_clear()
    print(f"fitted {len(cells)} cells from {len(train):,} passes -> {ay.PRIOR_PATH} "
          f"({ay.PRIOR_PATH.stat().st_size / 1024:.0f} KB)")

    metrics = evaluate(load(a.holdout), train)
    payload["meta"]["holdout"] = {"season": a.holdout, **{k: round(v, 3) for k, v in metrics.items()}}
    ay.PRIOR_PATH.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    ay._table.cache_clear()
    print(f"held-out {a.holdout}:")
    for k, v in metrics.items():
        print(f"  {k:<28}{v:>10.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
