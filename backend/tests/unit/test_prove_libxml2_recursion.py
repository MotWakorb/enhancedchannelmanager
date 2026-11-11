"""Safety/identity tests for the deliberately narrow native proof helper."""

from __future__ import annotations

import importlib.util
import base64
import gzip
import hashlib
import json
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
