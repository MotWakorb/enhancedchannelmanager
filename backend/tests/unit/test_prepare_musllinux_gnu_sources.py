"""Real reviewed GNU source plan; transport stubs never claim source builds."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def preparer(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "prepare_musllinux_gnu_sources",
        ROOT / "scripts/prepare_musllinux_gnu_sources.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recorded_plan_covers_all27_deliveries_and21_unique_original_inputs(preparer):
    manifest, inputs = preparer.source_inputs()
    assert len(inputs) == 21
    assert (
        sum(len(row["source_files"]) for row in manifest["source_closures"].values())
        == 27
    )
    assert sum("local" in row for row in inputs) == 13
    assert sum("urls" in row for row in inputs) == 8
    assert {row["sha256"] for row in inputs} == {
        row["sha256"]
        for family in manifest["source_closures"].values()
        for row in family["source_files"]
    }
    assert all(type(row["size"]) is int and row["size"] > 0 for row in inputs)
    for row in inputs:
        if "local" in row:
            assert preparer.sha256(ROOT / row["local"]) == row["sha256"]
        else:
            assert row["urls"] and all(
                url.startswith("https://") and "latest" not in url
                for url in row["urls"]
            )
    assert {row["status"] for row in manifest["source_closures"].values()} == {
        "AUTHENTICATED_PROVIDER_SOURCE",
        "REPRODUCED_CORRESPONDING_SOURCE",
    }


def test_original_config_sub_is_retained_without_a_savannah_network_dependency(
    preparer,
):
    _manifest, inputs = preparer.source_inputs()
    row = next(
        row
        for row in inputs
        if row["sha256"]
        == "75d5d255a2a273b6e651f82eecfabf6cbcd8eaeae70e86b417384c8f4a58d8d3"
    )
    assert row == {
        "local": "docker/python-third-party/gnu-source-assets/config.sub",
        "size": 36159,
        "sha256": "75d5d255a2a273b6e651f82eecfabf6cbcd8eaeae70e86b417384c8f4a58d8d3",
    }
    assert preparer.sha256(ROOT / row["local"]) == row["sha256"]


@pytest.mark.parametrize("defect", ["missing", "corrupt", "symlink"])
def test_retained_config_sub_is_validated_before_any_download(
    preparer, tmp_path, monkeypatch, defect
):
    manifest, inputs = preparer.source_inputs()
    row = next(
        row
        for row in inputs
        if row.get("local") == "docker/python-third-party/gnu-source-assets/config.sub"
    )
    retained = tmp_path / "config.sub"
    if defect == "corrupt":
        original = (ROOT / row["local"]).read_bytes()
        retained.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    elif defect == "symlink":
        retained.symlink_to(ROOT / row["local"])
    row["local"] = str(retained)
    monkeypatch.setattr(preparer, "source_inputs", lambda: (manifest, inputs))

    def forbidden(*args, **kwargs):
        pytest.fail("invalid retained config.sub must fail before network")

    monkeypatch.setattr(preparer, "fetch_input", forbidden)
    work, output = tmp_path / "work", tmp_path / "sources"
    with pytest.raises(preparer.AttributionError, match="retained GNU source asset"):
        preparer.prepare(work, output)
    assert not work.exists() and not output.exists()


@pytest.mark.parametrize(
    "defect",
    [
        "existing-work",
        "existing-output",
        "alias-parent",
        "overlap",
        "wrong-local-input",
    ],
)
def test_preparation_rejects_unsafe_locations_and_changed_retained_assets_before_fetch(
    preparer, monkeypatch, tmp_path, defect
):
    work, output = tmp_path / "work", tmp_path / "sources"
    if defect == "existing-work":
        work.mkdir()
    elif defect == "existing-output":
        output.mkdir()
    elif defect == "alias-parent":
        alias = tmp_path / "alias"
        alias.symlink_to(tmp_path, target_is_directory=True)
        output = alias / "sources"
    elif defect == "overlap":
        output = work / "sources"
    else:
        original = preparer.source_inputs

        def changed():
            manifest, plan = original()
            local = next(row for row in plan if "local" in row)
            local["sha256"] = "0" * 64
            return manifest, plan

        monkeypatch.setattr(preparer, "source_inputs", changed)

    def forbidden(*args, **kwargs):
        pytest.fail("unsafe input must fail before network")

    monkeypatch.setattr(preparer, "fetch_input", forbidden)
    with pytest.raises(preparer.AttributionError):
        preparer.prepare(work, output)


def test_complete_exact_offline_cache_is_not_a_missing_input_network_fallback(
    preparer, tmp_path, monkeypatch
):
    cache = tmp_path / "cache"
    cache.mkdir()

    def forbidden(*args, **kwargs):
        pytest.fail("incomplete explicit cache cannot use network")

    monkeypatch.setattr(preparer, "fetch_input", forbidden)
    with pytest.raises(preparer.AttributionError):
        preparer.prepare(tmp_path / "work", tmp_path / "sources", cache=cache)


PRIMARY = {
    "gcc-14.2.0.tar.xz": "https://ftp.gnu.org/gnu/gcc/gcc-14.2.0/gcc-14.2.0.tar.xz",
    "gcc-12.4.0.tar.xz": "https://ftp.gnu.org/gnu/gcc/gcc-12.4.0/gcc-12.4.0.tar.xz",
    "binutils-2.44.tar.gz": "https://ftp.gnu.org/gnu/binutils/binutils-2.44.tar.gz",
    "gmp-6.3.0.tar.xz": "https://ftp.gnu.org/gnu/gmp/gmp-6.3.0.tar.xz",
    "mpc-1.3.1.tar.gz": "https://ftp.gnu.org/gnu/mpc/mpc-1.3.1.tar.gz",
    "mpfr-4.2.2.tar.xz": "https://ftp.gnu.org/gnu/mpfr/mpfr-4.2.2.tar.xz",
    "musl-1.2.5.tar.gz": "https://musl.libc.org/releases/musl-1.2.5.tar.gz",
    "linux-headers-4.19.88-2.tar.xz": "https://ftp.barfooze.de/pub/sabotage/tarballs/linux-headers-4.19.88-2.tar.xz",
}


def test_primary_route_stays_first_and_mirrors_keep_the_same_file(preparer):
    assert set(preparer.URLS) == set(PRIMARY)
    for name, urls in preparer.URLS.items():
        assert type(urls) is tuple and urls[0] == PRIMARY[name]
        assert len(set(urls)) == len(urls)
        assert all(
            url.startswith("https://") and url.rsplit("/", 1)[1] == name
            for url in urls
        )
    assert preparer.URLS["musl-1.2.5.tar.gz"][1:] == (
        "https://distfiles.alpinelinux.org/distfiles/v3.23/musl-1.2.5.tar.gz",
    )
    for name, primary in PRIMARY.items():
        if primary.startswith("https://ftp.gnu.org/gnu/"):
            assert preparer.URLS[name][1:] == (
                primary.replace("https://ftp.gnu.org/", "https://mirrors.kernel.org/"),
            )
    _manifest, inputs = preparer.source_inputs()
    assert sorted(tuple(row["urls"]) for row in inputs if "urls" in row) == sorted(
        preparer.URLS.values()
    )


class _Response(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def _row(data, *urls):
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "urls": list(urls),
    }


@pytest.fixture
def network(preparer, monkeypatch):
    """Replace only the transport under the real shared fetch/verify code."""
    calls, waits, routes = [], [], {}

    def open_url(url, *, timeout):
        calls.append(url)
        outcome = routes[url]
        if isinstance(outcome, BaseException):
            raise outcome
        return _Response(outcome, url)

    fetch_globals = preparer.fetch_input.__globals__
    monkeypatch.setitem(fetch_globals, "urlopen", open_url)
    monkeypatch.setattr(fetch_globals["time"], "sleep", waits.append)
    return calls, waits, routes


PRIMARY_URL = "https://primary.example/pub/src-1.0.tar.gz"
MIRROR_URL = "https://mirror.example/pub/src-1.0.tar.gz"
DATA = b"exact reviewed source bytes\n" * 64


@pytest.mark.parametrize(
    "failure",
    [
        URLError(OSError(101, "Network is unreachable")),
        HTTPError(PRIMARY_URL, 404, "Not Found", {}, None),
        HTTPError(PRIMARY_URL, 503, "Service Unavailable", {}, None),
    ],
    ids=["network-unreachable", "http-404", "http-503"],
)
def test_failed_primary_fetch_falls_back_to_the_next_pinned_mirror(
    preparer, network, tmp_path, capsys, failure
):
    calls, waits, routes = network
    routes.update({PRIMARY_URL: failure, MIRROR_URL: DATA})
    target = tmp_path / "input"
    used = preparer.fetch_pinned(_row(DATA, PRIMARY_URL, MIRROR_URL), target)
    assert used == MIRROR_URL
    assert target.read_bytes() == DATA
    primary_attempts = 3 if getattr(failure, "code", None) == 503 else 1
    assert calls == [PRIMARY_URL] * primary_attempts + [MIRROR_URL]
    assert waits == [1, 2][: primary_attempts - 1]
    out = capsys.readouterr().out
    assert f"fetched {_row(DATA)['sha256']} from {MIRROR_URL}" in out


def test_working_primary_is_used_without_touching_mirrors(
    preparer, network, tmp_path
):
    calls, _waits, routes = network
    routes.update({PRIMARY_URL: DATA, MIRROR_URL: AssertionError("mirror used")})
    used = preparer.fetch_pinned(
        _row(DATA, PRIMARY_URL, MIRROR_URL), tmp_path / "input"
    )
    assert used == PRIMARY_URL and calls == [PRIMARY_URL]


@pytest.mark.parametrize("defect", ["sha256", "size"])
def test_identity_mismatch_is_fatal_and_never_falls_back(
    preparer, network, tmp_path, defect
):
    calls, _waits, routes = network
    wrong = bytes([DATA[0] ^ 1]) + DATA[1:] if defect == "sha256" else DATA + b"x"
    routes.update({PRIMARY_URL: wrong, MIRROR_URL: DATA})
    row = _row(DATA, PRIMARY_URL, MIRROR_URL)
    if defect == "size":
        row["sha256"] = hashlib.sha256(wrong).hexdigest()
    expected = "source/RPM checksum" if defect == "sha256" else "pinned input size"
    with pytest.raises(
        preparer.BuildError, match=f"^{expected} mismatch: "
    ) as failure:
        preparer.fetch_pinned(row, tmp_path / "input")
    assert PRIMARY_URL in str(failure.value) and MIRROR_URL not in str(failure.value)
    assert calls == [PRIMARY_URL]


def test_mirror_identity_mismatch_after_primary_outage_is_fatal(
    preparer, network, tmp_path
):
    calls, _waits, routes = network
    third = "https://third.example/pub/src-1.0.tar.gz"
    routes.update(
        {
            PRIMARY_URL: URLError(OSError(101, "Network is unreachable")),
            MIRROR_URL: DATA[:-1] + b"?",
            third: DATA,
        }
    )
    with pytest.raises(
        preparer.BuildError, match="^source/RPM checksum mismatch: "
    ) as failure:
        preparer.fetch_pinned(
            _row(DATA, PRIMARY_URL, MIRROR_URL, third), tmp_path / "input"
        )
    assert MIRROR_URL in str(failure.value) and third not in str(failure.value)
    assert calls == [PRIMARY_URL, MIRROR_URL]


def test_every_route_failing_reports_each_route(preparer, network, tmp_path):
    calls, _waits, routes = network
    routes.update(
        {
            PRIMARY_URL: URLError(OSError(101, "Network is unreachable")),
            MIRROR_URL: HTTPError(MIRROR_URL, 404, "Not Found", {}, None),
        }
    )
    target = tmp_path / "input"
    with pytest.raises(preparer.BuildError) as failure:
        preparer.fetch_pinned(_row(DATA, PRIMARY_URL, MIRROR_URL), target)
    message = str(failure.value)
    assert message.startswith("fetch failed on all 2 pinned routes")
    assert PRIMARY_URL in message and MIRROR_URL in message
    assert calls == [PRIMARY_URL, MIRROR_URL] and not target.exists()


def test_receipt_records_the_route_actually_used(
    preparer, network, tmp_path, monkeypatch
):
    calls, _waits, routes = network
    other = b"second pinned input\n" * 8
    other_primary = "https://primary.example/pub/other-2.0.tar.gz"
    routes.update(
        {
            PRIMARY_URL: URLError(OSError(101, "Network is unreachable")),
            MIRROR_URL: DATA,
            other_primary: other,
        }
    )
    plan = sorted(
        [
            _row(DATA, PRIMARY_URL, MIRROR_URL),
            _row(other, other_primary, "https://mirror.example/pub/other-2.0.tar.gz"),
        ],
        key=lambda row: row["sha256"],
    )
    monkeypatch.setattr(preparer, "source_inputs", lambda: ({}, plan))
    monkeypatch.setattr(preparer, "collect", lambda *_a: {"copied": 2})
    monkeypatch.setattr(preparer, "require_source_closure", lambda *_a: None)
    receipt = preparer.prepare(tmp_path / "work", tmp_path / "sources")
    assert receipt["fetched_from"] == {
        _row(DATA)["sha256"]: MIRROR_URL,
        _row(other)["sha256"]: other_primary,
    }
    assert receipt["copied"] == 2
    planned = json.loads((tmp_path / "work/input-plan.json").read_text())
    assert planned == plan
