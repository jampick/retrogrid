"""Validate retroffb.parser.desc against nflverse's own parsed columns.

    PYTHONPATH=backend .venv/bin/python scripts/validate_parser.py [--season 2024] [--samples 8]

nflverse ships the raw `desc` string *and* the derived columns, so every play
is a labelled example.  Prints a per-field agreement table and writes
data/parser_report.md (agreement % + a sample of disagreements per field).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from retroffb.parser.desc import parse_desc  # noqa: E402

SCRIMMAGE = ["pass", "run", "qb_kneel", "qb_spike"]


def _flag(s: pd.Series) -> pd.Series:
    return s.fillna(0).astype(float).astype(int)


def _text(s: pd.Series) -> pd.Series:
    return s.astype(object).where(s.notna(), None)


def _num(s: pd.Series) -> pd.Series:
    return s.astype(float).fillna(-9999).round().astype(int)


# (label, parsed column, truth column, population name, normaliser)
Spec = tuple[str, str, str, str, Callable[[pd.Series], pd.Series]]
SPECS: list[Spec] = [
    ("play_type", "play_type", "play_type", "all", _text),
    ("shotgun", "shotgun", "shotgun", "scrimmage", _flag),
    ("no_huddle", "no_huddle", "no_huddle", "scrimmage", _flag),
    ("qb_scramble", "qb_scramble", "qb_scramble", "scrimmage", _flag),
    ("pass_length", "pass_length", "pass_length", "scrimmage", _text),
    ("pass_location", "pass_location", "pass_location", "scrimmage", _text),
    ("run_location", "run_location", "run_location", "scrimmage", _text),
    ("run_gap", "run_gap", "run_gap", "scrimmage", _text),
    ("complete_pass", "complete", "complete_pass", "scrimmage", _flag),
    ("yards_gained", "yards_gained", "yards_gained", "scrimmage", _num),
    ("yards_gained (no fumble/lateral)", "yards_gained", "yards_gained", "scrimmage_clean", _num),
    ("touchdown", "touchdown", "touchdown", "scrimmage", _flag),
    ("interception", "interception", "interception", "scrimmage", _flag),
    ("sack", "sack", "sack", "scrimmage", _flag),
    ("fumble", "fumble", "fumble", "scrimmage", _flag),
    ("fumble_lost", "fumble_lost", "fumble_lost", "scrimmage", _flag),
    ("safety", "safety", "safety", "scrimmage", _flag),
    ("penalty", "penalty", "penalty", "scrimmage", _flag),
    ("two_point_conv_result", "two_point_result", "two_point_conv_result", "scrimmage", _text),
    ("passer_name", "passer", "passer_player_name", "scrimmage", _text),
    ("receiver_name", "receiver", "receiver_player_name", "scrimmage", _text),
    ("rusher_name", "rusher", "rusher_player_name", "scrimmage", _text),
    ("interceptor_name", "interceptor", "interception_player_name", "scrimmage", _text),
    ("td_player_name", "td_scorer", "td_player_name", "scrimmage", _text),
    ("fumbler_name", "fumbler", "fumbled_1_player_name", "scrimmage", _text),
    ("field_goal_result", "field_goal_result", "field_goal_result", "field_goal", _text),
    ("kick_distance (FG)", "kick_distance", "kick_distance", "field_goal", _num),
    ("kicker_name (FG/XP)", "kicker", "kicker_player_name", "fg_xp", _text),
    ("extra_point_result", "extra_point_result", "extra_point_result", "extra_point", _text),
    ("kick_distance (punt)", "kick_distance", "kick_distance", "punt", _num),
    ("punter_name", "punter", "punter_player_name", "punt", _text),
    ("punt_returner_name", "returner", "punt_returner_player_name", "punt", _text),
    ("kick_distance (kickoff)", "kick_distance", "kick_distance", "kickoff", _num),
    ("kicker_name (kickoff)", "kicker", "kicker_player_name", "kickoff", _text),
    ("kickoff_returner_name", "returner", "kickoff_returner_player_name", "kickoff", _text),
    ("touchdown (special teams)", "touchdown", "touchdown", "special", _flag),
    ("fumble_lost (special teams)", "fumble_lost", "fumble_lost", "special", _flag),
]


NOTES = """
## Reading the residuals

The misses that remain are nflverse-side derivations that are not in the prose:

- **kick_distance (punt / kickoff)** - the prose distance is parsed exactly; nflverse
  re-derives the column (muffed kicks measured to the recovery spot, out-of-bounds or
  short kickoffs recorded as 25, blocked punts as 0, old touchbacks capped at 65).
- **play_type** - a handful of kickoffs / extra points carrying "No Play" that nflverse
  keeps as kickoff / extra_point, and 'Illegal Forward Pass' plays it files as runs.
- **yards_gained** - fumble, lateral and aborted-snap plays, where the stat feed nets
  yardage by rules the text only partly exposes (`posteam` gives the parser direction
  of play; `--prose-only` shows the score without it).
- **rusher_name** - aborted snaps recovered and advanced by a team-mate, 'Handoff to' /
  'Pass back to' after a bobbled snap: nflverse credits the eventual runner.
- two-point tries: nflverse leaves run_location / complete_pass empty and records a
  converted try as a 2-yard gain; the parser mirrors that on purpose.
"""


def populations(df: pd.DataFrame) -> dict[str, pd.Series]:
    pt = df["play_type"]
    scrim = pt.isin(SCRIMMAGE)
    messy = (df["fumble"].fillna(0) == 1) | df["desc"].str.contains("Lateral", na=False)
    return {
        "all": df["desc"].notna(),
        "scrimmage": scrim,
        "scrimmage_clean": scrim & ~messy,
        "field_goal": pt == "field_goal",
        "extra_point": pt == "extra_point",
        "fg_xp": pt.isin(["field_goal", "extra_point"]),
        "punt": pt == "punt",
        "kickoff": pt == "kickoff",
        "special": pt.isin(["field_goal", "extra_point", "punt", "kickoff"]),
    }


def tackler_agreement(df: pd.DataFrame, parsed: pd.DataFrame, mask: pd.Series) -> tuple[int, int, pd.Index]:
    cols = [c for c in df.columns if c.startswith(("solo_tackle_", "assist_tackle_", "tackle_with_assist_"))
            and c.endswith("_player_name")]
    truth = df.loc[mask, cols].apply(lambda r: frozenset(v for v in r if isinstance(v, str)), axis=1)
    ours = parsed.loc[mask, "tacklers"].apply(frozenset)
    ok = truth == ours
    return int(ok.sum()), int(mask.sum()), ok.index[~ok]


def run(season: int, samples: int, path: Path | None, prose_only: bool = False,
        report_path: Path | None = None) -> int:
    src = path or ROOT / "data" / "nflverse" / f"play_by_play_{season}.parquet"
    if not src.exists():
        print(f"missing {src}", file=sys.stderr)
        return 2
    df = pd.read_parquet(src).reset_index(drop=True)
    df = df[df["desc"].notna()].reset_index(drop=True)
    teams = [None] * len(df) if prose_only else [t if isinstance(t, str) else None for t in df["posteam"]]
    parsed = pd.DataFrame([asdict(parse_desc(d, posteam=t)) for d, t in zip(df["desc"], teams)])
    pops = populations(df)

    rows: list[tuple[str, str, int, int, float]] = []
    report: list[str] = [f"# Parser agreement vs nflverse {season}", "",
                         f"{len(df):,} plays from `{src.name}`.", ""]
    detail: list[str] = []
    for label, pcol, tcol, pop, norm in SPECS:
        mask = pops[pop]
        ours, truth = norm(parsed.loc[mask, pcol]), norm(df.loc[mask, tcol])
        ok = (ours == truth) | (ours.isna() & truth.isna())
        n, good = int(mask.sum()), int(ok.sum())
        rows.append((label, pop, n, n - good, 100.0 * good / max(n, 1)))
        bad = ok.index[~ok]
        if len(bad):
            detail += [f"## {label} — {len(bad)} disagreements", ""]
            for i in list(bad[:samples]):
                detail += [f"- ours=`{ours[i]}` nflverse=`{truth[i]}` — {df.at[i, 'desc']}"]
            detail.append("")
    good, n, bad = tackler_agreement(df, parsed, pops["scrimmage_clean"])
    rows.append(("tacklers (set)", "scrimmage_clean", n, n - good, 100.0 * good / max(n, 1)))
    if len(bad):
        detail += [f"## tacklers — {len(bad)} disagreements", ""]
        detail += [f"- ours=`{parsed.at[i, 'tacklers']}` — {df.at[i, 'desc']}" for i in list(bad[:samples])]
        detail.append("")

    head = f"{'field':<34}{'population':<17}{'n':>7}{'miss':>7}{'agree %':>10}"
    print(f"season {season}: {len(df):,} plays")
    print(head)
    print("-" * len(head))
    report += ["| field | population | n | miss | agree % |", "|---|---|---:|---:|---:|"]
    for label, pop, n, miss, pct in rows:
        print(f"{label:<34}{pop:<17}{n:>7}{miss:>7}{pct:>10.2f}")
        report.append(f"| {label} | {pop} | {n} | {miss} | {pct:.2f} |")
    out = report_path or ROOT / "data" / "parser_report.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(report + [NOTES, "# Disagreement samples", ""] + detail))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--season", type=int, default=2024)
    ap.add_argument("--samples", type=int, default=8)
    ap.add_argument("--path", type=Path, default=None)
    ap.add_argument("--prose-only", action="store_true",
                    help="do not give the parser posteam (it only affects yards on fumble plays)")
    ap.add_argument("--report", type=Path, default=None, help="default data/parser_report.md")
    a = ap.parse_args()
    raise SystemExit(run(a.season, a.samples, a.path, a.prose_only, a.report))
