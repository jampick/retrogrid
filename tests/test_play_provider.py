from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

from retrogrid.models import PlayRow
from retrogrid.providers.nflverse_plays import SlatePlayProvider
from retrogrid.providers.slate import Slate, load_slate
from retrogrid.sim.clock import SimClock

from conftest import needs_slate

pytestmark = needs_slate


@pytest.fixture(scope="module")
def slate() -> Slate:
    return load_slate()


def _provider(slate: Slate, speed: float = 1.0) -> SlatePlayProvider:
    return SlatePlayProvider(slate, SimClock(slate.duration, speed=speed))


async def _take(stream: AsyncIterator[PlayRow], n: int) -> list[PlayRow]:
    out: list[PlayRow] = []
    async for play in stream:
        out.append(play)
        if len(out) == n:
            break
    return out


def test_slate_shape(slate: Slate) -> None:
    assert len(slate.games) >= 10
    assert min(g.kickoff for g in slate.games) == 0.0
    ids = [p.play_id for p in slate.plays]
    assert len(ids) == len(set(ids))
    assert all(p.desc and p.play_type for p in slate.plays)
    assert all(0 <= p.sim_time <= slate.duration for p in slate.plays)


def test_monotonic_within_each_game(slate: Slate) -> None:
    p = _provider(slate)
    for g in slate.games:
        plays = p.game_plays(g.id)
        assert [x.seq for x in plays] == list(range(len(plays)))
        times = [x.sim_time for x in plays]
        assert times == sorted(times)
        assert times[0] == pytest.approx(g.kickoff)
        home = [x.home_score for x in plays]
        assert home == sorted(home)


def test_plays_until_and_between(slate: Slate) -> None:
    p = _provider(slate)
    everything = p.all_plays()
    assert p.plays_until(0) == []
    assert p.plays_until(slate.duration) == everything
    t0, t1 = 3000.0, 4000.0
    assert p.plays_until(t0) + p.plays_between(t0, t1) == p.plays_until(t1)
    assert all(t0 < x.sim_time <= t1 for x in p.plays_between(t0, t1))


def test_stream_arrives_in_order_and_interleaved(slate: Slate) -> None:
    async def run() -> list[PlayRow]:
        p = _provider(slate, speed=20_000)
        stream = p.stream_plays()
        p.clock.start()
        return await asyncio.wait_for(_take(stream, 300), 10.0)

    got = asyncio.run(run())
    assert got == _provider(slate).all_plays()[:300]
    times = [x.sim_time for x in got]
    assert times == sorted(times)
    assert len({x.game_id for x in got}) > 3
    switches = sum(a.game_id != b.game_id for a, b in zip(got, got[1:]))
    assert switches > 100


def test_stream_follows_seek(slate: Slate) -> None:
    async def run() -> tuple[list[PlayRow], list[PlayRow]]:
        p = _provider(slate, speed=20_000)
        stream = p.stream_plays()
        p.clock.start()
        first = await asyncio.wait_for(_take(stream, 5), 5.0)
        p.clock.pause()
        p.clock.seek(7200)
        p.clock.resume()
        later = await asyncio.wait_for(_take(stream, 5), 5.0)
        return first, later

    first, later = asyncio.run(run())
    assert first[0].sim_time == 0.0
    assert later == _provider(slate).plays_between(7200, slate.duration)[:5]


def test_live_games_track_the_clock(slate: Slate) -> None:
    p = _provider(slate)
    assert all(g.status == "pre" and g.home_score == 0 for g in p.live_games())

    p.clock.seek(1800)
    statuses = {g.status for g in p.live_games()}
    assert statuses == {"live", "pre"}
    live = next(g for g in p.live_games() if g.status == "live")
    assert live.quarter >= 1 and live.possession in (live.home, live.away)

    assert any(g.status == "half" for t in range(4800, 7200, 120) for g in p.games_at(t))

    p.clock.seek(slate.duration)
    for g in p.live_games():
        assert g.status == "final"
        assert (g.home_score, g.away_score) == slate.final_scores[g.id]
    assert slate.games[0].status == "pre"            # base objects untouched
