#!/usr/bin/env python
"""Freeze the SIM SUNDAY demo into the package (backend/retrogrid/bundled/), so
an install runs with no downloads: the slate, a player-directory snapshot, and
the sprites the slate can show. Re-run after rebuilding the slate or sprites.

    retrogrid fetch && retrogrid build-slate && retrogrid sprites --all
    .venv/bin/python scripts/make_bundle.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from retrogrid import paths  # noqa: E402
from retrogrid.providers.directory import TEAM_NAMES, NflversePlayerDirectory  # noqa: E402
from retrogrid.providers.slate import load_pool, load_slate  # noqa: E402
from retrogrid.tools.build_sprites import defence_sprite  # noqa: E402


def main() -> int:
    src = paths.DATA / "slate"
    slate = load_slate(src)
    shutil.rmtree(paths.BUNDLED, ignore_errors=True)
    shutil.copytree(src, paths.BUNDLED_SLATE)

    directory = NflversePlayerDirectory.from_data_dir(week=slate.week, season=slate.season)
    paths.BUNDLED_DIRECTORY.write_text(json.dumps(directory.snapshot(), separators=(",", ":"), ensure_ascii=False))

    ids = {e.id for e in load_pool(src)}
    for p in slate.plays:
        ids |= {i for i in (p.passer_id, p.receiver_id, p.rusher_id, p.kicker_id) if i}
    paths.BUNDLED_SPRITES.mkdir(parents=True)
    n = 0
    for i in sorted(ids):
        if (f := paths.SPRITES / f"{i}.png").is_file():
            shutil.copy2(f, paths.BUNDLED_SPRITES / f.name)
            n += 1
    for team in TEAM_NAMES:                                # shields are synthetic: ship all 32
        defence_sprite(team).save(paths.BUNDLED_SPRITES / f"DEF-{team}.png")
    size = sum(f.stat().st_size for f in paths.BUNDLED.rglob("*") if f.is_file()) / 1e6
    print(f"bundle: {slate.season} wk {slate.week}, {len(slate.plays)} plays, {len(directory)} players, "
          f"{n}/{len(ids)} sprites, {size:.1f} MB -> {paths.BUNDLED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
