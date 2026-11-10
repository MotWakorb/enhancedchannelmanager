"""Dev-targeted builds must not silently ship latest-channel runtime metadata."""

import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
@pytest.mark.parametrize(
    "ref,base_ref,expected",
    [
        ("refs/heads/dev", "", "dev"),
        ("refs/pull/1064/merge", "dev", "dev"),
        ("refs/heads/main", "", "latest"),
        ("refs/pull/1064/merge", "main", "latest"),
    ],
)
def test_native_candidate_channel_matches_dev_push_or_pull_request_target(
    architecture, ref, base_ref, expected
):
    jobs = yaml.safe_load((ROOT / ".github/workflows/build.yml").read_text())["jobs"]
    preparation = next(
        step
        for step in jobs[f"build-{architecture}"]["steps"]
        if step.get("id") == "version"
    )
    commands = preparation["run"].splitlines()
    selection = next(line for line in commands if "channel=dev" in line)
    selection = selection.replace("${{ github.ref }}", ref).replace(
        "${{ github.base_ref }}", base_ref
    )
    result = subprocess.run(
        ["bash", "-euc", selection + '\nprintf "%s" "$channel"'],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == expected
    build = next(
        step
        for step in jobs[f"build-{architecture}"]["steps"]
        if step.get("name", "").startswith("Build immutable ECM")
    )
    assert (
        "RELEASE_CHANNEL=${{ steps.version.outputs.channel }}"
        in build["with"]["build-args"]
    )
