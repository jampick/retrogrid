from __future__ import annotations

import pytest

from retroffb.providers.directory import NflversePlayerDirectory, derive_short

from conftest import needs_nflverse

pytestmark = needs_nflverse

KELCE = "00-0030506"


@pytest.fixture(scope="module")
def directory() -> NflversePlayerDirectory:
    return NflversePlayerDirectory.from_data_dir(week=15)


def test_player_by_id(directory: NflversePlayerDirectory) -> None:
    p = directory.player(KELCE)
    assert p is not None
    assert (p.name, p.short, p.position, p.team, p.number) == ("Travis Kelce", "T.Kelce", "TE", "KC", 87)
    assert p.headshot and p.headshot.startswith("https://")
    assert directory.player("nope") is None


def test_by_short_disambiguates_by_team(directory: NflversePlayerDirectory) -> None:
    buf = directory.by_short("J.Allen", "BUF")
    assert buf is not None and buf.name == "Josh Allen" and buf.position == "QB"
    assert directory.by_short("T.Kelce", "KC").id == KELCE  # type: ignore[union-attr]
    assert directory.by_short("T.Kelce", "BUF") is None
    assert directory.by_short("Z.Nobody") is None


def test_by_short_accepts_jersey_prefix(directory: NflversePlayerDirectory) -> None:
    p = directory.by_short("12-N.Collins", "HOU")
    assert p is not None and p.name == "Nico Collins"


def test_same_short_same_team_uses_jersey_or_position(directory: NflversePlayerDirectory) -> None:
    # Chris Jones (DE, 95) and Cam Jones (LB) are both "C.Jones" on KC.
    p = directory.by_short("95-C.Jones", "KC")
    assert p is not None and p.name == "Chris Jones"


def test_team_defence(directory: NflversePlayerDirectory) -> None:
    d = directory.player("DEF-KC")
    assert d is not None and (d.position, d.team, d.name) == ("DEF", "KC", "Kansas City")
    assert directory.by_short("KC DEF", "KC") == d
    assert directory.defence("SF").id == "DEF-SF"  # type: ignore[union-attr]


def test_derive_short() -> None:
    assert derive_short("Travis", "Kelce", "Travis Kelce") == "T.Kelce"
    assert derive_short(None, None, "Amon-Ra St. Brown") == "A.St. Brown"
