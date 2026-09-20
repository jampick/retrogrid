"""Yahoo league adapter, replayed from raw-shaped fixtures (no network)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from retrogrid.providers.directory import NflversePlayerDirectory, name_key
from retrogrid.providers.yahoo import YahooLeagueProvider, list_leagues, scoring_rules, team_abbr
from retrogrid.providers.yahoo_api import YahooClient, YahooError, flatten, many
from retrogrid.scoring import LeagueIndex, MatchupBoard, ScoringState
from retrogrid.scoring.engine import DEFAULT_RULES

from conftest import needs_nflverse

FIXTURES = Path(__file__).parent / "fixtures" / "yahoo"
LK, WEEK, SEASON = "461.l.777", 2, 2026


@pytest.fixture(scope="module")
def client() -> YahooClient:
    return YahooClient(raw_dir=FIXTURES, offline=True)


# ── flattening ────────────────────────────────────────────────────────────

def test_flatten_collections_records_and_wrappers():
    raw = {"teams": {"0": {"team": [[{"team_key": "t.1"}, [], {"name": "A"}], {"roster": {"week": "2", "0": {"players": {"count": 0}}}}]},
                     "1": {"team": [[{"team_key": "t.2"}]]}, "count": 2}}
    teams = flatten(raw)["teams"]
    assert [t["team_key"] for t in teams] == ["t.1", "t.2"]
    assert teams[0]["name"] == "A"
    assert teams[0]["roster"] == {"week": "2", "players": []}


def test_flatten_same_key_envelopes_become_a_list():
    assert flatten({"stats": [{"stat": {"stat_id": 4}}, {"stat": {"stat_id": 5}}]}) == {"stats": [{"stat_id": 4}, {"stat_id": 5}]}


def test_many_reads_every_collapsed_shape():
    assert many([{"stat_id": 4}, {"stat_id": 5}], "stat") == [{"stat_id": 4}, {"stat_id": 5}]
    assert many({"manager": {"nickname": "a"}}, "manager") == [{"nickname": "a"}]      # list of one, collapsed
    assert many({"nickname": "a"}, "manager") == [{"nickname": "a"}]
    assert many(None, "x") == [] and many([], "x") == []


def test_replay_misses_are_loud(client: YahooClient):
    with pytest.raises(YahooError):
        client.get("league/nope/settings")


def test_name_key_folds_suffixes_and_punctuation():
    assert name_key("D.J. Moore") == name_key("DJ Moore")
    assert name_key("Kenneth Walker III") == name_key("Kenneth Walker")
    assert name_key("Amon-Ra St. Brown") == name_key("Amon Ra St Brown")


def test_team_abbr():
    assert [team_abbr(a) for a in ("Was", "LAR", "Jax", "JAC", "KC", None)] == ["WAS", "LA", "JAX", "JAX", "KC", ""]


# ── league discovery + scoring ────────────────────────────────────────────

def test_list_leagues_discovers_the_game_key(client: YahooClient):
    assert list_leagues(client, SEASON) == [(LK, "Neon Sunday")]


def test_scoring_rules_cover_the_engine_vocabulary(client: YahooClient):
    rules, unmapped = scoring_rules(client.get(f"league/{LK}/settings")["league"]["settings"])
    assert rules == {**DEFAULT_RULES, "off_fum_ret_td": 6}
    notes = " | ".join(unmapped)
    assert "stat 48 (Ret Yds)" in notes and "stat 82 (XPR)" in notes      # unmodelled stats are reported, not dropped
    assert "bonuses" in notes


def test_conflicting_fg_tiers_are_reported():
    settings = {"stat_modifiers": {"stats": [{"stat_id": 19, "value": "3"}, {"stat_id": 21, "value": "3.5"}]}}
    rules, unmapped = scoring_rules(settings)
    assert rules == {"fg_0_39": 3.0} and "folded into fg_0_39" in unmapped[0]


# ── the provider ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def league(client: YahooClient) -> YahooLeagueProvider:
    directory = NflversePlayerDirectory.from_data_dir(week=WEEK, season=SEASON)
    return YahooLeagueProvider(client, directory, week=WEEK, season=SEASON)


@needs_nflverse
class TestProvider:
    def test_league_and_viewer(self, league: YahooLeagueProvider):
        lg = league.league()
        assert (lg.key, lg.name, len(lg.teams)) == (LK, "Neon Sunday", 4)
        assert lg.teams[0].name == "Gridiron Ghosts" and lg.teams[0].owner == "jampick"
        assert league.viewer == f"{LK}.t.1"

    def test_roster_slots(self, league: YahooLeagueProvider):
        r = league.roster(f"{LK}.t.1", WEEK)
        assert [s.slot for s in r.slots] == ["QB", "RB", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN"]   # IR sits
        assert r.slots[0].player_id == "00-0034857"           # Josh Allen, by yahoo_id
        assert r.slots[6].player_id == "DEF-WAS"
        assert len(r.starters()) == 7

    def test_join_falls_back_to_name(self, league: YahooLeagueProvider):
        ids = [s.player_id for s in league.roster(f"{LK}.t.2", WEEK).slots]
        assert ids[:3] == ["00-0040691", "00-0040122", "00-0034827"]      # Dart, Jeanty, "D.J. Moore": no yahoo_id
        assert ids[5] == "00-0041182" and ids[6] == "DEF-LA"
        assert ids[7] == "yahoo:49999"
        assert [m.split(" (")[0] for m in league.join.report.unmatched] == ["Nobody Atall"]
        assert league.join.report.by_name == 4

    def test_matchups_and_official_points(self, league: YahooLeagueProvider):
        assert [(m.a, m.b) for m in league.matchups(WEEK)] == [(f"{LK}.t.1", f"{LK}.t.2"), (f"{LK}.t.3", f"{LK}.t.4")]
        assert league.matchup(f"{LK}.t.4", WEEK).a == f"{LK}.t.3"
        pts = league.official_points(f"{LK}.t.1", WEEK)
        assert pts["00-0034857"] == 24.5 and pts["00-0039139"] == 18.2
        assert league.official_total(f"{LK}.t.1") == 42.7

    def test_downstream_is_untouched(self, league: YahooLeagueProvider):
        index = LeagueIndex.from_provider(league, WEEK)
        assert index.side(f"{LK}.t.1", "00-0034857") == "you"
        assert index.side(f"{LK}.t.1", "DEF-LA") == "them"
        assert index.side(f"{LK}.t.1", "00-0036322") is None or index.side(f"{LK}.t.1", "00-0036322") == "league"
        state = ScoringState(league.league().scoring_rules)
        state.set_base({"00-0034857": 24.5})                  # Thursday's QB on a Sunday slate
        board = MatchupBoard.for_viewer(league, f"{LK}.t.1", WEEK, state)
        assert board.totals() == (24.5, 0.0)
        state.reset()
        assert board.totals() == (24.5, 0.0)                  # a sim seek does not lose official points
        assert [r.slot for r in board.rows()] == ["QB", "RB", "WR", "TE", "FLEX", "K", "DEF"]     # the league's shape, not ours


def test_fixture_files_are_raw_shaped():
    raw = json.loads((FIXTURES / f"league_{LK}_scoreboard_week_2.json").read_text())
    assert "count" in raw["fantasy_content"]["league"][1]["scoreboard"]["0"]["matchups"]
