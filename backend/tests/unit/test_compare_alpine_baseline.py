"""Compare native Alpine with the actual old image, not an AMD64 proxy."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "compare_alpine_baseline", ROOT / "scripts/compare_alpine_baseline.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_retained_real_baseline_is_bound_to_original_source_and_both_native_identities(
    runner,
):
    descriptor = runner.load_baselines()
    assert (
        descriptor["application_source"] == "06cc4bf5a1b9eb1f6ef29f617513d237cda2f531"
    )
    arm = descriptor["architectures"]["arm64"]
    assert (
        arm["identity"]["ConfigDigest"]
        == "sha256:33db988ca62173209458e8df077081380e7d23b7da1188469a0899da803f1f2c"
    )
    assert arm["identity"]["Architecture"] == "arm64"
    assert (
        arm["image_artifact"]["sha256"]
        == "e5b37ca36e5fb6fcb62b91ca3241eff407a009f72071d3fcafe5aeeb1c207ea1"
    )
    assert set(descriptor["architectures"]) == {"amd64", "arm64"}


def test_paired_comparison_does_not_treat_recorded_amd64_as_the_arm_baseline(runner):
    expected = next(iter(runner.reference().values()))
    actual = {
        **expected,
        "machine": "aarch64",
        "python_version": "3.12.15",
        "library_sha256": "b" * 64,
        "loaded_codec_hashes": {"/usr/local/lib/libcodec2.so.1.2": "b" * 64},
    }
    # A real old-ARM result may differ from recorded AMD64; only equality with
    # that independently executed old-ARM result clears this paired gate.
    baseline = {**actual, "decoded_sha256": "c" * 64}
    with pytest.raises(runner.MatrixError, match="decoded_sha256"):
        runner.compare(actual, baseline, "arm64")
    runner.compare(baseline, baseline, "arm64")


def test_comparison_never_reuses_one_image_as_both_subjects(runner, tmp_path):
    image = runner.load_baselines()["architectures"]["arm64"]["identity"][
        "ConfigDigest"
    ]
    with pytest.raises(runner.MatrixError, match="distinct"):
        runner.collect(image, image, "arm64", tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_media_comparison_covers_all_five_frames_and_six_complete_outputs(runner):
    media = {
        "machine": "aarch64",
        "python_version": "3.12.15",
        "status": "COLLECTED_ONLY",
        "input_hashes": {name: "a" * 64 for name in runner.FIXTURES},
        "capabilities": {name: "full output " + name for name in runner.CAPABILITIES},
        "frames": {name: "complete framemd5 " + name for name in runner.FIXTURES},
        "existing_gate": {
            "synthetic_rare_input_decode": {
                name: {"metadata": "fixture"} for name in runner.FIXTURES
            }
        },
    }
    runner.compare_media(media, media, "arm64")
    for bad in [
        {**media, "frames": {}},
        {**media, "capabilities": {}},
        {**media, "frames": {**media["frames"], "synthetic.jxl": "changed pixel"}},
    ]:
        with pytest.raises(runner.MatrixError):
            runner.compare_media(bad, media, "arm64")


def test_failed_process_still_produces_a_complete_failed_receipt(
    runner, tmp_path, monkeypatch
):
    monkeypatch.setattr(runner, "reference", lambda: {(0, "silence"): {}})

    def fail(*args, **kwargs):
        raise OSError("deliberate native-process launch failure")

    monkeypatch.setattr(runner.subprocess, "run", fail)
    baseline = runner.load_baselines()["architectures"]["arm64"]["identity"][
        "ConfigDigest"
    ]
    with pytest.raises(runner.MatrixError):
        runner.collect(baseline, "sha256:" + "a" * 64, "arm64", tmp_path / "output")
    record = json.loads((tmp_path / "output/summary.json").read_text())
    assert record["status"] == "FAILED" and record["failures"]


def test_wrong_old_baseline_id_is_not_an_operator_override(runner, tmp_path):
    with pytest.raises(runner.MatrixError, match="original baseline identity"):
        runner.collect(
            "sha256:" + "a" * 64, "sha256:" + "b" * 64, "arm64", tmp_path / "output"
        )


def test_missing_both_media_results_is_not_parity(runner):
    media = {
        "machine": "aarch64",
        "python_version": "3.12.15",
        "capabilities": {name: "" for name in runner.CAPABILITIES},
        "frames": {name: "" for name in runner.FIXTURES},
    }
    with pytest.raises(runner.MatrixError, match="media"):
        runner.compare_media(media, media, "arm64")


def test_native_timeouts_retain_partial_stdout_and_stderr(
    runner, tmp_path, monkeypatch
):
    monkeypatch.setattr(runner, "reference", lambda: {(0, "silence"): {}})

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(
            command,
            90,
            output=b"partial native stdout",
            stderr=b"partial native stderr",
        )

    monkeypatch.setattr(runner.subprocess, "run", timeout)
    baseline = runner.load_baselines()["architectures"]["arm64"]["identity"][
        "ConfigDigest"
    ]
    with pytest.raises(runner.MatrixError):
        runner.collect(baseline, "sha256:" + "a" * 64, "arm64", tmp_path / "output")
    record = json.loads(
        (tmp_path / "output/0-silence-baseline-encode-decode.json").read_text()
    )
    assert record["stdout"] == "partial native stdout"
    assert record["stderr"] == "partial native stderr"
