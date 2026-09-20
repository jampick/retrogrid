"""Play grammar (DESIGN §8): pure, seeded, never crashes."""
import pytest

from retrogrid.grammar import compile_play
from retrogrid.models import PlayRow
from retrogrid.providers.slate import load_slate, slate_available


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


@pytest.mark.skipif(not slate_available(), reason="no slate data")
def test_kicks_have_a_fate_and_the_whistle_stops_everyone():
    fates = set()
    for p in load_slate().plays:
        if p.play_type not in ("punt", "kickoff"):
            continue
        c = compile_play(p)
        if "/flag" in c.template:                            # no-play penalty on the kick
            continue
        fates.add(c.template)
        assert all(abs(a.end_t - c.duration) < 1e-6 for a in c.actors), c.template
    assert fates <= {f"{t}/{f}" for t in ("punt", "kickoff")
                     for f in ("return", "touchback", "fair catch", "oob", "downed", "dead", "blocked")}
    assert {"punt/return", "punt/fair catch", "kickoff/return", "kickoff/touchback"} <= fates


@pytest.mark.skipif(not slate_available(), reason="no slate data")
def test_short_targets_are_not_dragged_across_the_field():
    for p in load_slate().plays:
        if p.play_type != "pass" or p.sack or p.qb_scramble or not p.complete or p.interception:
            continue
        c = compile_play(p)
        tgt = next((a for a in c.actors if a.involved and a.team == "off" and a.role != "QB"), None)
        ball = next(a for a in c.actors if a.team == "ball")
        if tgt is None or (p.air_yards or 0) >= 15 or tgt.role in ("RB", "FB"):
            continue
        apex = max(ball.keys, key=lambda k: k.z)
        catch_x = next(k.x for k in ball.keys if k.t > apex.t and k.z <= 0.61)
        assert abs(catch_x - tgt.start[0]) <= 5.0 + 0.7 * 15 + 0.1, c.template


@pytest.mark.skipif(not slate_available(), reason="no slate data")
def test_out_of_bounds_plays_finish_on_the_sideline():
    hits = 0
    for p in load_slate().plays:
        if p.play_type in ("run", "pass") and not p.sack and not p.interception \
                and ("pushed ob" in p.desc or "ran ob" in p.desc) and (p.complete or p.play_type == "run") \
                and "no play" not in p.desc.lower() and "FUMBLES" not in p.desc:
            c = compile_play(p)
            ball = next(a for a in c.actors if a.role == "BALL")
            x = ball.at(c.duration)[0]
            assert x < 1.0 or x > 53.333 - 1.0, p.desc
            assert c.duration < 14, p.desc
            hits += 1
    assert hits > 50


@pytest.mark.skipif(not slate_available(), reason="no slate data")
def test_flags_replay_the_wiped_play_anonymously_or_never_snap():
    called_back = dead = 0
    for p in load_slate().plays:
        if p.play_type != "no_play":
            continue
        c = compile_play(p)
        flag = next(a for a in c.actors if a.team == "flag")
        assert flag.keys[-1].t <= c.duration
        assert all(a.player_id is None for a in c.actors), p.desc       # it never counted: no name plates
        ball = next(a for a in c.actors if a.role == "BALL")
        if c.template.endswith("/called back"):
            called_back += 1
        else:
            dead += 1
            assert ball.at(0) == ball.at(c.duration), p.desc             # nobody snapped it
    assert called_back > 30 and dead > 30
