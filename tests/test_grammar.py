"""Play grammar (DESIGN §8): pure, seeded, never crashes."""
import pytest

from retroffb.grammar import compile_play
from retroffb.models import PlayRow
from retroffb.providers.slate import load_slate, slate_available


def _pass(**kw):
    base = dict(play_id="g:1", game_id="2025_15_LAC_KC", seq=1, sim_time=0, quarter=1, clock="10:00", down=2, ydstogo=7,
                yardline_100=55, posteam="KC", defteam="LAC", desc="pass short right", play_type="pass", yards_gained=9,
                shotgun=True, pass_length="short", pass_location="right", air_yards=6, complete=True,
                passer_id="QB1", receiver_id="WR1", tackler_ids=["DB1"])
    base.update(kw)
    return PlayRow(**base)


def test_deterministic():
    a, b = compile_play(_pass()), compile_play(_pass())
    assert [(k.t, k.x, k.y) for x in a.actors for k in x.keys] == [(k.t, k.x, k.y) for x in b.actors for k in x.keys]


def test_asymmetric_fidelity_three_believable_entities():
    c = compile_play(_pass())
    assert len([a for a in c.actors if a.team in ("off", "def")]) == 22
    assert {a.player_id for a in c.actors if a.involved} == {"QB1", "WR1", "DB1"}
    ball = next(a for a in c.actors if a.team == "ball")
    target = next(a for a in c.actors if a.player_id == "WR1")
    assert abs(ball.keys[-1].y - (55 + 9)) < 0.6            # ball ends where the gain says
    assert abs(target.keys[-1].y - ball.keys[-1].y) < 0.6
    assert max(k.z for k in ball.keys) > 1                  # it was thrown, not carried


def test_degrades_instead_of_crashing():
    junk = _pass(play_type="???", yardline_100=None, down=None, ydstogo=None, pass_location=None, air_yards=None)
    assert compile_play(junk).actors


@pytest.mark.skipif(not slate_available(), reason="no slate data")
def test_whole_slate_compiles_with_sane_timelines():
    fallbacks = 0
    for p in load_slate().plays:
        c = compile_play(p)
        fallbacks += c.template == "fallback"
        assert 1.0 <= c.duration <= 25
        for a in c.actors:
            ts = [k.t for k in a.keys]
            assert ts == sorted(ts) and all(0 <= k.x <= 53.4 for k in a.keys)
    assert fallbacks == 0
