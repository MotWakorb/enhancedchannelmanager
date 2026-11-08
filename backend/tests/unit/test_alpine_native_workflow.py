"""Native migration evidence cannot publish or masquerade as final acceptance."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_native_evidence_uses_real_hosts_and_no_publication_or_required_check_aliases():
    path = ROOT / ".github/workflows/alpine-native.yml"
    text = path.read_text()
    workflow = yaml.load(text, Loader=yaml.BaseLoader)
    assert workflow["permissions"] == {"contents": "read"}
    assert "scripts/run_alpine_codec_matrix.py" in text
    assert "Dockerfile.packages" in text
    assert "--build-context ffmpeg-evidence=" in text
    assert "--build-context gosu-evidence=" in text
    assert 'tar -C "$RUNNER_TEMP/alpine-evidence" -czf' in text
    assert "path: ${{ runner.temp }}/alpine-native-${{ matrix.arch }}.tar.gz" in text
    job = workflow["jobs"]["native-components"]
    assert job["strategy"]["matrix"]["include"] == [
        {"arch": "amd64", "machine": "x86_64", "runner": "ubuntu-24.04"},
        {"arch": "arm64", "machine": "aarch64", "runner": "ubuntu-24.04-arm"},
    ]
    assert "not final-image acceptance" in job["name"]
    assert 'test "$(uname -m)" = "$EXPECTED_MACHINE"' in text
    assert (
        "setup-qemu" not in text
        and "docker push" not in text
        and "packages: write" not in text
    )
    assert (
        "Backend Tests" not in text
        and "Frontend Tests" not in text
        and "CodeQL Analysis" not in text
    )
    steps = job["steps"]
    assert all(
        re.fullmatch(r"[^@]+@[0-9a-f]{40}", step["uses"])
        for step in steps
        if "uses" in step
    )
    assert {
        name
        for name in [
            "Dockerfile.codecs",
            "Dockerfile.security-libraries",
            "Dockerfile.libjxl",
            "Dockerfile.ffmpeg",
            "Dockerfile.gosu",
        ]
        if name in text
    } == {
        "Dockerfile.codecs",
        "Dockerfile.security-libraries",
        "Dockerfile.libjxl",
        "Dockerfile.ffmpeg",
        "Dockerfile.gosu",
    }


def test_original_baseline_is_executed_on_each_real_native_host_without_publication():
    text = (ROOT / ".github/workflows/alpine-native.yml").read_text()
    workflow = yaml.load(text, Loader=yaml.BaseLoader)
    job = workflow["jobs"]["original-baseline"]
    assert job["permissions"] == {"contents": "read", "actions": "read"}
    assert job["strategy"]["matrix"]["include"] == [
        {"arch": "amd64", "machine": "x86_64", "runner": "ubuntu-24.04"},
        {"arch": "arm64", "machine": "aarch64", "runner": "ubuntu-24.04-arm"},
    ]
    assert "scripts/prepare_alpine_baseline.py" in text
    assert "scripts/compare_alpine_baseline.py" in text
    assert "scripts/candidate_image.py verify-loaded" in text
    assert "git diff --exit-code" in text
    assert "Dockerfile.baseline" in text
    assert "setup-qemu" not in text and "docker push" not in text
    assert all(
        re.fullmatch(r"[^@]+@[0-9a-f]{40}", s["uses"])
        for s in job["steps"]
        if "uses" in s
    )


def test_current_package_is_paired_after_signing_without_historical_substitution():
    workflow = yaml.load(
        (ROOT / ".github/workflows/alpine-native.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    job = workflow["jobs"]["native-components"]
    assert job["permissions"] == {"contents": "read", "actions": "read"}
    steps = job["steps"]
    signing = next(
        i
        for i, step in enumerate(steps)
        if "Dockerfile.packages" in step.get("run", "")
    )
    pairing = next(
        i
        for i, step in enumerate(steps)
        if "scripts/compare_alpine_baseline.py" in step.get("run", "")
    )
    assert signing < pairing
    run = "\n".join(step.get("run", "") for step in steps[signing + 1 : pairing + 1])
    assert "--native-zip" not in run
    assert 'paired-packages="$RUNNER_TEMP/alpine-evidence/packages"' in run
    assert "verify-loaded" in run and "prepare_alpine_baseline.py" in run
    retained = [
        step
        for step in steps
        if step.get("with", {}).get("name", "").startswith("retained-current-original-")
    ]
    assert len(retained) == 1 and "always()" in retained[0]["if"]
