"""The shipped SIM SUNDAY bundle: an install must run on it alone."""
from retrogrid import paths
from retrogrid.providers.directory import NflversePlayerDirectory
from retrogrid.providers.slate import load_pool, load_slate, slate_available


def test_bundle_is_complete():
    assert slate_available(paths.BUNDLED_SLATE)
    slate = load_slate(paths.BUNDLED_SLATE)
    directory = NflversePlayerDirectory.from_snapshot(paths.BUNDLED_DIRECTORY)
    assert len(slate.games) >= 10 and len(slate.plays) > 1000
    ids = {i for p in slate.plays for i in (p.passer_id, p.receiver_id, p.rusher_id) if i}
    assert ids and all(directory.player(i) for i in ids)
    assert all(directory.player(e.id) for e in load_pool(paths.BUNDLED_SLATE))
    assert any(paths.BUNDLED_SPRITES.glob("*.png"))


def test_snapshot_round_trips(tmp_path):
    import json
    a = NflversePlayerDirectory.from_snapshot(paths.BUNDLED_DIRECTORY)
    f = tmp_path / "d.json"
    f.write_text(json.dumps(a.snapshot()))
    b = NflversePlayerDirectory.from_snapshot(f)
    assert a.snapshot() == b.snapshot()
    kc = a.defence("KC")
    assert kc and kc.position == "DEF"
    some = next(p for p in a._players.values() if p.position == "QB" and p.team)
    assert b.by_short(some.short, some.team).id == a.by_short(some.short, some.team).id
