"""The live e2e suite runs only on an explicit opt-in, never on reachability alone.

backend/tests/e2e/ mutates whatever ECM answers on localhost:6100, which on a
developer host is the operator's live instance. Reachability is therefore not
consent: unless ECM_E2E_LIVE=1 is set, the suite must be neither imported nor
run, and that must not depend on the skipping plugin (-p no:skipping turns skip
markers into passes).

The first half loads the e2e conftest as a plain module with every httpx entry
point replaced. The second half runs real pytest in a throwaway tree holding a
copy of the conftest, dummy tests and an httpx stub that records any use, so
nothing here can open a connection.
"""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

import httpx
import pytest


CONFTEST = Path(__file__).resolve().parents[1] / "e2e" / "conftest.py"
PYTEST_INI = Path(__file__).resolve().parents[2] / "pytest.ini"


def _no_network(*_args, **_kwargs):
    raise AssertionError("the e2e conftest must not touch the network here")


def _load(monkeypatch, live=None):
    for name in ("get", "post", "request", "Client"):
        monkeypatch.setattr(httpx, name, _no_network)
    if live is None:
        monkeypatch.delenv("ECM_E2E_LIVE", raising=False)
    else:
        monkeypatch.setenv("ECM_E2E_LIVE", live)
    spec = importlib.util.spec_from_file_location("e2e_conftest_under_test", CONFTEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def conftest(monkeypatch):
    return _load(monkeypatch)


class FakeItem:
    def __init__(self, path):
        self.path = path
        self.markers = []

    def add_marker(self, marker):
        self.markers.append(marker)


class FakeConfig:
    def __init__(self):
        self.deselected = []
        self.hook = self

    def pytest_deselected(self, items):
        self.deselected.extend(items)


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
def test_without_opt_in_nothing_below_the_directory_is_collected(monkeypatch, value):
    module = _load(monkeypatch, live=value)
    assert module.collect_ignore_glob == ["*"]


def test_with_opt_in_collection_is_not_suppressed(monkeypatch):
    module = _load(monkeypatch, live="1")
    assert not getattr(module, "collect_ignore_glob", [])


@pytest.mark.parametrize("value", [None, "", "0", "true", "yes"])
def test_named_e2e_items_are_deselected_without_opt_in(monkeypatch, value):
    module = _load(monkeypatch, live=value)
    probes = _probe(monkeypatch, module, reachable=True)
    ours = _items("test_channels.py", "sub/test_event_sync.py")
    outside = FakeItem(CONFTEST.parents[1] / "unit" / "test_something.py")
    items = [*ours, outside]
    config = FakeConfig()
    module.pytest_collection_modifyitems(config=config, items=items)
    assert items == [outside]
    assert config.deselected == ours
    assert all(item.markers == [] for item in ours), "deselection must not rely on skip markers"
    assert probes == [], "without the opt-in the live instance must not even be probed"


def test_opted_in_and_reachable_runs(monkeypatch):
    module = _load(monkeypatch, live="1")
    probes = _probe(monkeypatch, module, reachable=True)
    items = _items("test_channels.py", "test_m3u.py")
    config = FakeConfig()
    module.pytest_collection_modifyitems(config=config, items=items)
    assert len(items) == 2 and all(item.markers == [] for item in items)
    assert config.deselected == []
    assert len(probes) == 1


def test_opted_in_but_unreachable_skips_with_the_reachability_reason(monkeypatch):
    module = _load(monkeypatch, live="1")
    _probe(monkeypatch, module, reachable=False)
    [item] = _items("test_channels.py")
    module.pytest_collection_modifyitems(config=FakeConfig(), items=[item])
    [reason] = _skip_reasons(item)
    assert "not reachable" in reason


def test_tests_outside_the_e2e_directory_are_left_alone(conftest, monkeypatch):
    probes = _probe(monkeypatch, conftest, reachable=True)
    outside = FakeItem(CONFTEST.parents[1] / "unit" / "test_something.py")
    config = FakeConfig()
    items = [outside]
    conftest.pytest_collection_modifyitems(config=config, items=items)
    assert items == [outside] and outside.markers == [] and config.deselected == []
    assert probes == []


@pytest.mark.parametrize("error", [
    httpx.ConnectError("refused"),
    httpx.ReadTimeout("slow"),
    httpx.RemoteProtocolError("garbled"),
    httpx.ReadError("reset"),
    httpx.UnsupportedProtocol("scheme"),
])
def test_any_httpx_error_reads_as_unreachable(conftest, monkeypatch, error):
    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(httpx, "get", fail)
    assert conftest._container_reachable() is False


@pytest.mark.parametrize("value", [None, "", "0"])
def test_e2e_client_refuses_before_any_httpx_call_without_opt_in(monkeypatch, value):
    module = _load(monkeypatch, live="1")
    if value is None:
        monkeypatch.delenv("ECM_E2E_LIVE", raising=False)
    else:
        monkeypatch.setenv("ECM_E2E_LIVE", value)
    fixture = module.e2e_client._get_wrapped_function()
    with pytest.raises(pytest.fail.Exception, match="ECM_E2E_LIVE=1"):
        next(fixture())


# --- real pytest against a copy of the conftest --------------------------------

HTTPX_STUB = '''\
import os


def _record(name):
    with open(os.environ["E2E_MARKS"] + "/httpx", "a") as stream:
        stream.write(name + "\\n")


class HTTPError(Exception):
    pass


class ConnectError(HTTPError):
    pass


class TimeoutException(HTTPError):
    pass


def get(*_args, **_kwargs):
    _record("get")
    raise ConnectError("httpx is stubbed in this test")


def post(*_args, **_kwargs):
    _record("post")
    raise ConnectError("httpx is stubbed in this test")


class Client:
    def __init__(self, *_args, **_kwargs):
        _record("Client")
        raise ConnectError("httpx is stubbed in this test")
'''

DUMMY_TEST = '''\
import os

with open(os.environ["E2E_MARKS"] + "/imported", "a") as stream:
    stream.write(__name__ + "\\n")


def test_marks_that_it_ran():
    with open(os.environ["E2E_MARKS"] + "/ran", "a") as stream:
        stream.write(__name__ + "\\n")


def test_uses_the_live_client(e2e_client):
    with open(os.environ["E2E_MARKS"] + "/ran", "a") as stream:
        stream.write(__name__ + " client\\n")
'''


@pytest.fixture
def e2e_tree(tmp_path):
    root = tmp_path / "backend"
    e2e = root / "tests/e2e"
    (e2e / "sub").mkdir(parents=True)
    (root / "tests/unit").mkdir()
    shutil.copy(PYTEST_INI, root / "pytest.ini")
    shutil.copy(CONFTEST, e2e / "conftest.py")
    for name in ("tests/__init__.py", "tests/e2e/__init__.py", "tests/e2e/sub/__init__.py"):
        (root / name).touch()
    (e2e / "test_live.py").write_text(DUMMY_TEST)
    (e2e / "sub/test_deep.py").write_text(DUMMY_TEST)
    (root / "tests/unit/test_ok.py").write_text("def test_ok():\n    pass\n")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "httpx.py").write_text(HTTPX_STUB)
    marks = tmp_path / "marks"
    marks.mkdir()
    env = {key: value for key, value in os.environ.items()
           if key not in ("ECM_E2E_LIVE", "PYTEST_ADDOPTS", "PYTEST_PLUGINS")}
    env.update(PYTHONPATH=str(stubs), E2E_MARKS=str(marks))
    return root, marks, env


def _run(e2e_tree, *args, live=None):
    root, marks, env = e2e_tree
    if live is not None:
        env = {**env, "ECM_E2E_LIVE": live}
    # respx's autoloaded plugin imports httpx, which is stubbed here. Under
    # --strict-markers, -p no:skipping unregisters the skip marker that
    # pytest-benchmark's collection hook builds, which aborts the run before
    # the conftest is exercised, so those runs drop pytest-benchmark too.
    if "no:skipping" in args:
        args = ("-p", "no:benchmark", *args)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--no-cov", "-p", "no:cacheprovider", "-p", "no:respx",
         "-rA", *args],
        cwd=root, env=env, text=True, capture_output=True, timeout=120,
    )
    seen = {name: (marks / name).read_text().split("\n")[:-1] if (marks / name).exists() else []
            for name in ("imported", "ran", "httpx")}
    return result, seen


@pytest.mark.parametrize("args", [
    ["tests/e2e"],
    ["tests/e2e/sub"],
    ["tests/e2e/"],
    ["-p", "no:skipping", "tests/e2e"],
    ["-p", "no:skipping", "tests/e2e/sub"],
    ["-o", "addopts=", "tests"],
    ["-p", "no:skipping", "-o", "addopts=", "tests"],
    ["-o", "testpaths=tests/e2e"],
    ["-p", "no:skipping", "-o", "addopts=", "-o", "testpaths=tests/e2e/sub"],
])
def test_without_opt_in_e2e_modules_are_never_imported_or_run(e2e_tree, args):
    result, seen = _run(e2e_tree, *args)
    output = result.stdout + result.stderr
    assert result.returncode in (0, 5), output
    assert seen == {"imported": [], "ran": [], "httpx": []}, output


@pytest.mark.parametrize("args", [
    ["tests/e2e/test_live.py"],
    ["tests/e2e/sub/test_deep.py"],
    ["-p", "no:skipping", "tests/e2e/test_live.py"],
    ["-p", "no:skipping", "tests/e2e/sub/test_deep.py::test_uses_the_live_client"],
])
def test_without_opt_in_a_named_e2e_file_is_deselected_not_run(e2e_tree, args):
    result, seen = _run(e2e_tree, *args)
    output = result.stdout + result.stderr
    assert result.returncode == 5, output
    assert "deselected" in output and "ECM_E2E_LIVE=1" in output, output
    assert seen["ran"] == [] and seen["httpx"] == [], output


def test_the_default_selection_runs_unit_tests_and_leaves_e2e_alone(e2e_tree):
    result, seen = _run(e2e_tree)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "1 passed" in output
    assert seen == {"imported": [], "ran": [], "httpx": []}, output


def test_with_opt_in_a_named_e2e_directory_is_collected_despite_the_ini_ignore(e2e_tree):
    result, seen = _run(e2e_tree, "tests/e2e", live="1")
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "4 skipped" in output and "not reachable" in output, output
    assert sorted(seen["imported"]) == ["tests.e2e.sub.test_deep", "tests.e2e.test_live"], output
    assert seen["ran"] == [], output
    assert seen["httpx"] == ["get"], "the opt-in probe runs once, through httpx"
