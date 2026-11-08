"""Use recorded real results; missing modes or a one-sample drift must fail."""

import copy
import gzip
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def runner():
    spec = importlib.util.spec_from_file_location(
        "run_alpine_codec_matrix", ROOT / "scripts/run_alpine_codec_matrix.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reference_is_the_complete_recorded_35_case_baseline(runner):
    records = runner.reference()
    assert len(records) == 35
    assert {mode for mode, _ in records} == {0, 1, 2, 3, 4, 5, 8}
    assert sum(row["decoded_bytes"] // 2 for row in records.values()) == 153600


def test_exact_native_result_passes_but_any_output_or_input_drift_fails(runner):
    artifact = ROOT / "docs/design/evidence/stable-alpine-native-amd64-20261108.json.gz"
    record = json.loads(gzip.decompress(artifact.read_bytes()))["codec_matrix"][
        "cases"
    ][0]
    runner.compare(record["candidate"], record["baseline"], "amd64")
    for key in [
        "input_sha256",
        "encoded_sha256",
        "decoded_sha256",
        "encoded_hex",
        "samples_per_frame",
    ]:
        changed = copy.deepcopy(record["candidate"])
        changed[key] = "not the original result"
        with pytest.raises(runner.MatrixError):
            runner.compare(changed, record["baseline"], "amd64")
    changed = {**record["candidate"], "loaded_codec_hashes": {}}
    with pytest.raises(runner.MatrixError):
        runner.compare(changed, record["baseline"], "amd64")
    with pytest.raises(runner.MatrixError):
        runner.compare(record["candidate"], record["baseline"], "arm64")


def test_timeout_is_retained_as_a_failure_not_a_missing_receipt(
    runner, monkeypatch, tmp_path
):
    baseline = next(iter(runner.reference().values()))
    monkeypatch.setattr(
        runner, "reference", lambda: {(baseline["mode"], baseline["corpus"]): baseline}
    )

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(
            command, 60, output=b"partial output", stderr=b"partial error"
        )

    monkeypatch.setattr(runner.subprocess, "run", timeout)
    with pytest.raises(runner.MatrixError, match="timed out"):
        runner.collect("sha256:" + "a" * 64, "amd64", tmp_path / "results")
    summary = json.loads((tmp_path / "results/summary.json").read_text())
    assert summary["status"] == "FAILED" and len(summary["failures"]) == 2
    records = [
        json.loads(p.read_text()) for p in (tmp_path / "results").glob("*-decode*.json")
    ]
    assert len(records) == 2
    assert all(
        row["stdout"] == "partial output" and row["stderr"] == "partial error"
        for row in records
    )
