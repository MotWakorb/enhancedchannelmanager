"""Safety/identity tests for the deliberately narrow native proof helper."""

from __future__ import annotations

import importlib.util
import base64
import gzip
import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def proof(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "prove_libxml2_recursion", REPO / "scripts/prove_libxml2_recursion.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def recorded_files():
    path = (
        REPO
        / "docs/design/evidence/libxml2-recursion-native-amd64-20261111.receipts.json.gz"
    )
    receipt = json.loads(gzip.decompress(path.read_bytes()))
    files = {}
    for name, entry in receipt["files"].items():
        data = base64.b64decode(entry["base64"], validate=True)
        assert len(data) == entry["bytes"]
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
        files[name] = data
    return files


def test_recorded_libxml2_identity_rejects_wrong_archive_before_extraction(
    proof, tmp_path
):
    archive = tmp_path / "libxml2-2.13.9.tar.xz"
    archive.write_bytes(b"not the reviewed upstream archive")
    destination = tmp_path / "source"
    with pytest.raises(proof.BuildError, match="archive identity"):
        proof.prepare(REPO, archive, destination)
    assert not destination.exists()


def test_baseline_preserves_all_nine_recorded_corrections(proof, monkeypatch, tmp_path):
    recipe_root = REPO / "docker/alpine-native"
    item = json.loads((recipe_root / "security-libraries.json").read_text())[
        "libraries"
    ][0]
    archive = tmp_path / "archive"
    with archive.open("wb") as stream:
        stream.truncate(item["archive"]["size"])
    real_hash = proof.sha256
    monkeypatch.setattr(
        proof,
        "sha256",
        lambda path: item["archive"]["sha256"] if path == archive else real_hash(path),
    )
    monkeypatch.setattr(proof, "extract_source", lambda archive, source: source.mkdir())
    applied = Mock(side_effect=lambda record, *_: record)
    monkeypatch.setattr(proof, "apply_patch", applied)
    receipt = proof.prepare(REPO, archive, tmp_path / "source", baseline=True)
    assert [p["id"] for p in receipt["patches"]] == [
        "CVE-2026-6732",
        "CVE-2026-86138",
        "CVE-2026-86139",
        "CVE-2026-86140",
        "CVE-2026-86142",
        "CVE-2026-86143",
        "CVE-2026-86144",
        "CVE-2026-86137",
        "CVE-2026-86141",
    ]
    assert applied.call_count == 9


def test_native_build_requires_disposable_opt_in_before_any_execution(
    proof, monkeypatch, tmp_path
):
    monkeypatch.delenv("ECM_DISPOSABLE_LIBXML2_PROOF", raising=False)
    run = Mock()
    monkeypatch.setattr(proof.subprocess, "run", run)
    with pytest.raises(proof.BuildError, match="disposable native Alpine"):
        proof.native_build(REPO, tmp_path)
    run.assert_not_called()


def test_complete_recorded_native_red_green_matrices(proof, recorded_files):
    for phase in ["baseline", "patched", "production"]:
        matrix = json.loads(recorded_files[f"{phase}/proof-run.json"])
        assert len(matrix["results"]) == 14
        assert matrix["architecture"] == "x86_64"
        assert matrix["uid"] == 3000
        for result in matrix["results"]:
            assert result["exit_code"] == result["expected_exit_code"]
            log = recorded_files[f"{phase}/{result['log']}"]
            if result["log"] != "proof-run-shell.log":
                proof.validate_smoke_status(
                    log,
                    result["exit_code"],
                    result["command"][-1],
                    result["expected_exit_code"],
                )


@pytest.mark.parametrize("phase", ["patched", "production"])
def test_all_seven_recorded_dangerous_guard_mutants_were_detected(
    proof, recorded_files, phase
):
    mutants = json.loads(recorded_files[f"{phase}/proof-mutants.json"])
    assert {m["name"] for m in mutants} == {
        "sgml-depth",
        "sgml-dedup",
        "rng-limit",
        "rng-propagation",
        "shell-command",
        "shell-argument",
        "shell-argv",
    }
    for mutant in mutants:
        assert mutant["exit_code"] == 1
        log = recorded_files[f"{phase}/{mutant['log']}"]
        if mutant["name"].startswith("shell-"):
            assert (mutant["name"] + " FAIL").encode() in log
        else:
            proof.validate_smoke_status(log, 1, mutant["command"][-1], 1)


def test_incomplete_or_empty_pass_output_is_rejected(proof, recorded_files):
    complete = recorded_files["patched/proof-run-2-rng-default.log"]
    proof.validate_smoke_status(complete, 0, "rng-default", 0)
    for incomplete in [b"", b"PASS\n", b"\n".join(complete.splitlines()[:-1])]:
        with pytest.raises(proof.BuildError, match="incomplete lifecycle"):
            proof.validate_smoke_status(incomplete, 0, "rng-default", 0)


def test_tool_failure_is_not_accepted_as_behavioral_red(proof):
    with pytest.raises(proof.BuildError, match="not a regression RED"):
        proof.validate_smoke_status(
            b"unrelated loader or command error\n", 1, "rng-api", 1
        )


def test_recorded_source_identity_matches_the_libxml2_recipe(recorded_files):
    recipe = json.loads(
        (REPO / "docker/alpine-native/security-libraries.json").read_bytes()
    )
    source = json.loads(recorded_files["patched-patch-proof/source.json"])
    assert source["archive"] == recipe["libraries"][0]["archive"]
    assert [p["sha256"] for p in source["patches"]] == [
        p["sha256"] for p in recipe["libraries"][0]["patches"]
    ]
    runtime = json.loads(recorded_files["toolchain-runtime.json"])
    assert runtime["networks"] == {} and runtime["repo_mount_readonly"]
    assert (
        runtime["base_config_digest"]
        == "sha256:0c5586b42b0da040b974f5e87b80c6075374216186f28ad3833eb4cab704fc2f"
    )


@pytest.fixture
def upstream_case(proof, recorded_files, monkeypatch, tmp_path):
    """Materialize exact recorded inputs; stub capture, never execute native code."""
    fixture = json.loads(
        gzip.decompress(
            (
                REPO
                / "backend/tests/fixtures/libxml2/upstream-source-contract-20261111.json.gz"
            ).read_bytes()
        )
    )
    copyright_record = fixture["upstream_copyright"]
    assert (
        hashlib.sha256(
            base64.b64decode(copyright_record["base64"], validate=True)
        ).hexdigest()
        == copyright_record["sha256"]
    )
    source = tmp_path / "source"
    for name, entry in fixture["files"].items():
        data = base64.b64decode(entry["base64"], validate=True)
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    outputs = {
        name: recorded_files[f"production/proof-upstream-{name}.log"]
        for name in ["runtest", "testparser", "Catatests"]
    }
    calls = []

    def capture(command, **kwargs):
        calls.append((command, kwargs))
        if command[0] == "./xmlcatalog":
            output = (source / "result/catalogs" / Path(command[-1]).stem).read_bytes()
        else:
            output = outputs[command[-1].removeprefix("./")]
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr=b"")

    monkeypatch.setattr(proof, "require_native", lambda: None)
    monkeypatch.setattr(proof.subprocess, "run", capture)
    return source, outputs, calls


def test_genuine_completed_upstream_capture_validates(proof, upstream_case):
    source, _, calls = upstream_case
    receipt = proof.native_upstream(source)
    assert receipt["runtest_summary"] == {"executed": 3809, "errors": 0, "leaks": 0}
    assert len(receipt["catalog_golden_pairs"]) == 7
    assert len(calls) == 10
    for _, capture in calls[:3]:
        assert capture["stdout"] == subprocess.PIPE
        assert capture["stderr"] == subprocess.STDOUT


@pytest.mark.parametrize(
    "kind",
    [
        "empty",
        "truncated",
        "zero-tests",
        "errors",
        "leaks",
        "summary-only",
        "missing-section",
        "duplicate-section",
        "trailing-junk",
        "disabled-output",
    ],
)
def test_exit_zero_cannot_hide_incomplete_runtest(proof, upstream_case, kind):
    source, outputs, _ = upstream_case
    complete = outputs["runtest"]
    before_summary = complete.rsplit(b"Total ", 1)[0]
    outputs["runtest"] = {
        "empty": b"",
        "truncated": complete[:-4],
        "zero-tests": before_summary + b"Total 0 tests, no errors\n",
        "errors": before_summary + b"Total 3809 tests, 1 errors, 0 leaks\n",
        "leaks": before_summary + b"Total 3809 tests, 0 errors, 1 leaks\n",
        "summary-only": b"Total 3809 tests, no errors\n",
        "missing-section": b"\n".join(complete.split(b"\n")[1:]),
        "duplicate-section": complete.splitlines(keepends=True)[0] + complete,
        "trailing-junk": complete + b"unfinished extra output\n",
        "disabled-output": b"runtest requires output to be enabled in libxml2\n",
    }[kind]
    with pytest.raises(proof.BuildError):
        proof.native_upstream(source)


def test_harness_counter_is_not_hardcoded_as_unique_fixture_count(proof, upstream_case):
    source, outputs, _ = upstream_case
    outputs["runtest"] = outputs["runtest"].replace(
        b"Total 3809 tests", b"Total 73 tests"
    )
    assert proof.native_upstream(source)["runtest_summary"]["executed"] == 73


@pytest.mark.parametrize(
    "kind",
    [
        "zero-pairs",
        "missing-script",
        "missing-catalog",
        "missing-golden",
        "rewritten-golden",
        "unexpected-pair",
    ],
)
def test_exact_catalog_fixture_coverage_cannot_shrink_or_drift(
    proof, upstream_case, kind
):
    source, _, _ = upstream_case
    if kind == "zero-pairs":
        for path in (source / "test/catalogs").glob("*.script"):
            path.unlink()
    elif kind.startswith("missing-"):
        path = {
            "missing-script": "test/catalogs/recursive.script",
            "missing-catalog": "test/catalogs/recursive.sgml",
            "missing-golden": "result/catalogs/recursive",
        }[kind]
        (source / path).unlink()
    elif kind == "rewritten-golden":
        # An untrusted source tree and fake command can agree with each other.
        (source / "result/catalogs/recursive").write_bytes(
            b"fabricated expected output"
        )
    else:
        (source / "test/catalogs/extra.script").write_bytes(b"")
        (source / "test/catalogs/extra.sgml").write_bytes(b"SYSTEM ordinary value\n")
        (source / "result/catalogs/extra").write_bytes(b"> ")
    with pytest.raises(proof.BuildError):
        proof.native_upstream(source)


@pytest.mark.parametrize(
    "kind", ["empty-parser", "empty-catalog", "missing-shell-case", "truncated-catalog"]
)
def test_other_upstream_commands_need_their_completion_contract(
    proof, upstream_case, kind
):
    source, outputs, _ = upstream_case
    if kind == "empty-parser":
        outputs["testparser"] = b""
    elif kind == "empty-catalog":
        outputs["Catatests"] = b""
    elif kind == "missing-shell-case":
        outputs["Catatests"] = outputs["Catatests"].replace(b"shell-argv PASS\n", b"")
    else:
        outputs["Catatests"] = outputs["Catatests"].split(b"## Add and del")[0]
    with pytest.raises(proof.BuildError):
        proof.native_upstream(source)


def test_failed_capture_cannot_leave_a_stale_pass_receipt(proof, upstream_case):
    source, outputs, _ = upstream_case
    assert proof.native_upstream(source)["status"] == "PASS"
    outputs["runtest"] = b""
    with pytest.raises(proof.BuildError):
        proof.native_upstream(source)
    assert json.loads((source / "proof-upstream.json").read_text())["status"] != "PASS"


def test_command_cannot_redefine_its_own_expected_golden(
    proof, upstream_case, monkeypatch
):
    source, _, _ = upstream_case
    capture = proof.subprocess.run

    def changing_command(command, **kwargs):
        if command == ["./xmlcatalog", "--shell", "test/catalogs/recursive.sgml"]:
            (source / "result/catalogs/recursive").write_bytes(
                b"rewritten during execution"
            )
        return capture(command, **kwargs)

    monkeypatch.setattr(proof.subprocess, "run", changing_command)
    with pytest.raises(proof.BuildError):
        proof.native_upstream(source)


def test_explicit_assets_support_the_actual_production_builder_layout(
    proof, upstream_case, tmp_path
):
    source, _, _ = upstream_case
    assets = tmp_path / "proof/recipe"
    assets.mkdir(parents=True)
    for name in ["security-libraries.json", "libxml2-upstream-test-contract.json"]:
        (assets / name).write_bytes((REPO / "docker/alpine-native" / name).read_bytes())
    assert proof.native_upstream(source, assets=assets)["status"] == "PASS"
    (assets / "libxml2-upstream-test-contract.json").write_bytes(b"{}")
    with pytest.raises(proof.BuildError, match="contract identity"):
        proof.native_upstream(source, assets=assets)


def test_upstream_cli_routes_the_explicit_production_asset_directory(
    proof, monkeypatch
):
    monkeypatch.setattr(
        "sys.argv",
        [
            "prove_libxml2_recursion.py",
            "upstream",
            "--repo",
            "/proof",
            "--source",
            "/proof/work/libxml2",
            "--assets",
            "/proof/recipe",
        ],
    )
    upstream = Mock(return_value={"scope": "argument-routing-only"})
    monkeypatch.setattr(proof, "native_upstream", upstream)
    proof.main()
    upstream.assert_called_once_with(
        Path("/proof/work/libxml2"), assets=Path("/proof/recipe")
    )
