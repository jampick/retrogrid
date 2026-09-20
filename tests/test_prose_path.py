"""The simulated live path: prose alone must score the slate like nflverse does."""
import pytest

from retroffb.providers.directory import NflversePlayerDirectory
from retroffb.providers.prose import reparse
from retroffb.providers.slate import load_slate, slate_available
from retroffb.providers.stub_league import SyntheticLeagueProvider
from retroffb.scoring import ScoringState


@pytest.mark.skipif(not slate_available(), reason="no slate data")
def test_prose_only_scoring_matches_nflverse_columns():
    slate = load_slate()
    d = NflversePlayerDirectory.from_data_dir(week=slate.week, season=slate.season)
    rules = SyntheticLeagueProvider.from_slate(seed=1, directory=d).league().scoring_rules
    truth, live = ScoringState(rules), ScoringState(rules)
    for g in slate.games:
        truth.open_game(g), live.open_game(g)
    for p in slate.plays:
        q = reparse(p, d)
        assert q.air_yards is None                          # a live feed never has it
        truth.apply(p), live.apply(q)
    a, b = truth.all_points(), live.all_points()
    wrong = {k: (a.get(k, 0), b.get(k, 0)) for k in set(a) | set(b) if abs(a.get(k, 0) - b.get(k, 0)) > 0.05}
    assert len(wrong) <= 2, wrong                           # namesakes are the failure mode; keep it near zero
