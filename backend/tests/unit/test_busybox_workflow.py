"""Native replacement proof is mandatory and cannot authorize publication."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_busybox_workflow_builds_and_installs_on_real_native_hosts_without_publication():
    text = (ROOT / ".github/workflows/alpine-busybox.yml").read_text()
    workflow = yaml.load(text, Loader=yaml.BaseLoader)
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["provider"]
    assert job["strategy"]["matrix"]["include"] == [
        {"arch": "amd64", "machine": "x86_64", "runner": "ubuntu-24.04"},
        {"arch": "arm64", "machine": "aarch64", "runner": "ubuntu-24.04-arm"},
    ]
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "prepare_busybox_cache.py" in commands
    assert "--file docker/alpine-busybox/Dockerfile " in commands
    assert "--file docker/alpine-busybox/Dockerfile.runtime " in commands
    assert "--build-context busybox-packages=" in commands
    assert "official-base-runtime.json" in commands
    assert "--network none" in commands
    assert 'tar -C "$RUNNER_TEMP/busybox-evidence" -czf' in commands
    assert any(step.get('with',{}).get('path') == '${{ runner.temp }}/busybox-native-${{ matrix.arch }}.tar.gz' for step in job['steps'])
    assert "${{ github.sha }}" in workflow["concurrency"]["group"]
    assert "not final-image acceptance" in job["name"]
    assert all(
        re.fullmatch(r"[^@]+@[a-f0-9]{40}", step["uses"])
        for step in job["steps"]
        if "uses" in step
    )
    assert (
        "setup-qemu" not in text
        and "docker push" not in text
        and "continue-on-error" not in text
    )
    assert any(
        step.get("if") == "always()" and "upload-artifact" in step.get("uses", "")
        for step in job["steps"]
    )


def test_runtime_checks_bind_signatures_installed_bytes_and_local_behavior_without_unused_packages():
    text = (ROOT / "docker/alpine-busybox/Dockerfile.runtime").read_text()
    assert "RUN --network=none" in text
    assert "install_busybox_provider.py --packages-dir" in text
    assert "probe_busybox_runtime.py --output-file" in text
    copies = [
        line
        for line in text.splitlines()
        if line.startswith("COPY --from=busybox-packages")
    ]
    assert all(
        " packages/" not in line and "provenance-inputs" not in line for line in copies
    )
    build = (ROOT / "docker/alpine-busybox/Dockerfile").read_text()
    assert build.count("RUN --network=none") == 2
    assert "build_busybox_provider.py check" in build
