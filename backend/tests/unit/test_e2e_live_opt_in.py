"""The live e2e suite runs only on an explicit opt-in, never on reachability alone.

backend/tests/e2e/ mutates whatever ECM answers on localhost:6100, which on a
developer host is the operator's live instance. Reachability is therefore not
consent: the suite must skip unless ECM_E2E_LIVE=1 is set as well.

These tests load the e2e conftest as a plain module with every httpx entry
point replaced, so nothing here can open a connection.
"""
import importlib.util
from pathlib import Path

import httpx
import pytest


CONFTEST = Path(__file__).resolve().parents[1] / "e2e" / "conftest.py"


def _no_network(*_args, **_kwargs):
    raise AssertionError("the e2e conftest must not touch the network here")


@pytest.fixture
def conftest(monkeypatch):
    for name in ("get", "post", "request", "Client"):
        monkeypatch.setattr(httpx, name, _no_network)
    monkeypatch.delenv("ECM_E2E_LIVE", raising=False)
    spec = importlib.util.spec_from_file_location("e2e_conftest_under_test", CONFTEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeItem:
    def __init__(self, path):
        self.path = path
        self.markers = []

    def add_marker(self, marker):
        self.markers.append(marker)


def _items(*relative):
    return [FakeItem(CONFTEST.parent / name) for name in relative]


def _probe(monkeypatch, conftest, reachable):
    calls = []

    def probe():
        calls.append(1)
        return reachable

    monkeypatch.setattr(conftest, "_container_reachable", probe)
    return calls


def _skip_reasons(item):
    return [marker.kwargs["reason"] for marker in item.markers if marker.name == "skip"]


def test_importing_the_conftest_does_not_probe_the_network(conftest):
    assert conftest.BASE_URL == "http://localhost:6100"


@pytest.mark.parametrize("value", [None, "", "0", "true", "yes"])
def test_skipped_without_opt_in_even_when_the_probe_would_succeed(conftest, monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("ECM_E2E_LIVE", value)
    probes = _probe(monkeypatch, conftest, reachable=True)
    items = _items("test_channels.py", "test_event_sync.py")
    conftest.pytest_collection_modifyitems(config=None, items=items)
    for item in items:
        [reason] = _skip_reasons(item)
        assert "ECM_E2E_LIVE=1" in reason
    assert probes == [], "without the opt-in the live instance must not even be probed"


def test_opted_in_and_reachable_runs(conftest, monkeypatch):
    monkeypatch.setenv("ECM_E2E_LIVE", "1")
    probes = _probe(monkeypatch, conftest, reachable=True)
    items = _items("test_channels.py", "test_m3u.py")
    conftest.pytest_collection_modifyitems(config=None, items=items)
    assert all(item.markers == [] for item in items)
    assert len(probes) == 1


def test_opted_in_but_unreachable_skips_with_the_reachability_reason(conftest, monkeypatch):
    monkeypatch.setenv("ECM_E2E_LIVE", "1")
    _probe(monkeypatch, conftest, reachable=False)
    [item] = _items("test_channels.py")
    conftest.pytest_collection_modifyitems(config=None, items=[item])
    [reason] = _skip_reasons(item)
    assert "not reachable" in reason


def test_tests_outside_the_e2e_directory_are_left_alone(conftest, monkeypatch):
    probes = _probe(monkeypatch, conftest, reachable=True)
    outside = FakeItem(CONFTEST.parents[1] / "unit" / "test_something.py")
    conftest.pytest_collection_modifyitems(config=None, items=[outside])
    assert outside.markers == []
    assert probes == []
