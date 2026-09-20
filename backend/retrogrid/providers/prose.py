"""The simulated *live* path (DESIGN §7): a play as ESPN would hand it over.

ESPN gives identity, situation, the score, yards and a sentence. Everything
else — who threw it, who caught it, where, what happened — must come out of
the prose. `reparse` throws away the nflverse columns a live feed would not
have and rebuilds the row through the desc parser + PlayerDirectory, so SIM
SUNDAY can rehearse Phase 8 before any credentials exist.
"""
from __future__ import annotations

import re
from dataclasses import fields

from ..models import PlayRow
from ..parser.desc import apply_to_row, parse_desc
from .directory import NflversePlayerDirectory

# What a live feed really provides. (air_yards is never in it: the prior fills in.)
LIVE_FIELDS = ("play_id", "game_id", "seq", "sim_time", "quarter", "clock", "down", "ydstogo", "yardline_100",
               "posteam", "defteam", "desc", "yards_gained", "home_score", "away_score")
_ID_OF = {"passer_name": "passer_id", "receiver_name": "receiver_id", "rusher_name": "rusher_id",
          "kicker_name": "kicker_id", "interceptor_name": "interceptor_id", "returner_name": "returner_id",
          "td_player_name": "td_player_id", "fumbler_name": "fumbler_id"}
_OFFENCE = {"passer_name", "receiver_name", "rusher_name", "kicker_name", "fumbler_name"}
_ROW_FIELDS = {f.name for f in fields(PlayRow)}


def _with_jersey(short: str, desc: str) -> str:
    """The parser hands back "T.Etienne"; the sentence still knows he wears 1."""
    m = re.search(rf"(\d{{1,2}})-{re.escape(short)}(?![\w.])", desc)
    return f"{m.group(1)}-{short}" if m else short


def reparse(play: PlayRow, directory: NflversePlayerDirectory) -> PlayRow:
    base = {k: getattr(play, k) for k in LIVE_FIELDS}
    parsed = parse_desc(play.desc, posteam=play.posteam)
    if parsed.play_type is None:                            # administrative line: nothing to recover
        return play
    row, names = apply_to_row(parsed, base)
    # nflverse convention: on a kickoff posteam is the *receiving* team
    kicking = play.defteam if parsed.play_type == "kickoff" else play.posteam
    receiving = play.posteam if parsed.play_type == "kickoff" else play.defteam
    resolved: dict[str, str | None] = {}
    for key, field_name in _ID_OF.items():
        short = names.get(key)
        if not short:
            continue
        team = kicking if key == "kicker_name" else play.posteam if key in _OFFENCE else None
        if key == "returner_name":
            team = receiving
        elif key == "interceptor_name":
            team = play.defteam
        full = _with_jersey(short, play.desc)
        pl = directory.by_short(full, team) or (directory.by_short(full, play.defteam if team == play.posteam else play.posteam)
                                               if team else None) or directory.by_short(full)
        row[field_name] = pl.id if pl else None
        resolved[short] = row[field_name]
    if names.get("td_player_name") in resolved and names["td_player_name"]:     # the scorer is someone already named
        first = next((row[f] for k, f in _ID_OF.items() if k != "td_player_name" and names.get(k) == names["td_player_name"] and row.get(f)), None)
        row["td_player_id"] = first or row.get("td_player_id")
    tacklers = []
    for short in names.get("tackler_names", []):
        full = _with_jersey(short, play.desc)
        pl = directory.by_short(full, receiving if parsed.play_type in ("pass", "run") else None) or directory.by_short(full)
        if pl:
            tacklers.append(pl.id)
    row["tackler_ids"] = tacklers
    return PlayRow(**{k: v for k, v in row.items() if k in _ROW_FIELDS})
