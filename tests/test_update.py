"""The release check: version compare, the install-specific hint, and failing quietly."""
import asyncio

import httpx
import pytest

from retrogrid import update


@pytest.mark.parametrize("latest,installed,expect", [
    ("0.1.1", "0.1.0", True),
    ("0.2.0", "0.1.9", True),
    ("1.0.0", "0.9.9", True),
    ("0.1.0", "0.1.0", False),
    ("0.1.0", "0.1.1", False),
    ("0.1.1", "0.1.1.dev3", False),    # a dev build of 0.1.1 is not behind 0.1.1
    ("", "0.1.0", False),
])
def test_newer(latest, installed, expect):
    assert update.newer(latest, installed) is expect


def test_hint_follows_install_location(monkeypatch):
    monkeypatch.setattr(update, "__file__", "/usr/lib/python3.14/site-packages/retrogrid/update.py")
    assert update.hint() == "omarchy-update"
    monkeypatch.setattr(update, "__file__", "/home/x/.venv/lib/python3.14/site-packages/retrogrid/update.py")
    assert update.hint() == "pip install -U retrogrid"


def _fresh(monkeypatch):
    monkeypatch.setattr(update, "_result", None)
    monkeypatch.setattr(update, "_done", False)
    monkeypatch.delenv("RETROGRID_NO_UPDATE_CHECK", raising=False)


def _serve(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))


def test_check_reports_newer(monkeypatch):
    _fresh(monkeypatch)
    monkeypatch.setattr(update, "current", lambda: "0.1.0")
    _serve(monkeypatch, lambda req: httpx.Response(200, json={"tag_name": "v0.2.0"}))
    assert asyncio.run(update.check()) == {"latest": "0.2.0", "current": "0.1.0", "hint": update.hint()}
    assert update.result()["latest"] == "0.2.0"


def test_check_quiet_when_current(monkeypatch):
    _fresh(monkeypatch)
    monkeypatch.setattr(update, "current", lambda: "0.2.0")
    _serve(monkeypatch, lambda req: httpx.Response(200, json={"tag_name": "v0.2.0"}))
    assert asyncio.run(update.check()) is None


def test_check_swallows_network_errors(monkeypatch):
    _fresh(monkeypatch)
    def boom(req):
        raise httpx.ConnectError("offline")
    _serve(monkeypatch, boom)
    assert asyncio.run(update.check()) is None
    assert update.result() is None


def test_check_runs_once(monkeypatch):
    _fresh(monkeypatch)
    calls = []
    monkeypatch.setattr(update, "current", lambda: "0.1.0")
    _serve(monkeypatch, lambda req: calls.append(1) or httpx.Response(200, json={"tag_name": "v0.3.0"}))
    asyncio.run(update.check())
    asyncio.run(update.check())
    assert calls == [1]


def test_check_can_be_disabled(monkeypatch):
    _fresh(monkeypatch)
    monkeypatch.setenv("RETROGRID_NO_UPDATE_CHECK", "1")
    def boom(req):
        raise AssertionError("must not hit the network")
    _serve(monkeypatch, boom)
    assert asyncio.run(update.check()) is None
