from __future__ import annotations

import asyncio

import pytest

from retroffb.sim.clock import SimClock

from conftest import FakeTime


def test_starts_paused_and_advances_with_speed() -> None:
    ft = FakeTime()
    c = SimClock(1000, speed=10, time_fn=ft)
    ft.advance(5)
    assert c.now() == 0 and not c.running
    c.start()
    ft.advance(5)
    assert c.now() == pytest.approx(50)


def test_speed_change_is_continuous() -> None:
    ft = FakeTime()
    c = SimClock(10_000, time_fn=ft)
    c.start()
    ft.advance(10)
    c.speed = 60
    assert c.now() == pytest.approx(10)
    ft.advance(2)
    assert c.now() == pytest.approx(130)
    with pytest.raises(ValueError):
        c.set_speed(0)


def test_pause_resume() -> None:
    ft = FakeTime()
    c = SimClock(1000, time_fn=ft)
    c.start()
    ft.advance(7)
    c.pause()
    ft.advance(100)
    assert c.now() == pytest.approx(7)
    c.resume()
    ft.advance(3)
    assert c.now() == pytest.approx(10)


def test_seek_clamps_and_bumps_epoch() -> None:
    ft = FakeTime()
    c = SimClock(100, time_fn=ft)
    c.seek(40)
    assert c.now() == 40 and c.epoch == 1
    c.seek(1e9)
    assert c.now() == 100 and c.finished
    c.seek(-5)
    assert c.now() == 0
    c.start()
    ft.advance(500)
    assert c.now() == 100


def test_sleep_until_real_time() -> None:
    async def run() -> None:
        c = SimClock(1000, speed=100)
        c.start()
        assert await asyncio.wait_for(c.sleep_until(5), 1.0) is True   # 50 ms real
        assert c.now() >= 5

    asyncio.run(run())


def test_sleep_until_wakes_on_speed_change() -> None:
    async def run() -> None:
        c = SimClock(100_000, speed=1)
        c.start()
        task = asyncio.create_task(c.sleep_until(600))   # 10 real minutes at 1x
        await asyncio.sleep(0.02)
        assert not task.done()
        c.speed = 60_000                                  # now ~10 ms
        assert await asyncio.wait_for(task, 1.0) is True

    asyncio.run(run())


def test_sleep_until_holds_while_paused() -> None:
    async def run() -> None:
        c = SimClock(1000, speed=1000)
        task = asyncio.create_task(c.sleep_until(10))
        await asyncio.sleep(0.05)
        assert not task.done()                            # never started
        c.start()
        await asyncio.sleep(0.002)
        c.pause()
        frozen = c.now()
        await asyncio.sleep(0.05)
        assert c.now() == frozen
        c.resume()
        assert await asyncio.wait_for(task, 1.0) is True

    asyncio.run(run())


def test_sleep_until_interrupted_by_seek() -> None:
    async def run() -> None:
        c = SimClock(1000)
        c.start()
        task = asyncio.create_task(c.sleep_until(500))
        await asyncio.sleep(0.01)
        c.seek(900)
        assert await asyncio.wait_for(task, 1.0) is False
        assert await c.sleep_until(100) is True           # already past

    asyncio.run(run())


def test_sleep_past_duration_waits_for_seek() -> None:
    async def run() -> None:
        c = SimClock(10, speed=10_000)
        c.start()
        task = asyncio.create_task(c.sleep_until(float("inf")))
        await asyncio.sleep(0.03)
        assert c.finished and not task.done()
        c.seek(0)
        assert await asyncio.wait_for(task, 1.0) is False

    asyncio.run(run())
