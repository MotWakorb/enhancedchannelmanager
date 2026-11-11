"""Production orchestration contracts; no native library or hazard probe executes."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ASSETS = ROOT / "docker/alpine-native"


@pytest.fixture
def builder(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "security_upstream_integration",
        ROOT / "scripts/build_alpine_security_libraries.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "subprocess", subprocess, raising=False)
    return module


@pytest.fixture
def case(builder, tmp_path, monkeypatch):
    work = tmp_path / "work"
    source = work / "libxml2"
    source.mkdir(parents=True)
    (source / "Makefile").write_text("fixture, not executable native tests\n")
    sources = tmp_path / "sources"
    (sources / "recipe").mkdir(parents=True)
    events = []
    ownership = []
    monkeypatch.setattr(
        builder.os,
        "chown",
        lambda path, uid, gid, **kw: ownership.append((path, uid, gid, kw)),
    )
    monkeypatch.setattr(builder, "run", lambda argv, **kw: events.append((argv, kw)))
    contract = json.loads((ASSETS / "libxml2-upstream-test-contract.json").read_bytes())

    def capture(argv, **kwargs):
        events.append((argv, kwargs))
        rows = []
        for command, name in [
            (["./runtest"], "runtest"),
            (["./testparser"], "testparser"),
            (["make", "Catatests"], "Catatests"),
        ]:
            log = source / f"proof-upstream-{name}.log"
            log.write_bytes((name + " orchestration fixture\n").encode())
            rows.append(
                {
                    "command": command,
                    "log": log.name,
                    "sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
                }
            )
        receipt = {
            "status": "PASS",
            "upstream": rows,
            "runtest_summary": {"executed": 73, "errors": 0, "leaks": 0},
            "catalog_golden_pairs": [
                p["catalog"] for p in contract["catalog_golden_pairs"]
            ],
            "contract_sha256": hashlib.sha256(
                (ASSETS / "libxml2-upstream-test-contract.json").read_bytes()
            ).hexdigest(),
        }
        (source / "proof-upstream.json").write_text(json.dumps(receipt) + "\n")

    monkeypatch.setattr(builder.subprocess, "run", capture)
    return source, sources, work, events, ownership, capture


def invoke(builder, case, *, name="libxml2"):
    source, sources, work, *_ = case
    return builder.build_autotools(
        {"name": name, "configure": ["--prefix=/usr"]},
        source,
        work / "sdk",
        {"PATH": "/usr/bin:/bin", "RNG_INCLUDE_LIMIT": "1"},
        work=work,
        sources=sources,
        assets=ASSETS,
    )


def test_xml_runs_nonroot_upstream_phase_after_check_before_install(builder, case):
    source, sources, work, events, ownership, _ = case
    result = invoke(builder, case)
    commands = [entry[0] for entry in events]
    assert commands[:3] == [
        [str(source / "configure"), "--prefix=/usr"],
        ["/usr/bin/make", "-j2"],
        ["/usr/bin/make", "-j2", "check"],
    ]
    assert commands[3][1:] == [
        str(ROOT / "scripts/prove_libxml2_recursion.py"),
        "upstream",
        "--repo",
        str(ROOT),
        "--assets",
        str(ASSETS),
        "--source",
        str(source),
    ]
    assert commands[4] == ["/usr/bin/make", "install", "DESTDIR=" + str(work / "sdk")]
    call = events[3][1]
    assert call["user"] == call["group"] == 3000
    assert call["extra_groups"] == [] and call["check"] is True
    assert call["cwd"] == source
    assert call["env"]["ECM_DISPOSABLE_LIBXML2_PROOF"] == "1"
    assert call["env"]["TMPDIR"] == str(source / ".proof-tmp")
    assert "RNG_INCLUDE_LIMIT" not in call["env"]
    assert ownership
    assert all(
        Path(p).is_relative_to(source)
        and uid == gid == 3000
        and kw == {"follow_symlinks": False}
        for p, uid, gid, kw in ownership
    )
    retained = sources / "recipe/libxml2-upstream-proof"
    assert {p.name for p in retained.iterdir()} == {
        "proof-upstream.json",
        "proof-upstream-runtest.log",
        "proof-upstream-testparser.log",
        "proof-upstream-Catatests.log",
    }
    for path in retained.iterdir():
        assert path.read_bytes() == (source / path.name).read_bytes()
    assert result["execution_uid"] == result["execution_gid"] == 3000
    assert result["retained_files"] == builder.owned_artifacts(retained)
    assert result["helper_sha256"] == builder.sha256(
        ROOT / "scripts/prove_libxml2_recursion.py"
    )


def test_speex_keeps_make_check_and_install_without_xml_dispatch(builder, case):
    *_, events, ownership, _ = case
    assert invoke(builder, case, name="speex") is None
    assert len(events) == 4 and not ownership
    assert events[2][0] == ["/usr/bin/make", "-j2", "check"]
    assert events[3][0][:2] == ["/usr/bin/make", "install"]


@pytest.mark.parametrize(
    "mutation",
    [
        "running",
        "wrong-contract",
        "missing-log",
        "rewritten-log",
        "symlink-log",
        "missing-pair",
        "zero-counter",
        "leaks",
        "wrong-command",
        "extra-proof-field",
    ],
)
def test_invalid_proof_never_reaches_install(builder, case, monkeypatch, mutation):
    source, _, _, events, _, capture = case

    def corrupt(argv, **kwargs):
        capture(argv, **kwargs)
        path = source / "proof-upstream.json"
        proof = json.loads(path.read_text())
        log = source / proof["upstream"][0]["log"]
        if mutation == "running":
            proof["status"] = "RUNNING"
        elif mutation == "wrong-contract":
            proof["contract_sha256"] = "0" * 64
        elif mutation == "missing-log":
            log.unlink()
        elif mutation == "rewritten-log":
            log.write_bytes(b"different capture\n")
        elif mutation == "symlink-log":
            log.unlink()
            log.symlink_to(source / "Makefile")
        elif mutation == "missing-pair":
            proof["catalog_golden_pairs"].pop()
        elif mutation == "zero-counter":
            proof["runtest_summary"]["executed"] = 0
        elif mutation == "leaks":
            proof["runtest_summary"]["leaks"] = 1
        elif mutation == "wrong-command":
            proof["upstream"][0]["command"] = ["true"]
        else:
            proof["unreviewed"] = True
        path.write_text(json.dumps(proof))

    monkeypatch.setattr(builder.subprocess, "run", corrupt)
    with pytest.raises(builder.BuildError):
        invoke(builder, case)
    assert not any(argv[:2] == ["/usr/bin/make", "install"] for argv, _ in events)


def test_unreviewed_helper_is_rejected_before_ownership_or_execution(
    builder, case, monkeypatch
):
    original = builder.sha256
    monkeypatch.setattr(
        builder,
        "sha256",
        lambda p: "0" * 64
        if Path(p).name == "prove_libxml2_recursion.py"
        else original(p),
    )
    with pytest.raises(builder.BuildError, match="proof helper"):
        invoke(builder, case)
    assert not case[4]
    assert len(case[3]) == 3


def test_source_ownership_cannot_target_the_work_root(builder, case):
    source, sources, work, events, ownership, _ = case
    with pytest.raises(builder.BuildError, match="disposable extracted source"):
        builder.libxml2_upstream_proof(
            work, work=work, sources=sources, assets=ASSETS, env={}
        )
    assert not events and not ownership


def test_failed_upstream_process_never_reaches_install(builder, case, monkeypatch):
    def failed(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(builder.subprocess, "run", failed)
    with pytest.raises(subprocess.CalledProcessError):
        invoke(builder, case)
    assert len(case[3]) == 3


def test_ownership_does_not_follow_an_extracted_source_link(builder, case):
    source, _, work, _, ownership, _ = case
    outside = work / "unrelated"
    outside.write_bytes(b"unrelated work product")
    (source / "link").symlink_to(outside)
    invoke(builder, case)
    assert outside.read_bytes() == b"unrelated work product"
    assert outside not in [path for path, *_ in ownership]
    link = next(row for row in ownership if row[0] == source / "link")
    assert link[3] == {"follow_symlinks": False}


def test_real_builder_routes_xml_through_the_preinstall_seam(
    builder, tmp_path, monkeypatch
):
    """Exercise the actual build dispatcher, not just its extracted function."""
    recipe = json.loads((ASSETS / "security-libraries.json").read_bytes())
    recipe["libraries"] = [recipe["libraries"][0]]
    recipe["libraries"][0]["patches"] = []
    monkeypatch.setattr(builder, "load_recipe", lambda path: recipe)
    monkeypatch.setattr(builder, "validate_platform", lambda *args: None)
    monkeypatch.setattr(builder, "validate_repositories", lambda *args: None)
    monkeypatch.setattr(builder.os, "geteuid", lambda: 0)
    monkeypatch.setattr(builder, "run", lambda *args, **kwargs: "fixture")
    monkeypatch.setattr(builder, "fetch_input", lambda *args: None)
    monkeypatch.setattr(builder, "extract_source", lambda archive, path: path.mkdir())
    monkeypatch.setattr(
        builder,
        "_install_builder_sdk",
        lambda sdk: pytest.fail("installation boundary reached without the proof seam"),
    )
    calls = []

    class StopBeforeInstall(Exception):
        pass

    def seam(item, source, sdk, env, **kwargs):
        calls.append((item, source, sdk, env, kwargs))
        raise StopBeforeInstall

    monkeypatch.setattr(builder, "build_autotools", seam)
    with pytest.raises(StopBeforeInstall):
        builder.build(
            ASSETS / "security-libraries.json",
            "amd64",
            tmp_path / "work",
            tmp_path / "output",
            None,
        )
    assert len(calls) == 1
    item, source, sdk, env, kwargs = calls[0]
    assert item["name"] == "libxml2"
    assert source == tmp_path / "work/libxml2"
    assert sdk == tmp_path / "output/sdk" and env["CFLAGS"] == "-O2 -fPIC"
    assert kwargs == {
        "work": tmp_path / "work",
        "sources": tmp_path / "output/sources",
        "assets": ASSETS,
    }
    retained = tmp_path / "output/sources/recipe"
    for name, original in [
        ("prove_libxml2_recursion.py", ROOT / "scripts/prove_libxml2_recursion.py"),
        (
            "libxml2-upstream-test-contract.json",
            ASSETS / "libxml2-upstream-test-contract.json",
        ),
    ]:
        assert (retained / name).read_bytes() == original.read_bytes()
