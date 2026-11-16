"""vn6jt: exact transport-tool checks cannot follow a moving hosted image."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("filename", ["build.yml", "alpine-native.yml"])
def test_all_exact_skopeo_guards_use_supported_fixed_runner_family(filename):
    workflow = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text())
    guarded_jobs = 0
    for job_id, job in workflow["jobs"].items():
        commands = "\n".join(step.get("run", "") for step in job["steps"])
        if 'test "$(skopeo --version)" = "skopeo version 1.13.3"' not in commands:
            continue
        guarded_jobs += 1
        runner = job["runs-on"]
        runners = (
            [row["runner"] for row in job["strategy"]["matrix"]["include"]]
            if runner == "${{ matrix.runner }}" else [runner]
        )
        assert set(runners) <= {"ubuntu-24.04", "ubuntu-24.04-arm"}, job_id
    assert guarded_jobs == (6 if filename == "build.yml" else 2)
