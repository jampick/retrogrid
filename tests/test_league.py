from __future__ import annotations

from collections import Counter

import pytest

from retrogrid.models import SLOTS
from retrogrid.providers.slate import PoolEntry, Slate, load_pool, load_slate
from retrogrid.providers.stub_league import (
    YAHOO_HALF_PPR, SyntheticLeagueProvider, approx_play_points, pa_tier_key,
)

from conftest import needs_slate

pytestmark = needs_slate


@pytest.fixture(scope="module")
def slate() -> Slate:
    return load_slate()


@pytest.fixture(scope="module")
def pool() -> list[PoolEntry]:
    return load_pool()


@pytest.fixture(scope="module")
def league(slate: Slate, pool: list[PoolEntry]) -> SyntheticLeagueProvider:
    return SyntheticLeagueProvider(seed=7, week=slate.week, pool=pool, plays=slate.plays)


def _dump(lg: SyntheticLeagueProvider, week: int) -> tuple:
    return (
        lg.league().to_dict(),
        [lg.roster(t.key, week).to_dict() for t in lg.league().teams],
        [m.to_dict() for m in lg.matchups(week)],
    )


def test_same_seed_same_league(slate: Slate, pool: list[PoolEntry]) -> None:
    a = SyntheticLeagueProvider(seed=7, week=slate.week, pool=pool, plays=slate.plays)
    b = SyntheticLeagueProvider(seed=7, week=slate.week, pool=pool, plays=slate.plays)
    c = SyntheticLeagueProvider(seed=8, week=slate.week, pool=pool, plays=slate.plays)
    assert _dump(a, slate.week) == _dump(b, slate.week)
    assert _dump(a, slate.week) != _dump(c, slate.week)


def test_league_shape(league: SyntheticLeagueProvider) -> None:
    lg = league.league()
    assert len(lg.teams) == 12
    assert lg.teams[0].key == "t01" and lg.teams[0].owner == "jampick"
    assert len({t.name for t in lg.teams}) == 12 and len({t.owner for t in lg.teams}) == 12
    assert all(t.name == t.name.upper() and t.owner == t.owner.lower() for t in lg.teams)
    assert lg.scoring_rules == YAHOO_HALF_PPR


def test_rosters_valid_and_unique(league: SyntheticLeagueProvider, slate: Slate, pool: list[PoolEntry]) -> None:
    by_id = {e.id: e for e in pool}
    seen: Counter[str] = Counter()
    for team in league.league().teams:
        roster = league.roster(team.key, slate.week)
        assert tuple(s.slot for s in roster.starters()) == SLOTS
        assert [s.slot for s in roster.slots].count("BN") == 5
        for s in roster.slots:
            seen[s.player_id] += 1
            entry = by_id[s.player_id]
            assert entry.team in slate.teams
            if s.slot == "FLEX":
                assert entry.position in ("RB", "WR", "TE")
            elif s.slot != "BN":
                assert entry.position == s.slot
            if s.slot == "DEF":
                assert s.player_id == f"DEF-{entry.team}"
            if s.slot != "BN":
                assert entry.active
    assert max(seen.values()) == 1


def test_matchups(league: SyntheticLeagueProvider, slate: Slate) -> None:
    ms = league.matchups(slate.week)
    assert len(ms) == 6
    assert sorted(k for m in ms for k in (m.a, m.b)) == [f"t{i:02d}" for i in range(1, 13)]
    mine = league.matchup("t01", slate.week)
    rival = mine.b if mine.a == "t01" else mine.a
    gap = abs(league.projected_final("t01") - league.projected_final(rival))
    others = [t.key for t in league.league().teams if t.key != "t01"]
    nearest = min(abs(league.projected_final("t01") - league.projected_final(k)) for k in others)
    assert gap <= max(10.0, nearest)
    assert league.matchup(rival, slate.week) == mine
    assert league.official_points("t01", slate.week) == {}


def test_pa_tiers() -> None:
    assert [pa_tier_key(n) for n in (0, 3, 7, 14, 27, 28, 35, 50)] == [
        "def_pa_0", "def_pa_1_6", "def_pa_7_13", "def_pa_14_20",
        "def_pa_21_27", "def_pa_28_34", "def_pa_35", "def_pa_35",
    ]
    assert all(pa_tier_key(n) in YAHOO_HALF_PPR for n in range(0, 80))


def test_approx_points_on_a_passing_td(slate: Slate) -> None:
    play = next(p for p in slate.plays if p.play_type == "pass" and p.touchdown and p.complete
                and p.td_team == p.posteam and not p.fumble_lost)
    pts = approx_play_points(play)
    assert pts[play.passer_id] == pytest.approx(4 + 0.04 * play.yards_gained)  # type: ignore[index]
    assert pts[play.receiver_id] == pytest.approx(6.5 + 0.1 * play.yards_gained)  # type: ignore[index]
