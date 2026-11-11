"""Real native Python application gates cannot masquerade as whole-image approval."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_native_application_uses_hash_bound_wheels_and_the_unmodified_full_gate():
    workflow = yaml.load(
        (ROOT / ".github/workflows/alpine-python.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["application"]
    assert job["strategy"]["matrix"]["include"] == [
        {"arch": "amd64", "machine": "x86_64", "runner": "ubuntu-24.04"},
        {"arch": "arm64", "machine": "aarch64", "runner": "ubuntu-24.04-arm"},
    ]
    assert "not whole-image acceptance" in job["name"]
    steps = job["steps"]
    assert all(
        re.fullmatch(r"[^@]+@[0-9a-f]{40}", step["uses"])
        for step in steps
        if "uses" in step
    )
    commands = "\n".join(step.get("run", "") for step in steps)
    assert "prepare_musllinux_wheels.py" in commands
    assert "--network none" in commands and "TMPDIR=/proof/results" in commands
    assert (
        "--subset" not in commands
        and "setup-qemu" not in commands
        and "docker push" not in commands
    )
    dockerfile = (ROOT / "docker/alpine-native/Dockerfile.backend-test").read_text()
    assert "--require-hashes" in dockerfile
    assert "inventory_musllinux_wheels.py --verify" in dockerfile
    assert "--site-packages /proof/venv/lib/python3.12/site-packages" in dockerfile
    assert 'CMD ["bash", "scripts/backend-gate.sh"]' in dockerfile
    assert "test ! -e /opt/venv" in dockerfile
