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
