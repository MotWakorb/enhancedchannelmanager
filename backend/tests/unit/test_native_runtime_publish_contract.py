"""Dogfood the actual build workflow, including dangerous per-architecture mutations.

Run: python -m pytest --no-cov tests/unit/test_native_runtime_publish_contract.py
Critical shell steps deliberately have a small canonical command contract: merely
mentioning a verifier in an echo/comment/ignored failure is not verification.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
TRIVY = "aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25"
DOWNLOAD = "actions/download-artifact@634f93cb2916e3fdff6788551b99b062d0335ce0"
UPLOAD = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
CHECKOUT = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"


def runtime_commands(arch):
    return f"""set -euo pipefail
mkdir -p native-evidence
python scripts/candidate_image.py verify-archive /tmp/ecm-{arch}.oci.tar --expected '${{{{ needs.build-{arch}.outputs.digest }}}}'
python scripts/candidate_image.py runtime-config /tmp/ecm-{arch}.oci.tar --expected '${{{{ needs.build-{arch}.outputs.digest }}}}' --architecture {arch} > native-evidence/identity.json
test "$(skopeo --version)" = "skopeo version 1.13.3"
skopeo copy oci-archive:/tmp/ecm-{arch}.oci.tar docker-archive:/tmp/ecm-{arch}.runtime.docker.tar:ecm-native-{arch}:candidate
docker load --input /tmp/ecm-{arch}.runtime.docker.tar
image_id=$(jq -er '.ConfigDigest' native-evidence/identity.json)
docker image inspect "$image_id" > native-evidence/loaded-image.json
python scripts/candidate_image.py verify-loaded native-evidence/identity.json --inspect native-evidence/loaded-image.json
python scripts/native_image_gate.py --image "$image_id" > native-evidence/runtime.json
jq -n --arg sha '${{{{ github.sha }}}}' --slurpfile identity native-evidence/identity.json '{{sha:$sha,identity:$identity[0]}}' > native-evidence/subject.json"""


def scan_commands(arch):
    return f"""set -euo pipefail
mkdir -p native-scan
python scripts/candidate_image.py verify-archive /tmp/ecm-{arch}.oci.tar --expected '${{{{ needs.build-{arch}.outputs.digest }}}}'
python scripts/candidate_image.py runtime-config /tmp/ecm-{arch}.oci.tar --expected '${{{{ needs.build-{arch}.outputs.digest }}}}' --architecture {arch} > native-scan/identity.json
test "$(skopeo --version)" = "skopeo version 1.13.3"
skopeo copy oci-archive:/tmp/ecm-{arch}.oci.tar docker-archive:/tmp/trivy-ecm-{arch}-scan.docker.tar
printf '%s\\n' '${{{{ github.sha }}}}' > native-scan/source-sha.txt"""


def _lines(text):
    return [line.strip() for line in text.strip().splitlines() if line.strip()]


def check_contract(workflow):
    jobs = workflow["jobs"]
    attest = jobs["attest-image-candidates"]
    expected_conditions = [
        "always() && github.event_name == 'push'",
        "&& (github.ref == 'refs/heads/dev' || github.ref == 'refs/heads/main')",
        "&& needs.build-amd64.result == 'success'",
        "&& needs.build-arm64.result == 'success'",
        "&& needs.build-mcp-amd64.result == 'success'",
        "&& needs.build-mcp-arm64.result == 'success'",
        "&& needs.trivy-scan.result == 'success'",
        "&& needs.trivy-scan-arm64.result == 'success'",
        "&& needs.trivy-scan-mcp-amd64.result == 'success'",
        "&& needs.trivy-scan-mcp-arm64.result == 'success'",
        "&& needs.native-runtime-amd64.result == 'success'",
        "&& needs.native-runtime-arm64.result == 'success'",
        "&& needs.security-scan-mcp.result == 'success'",
    ]
    assert " ".join(attest["if"].split()) == " ".join(expected_conditions)
    for arch, runner, scan_runner, scan_id in (
        ("amd64", "ubuntu-latest", "ubuntu-24.04", "trivy-scan"),
        ("arm64", "ubuntu-24.04-arm", "ubuntu-24.04-arm", "trivy-scan-arm64"),
    ):
        job_id = f"native-runtime-{arch}"
        job = jobs[job_id]
        assert set(job) == {"name", "runs-on", "needs", "permissions", "steps"}
        assert job_id in attest["needs"]
        assert f"needs.{job_id}.result == 'success'" in attest["if"]
        assert job["runs-on"] == runner == jobs[f"build-{arch}"]["runs-on"]
        assert job["needs"] == [f"build-{arch}"]
        assert job["permissions"] == {"contents": "read"}
        assert "if" not in job
        assert not job.get("continue-on-error") and not job.get("env")
        steps = job["steps"]
        assert len(steps) == 4
        assert steps[0] == {"uses": CHECKOUT}
        assert steps[1]["uses"] == DOWNLOAD
        assert steps[1]["with"] == {"name": f"ecm-{arch}-candidate", "path": "/tmp"}
        assert _lines(steps[2]["run"]) == _lines(runtime_commands(arch))
        assert steps[3]["uses"] == UPLOAD and steps[3]["if"] == "always()"
        assert steps[3]["with"]["path"] == "native-evidence/"
        assert steps[3]["with"]["if-no-files-found"] == "error"
        assert (
            steps[3]["with"]["name"] == f"native-runtime-{arch}-${{{{ github.sha }}}}"
        )
        for step in steps:
            assert set(step) <= {"uses", "with", "name", "run", "if"}
            assert not step.get("continue-on-error")
            assert not step.get("env")
        assert "if" not in steps[2]

        scan = jobs[scan_id]
        assert set(scan) == {"name", "runs-on", "needs", "permissions", "steps"}
        assert scan["runs-on"] == scan_runner
        assert scan["needs"] == [f"build-{arch}"]
        assert scan["permissions"] == {"contents": "read"}
        assert not scan.get("continue-on-error") and not scan.get("if")
        assert not scan.get("env")
        steps = scan["steps"]
        assert steps[0] == {"uses": CHECKOUT}
        assert steps[1]["uses"] == DOWNLOAD
        assert steps[1]["with"] == {"name": f"ecm-{arch}-candidate", "path": "/tmp"}
        assert _lines(steps[2]["run"]) == _lines(scan_commands(arch))
        scans = [s for s in steps if s.get("uses") == TRIVY]
        assert len(scans) == 2
        for s in scans:
            assert "if" not in s
            settings = s["with"]
            assert settings["input"] == f"/tmp/trivy-ecm-{arch}-scan.docker.tar"
            assert settings["version"] == "v0.76.1"
            assert settings["vuln-type"] == "os,library"
            assert settings["scanners"] == "vuln"
            assert settings["ignore-unfixed"] is False
            assert settings["cache-dir"] == "${{ github.workspace }}/.cache/trivy"
            assert set(settings) <= {
                "input",
                "version",
                "vuln-type",
                "scanners",
                "ignore-unfixed",
                "cache-dir",
                "format",
                "exit-code",
                "severity",
                "output",
                "list-all-pkgs",
            }
        collect, block = scans
        assert collect["with"]["format"] == "json"
        assert collect["with"]["exit-code"] == "0"
        assert collect["with"]["severity"] == "UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL"
        assert collect["with"]["list-all-pkgs"] == "true"
        assert collect["with"]["output"] == "native-scan/findings.json"
        assert block["with"]["format"] == "table"
        assert block["with"]["exit-code"] == "1"
        assert block["with"]["severity"] == "CRITICAL,HIGH"
        evidence = next(
            s for s in steps if s.get("name") == "Validate complete scan evidence"
        )
        assert (
            _lines(evidence["run"])
            == _lines(f"""set -euo pipefail
trivy version --cache-dir "${{{{ github.workspace }}}}/.cache/trivy" --format json > native-scan/scanner.json
python scripts/check_native_image_scan.py --report native-scan/findings.json --identity native-scan/identity.json --scanner-identity native-scan/scanner.json --architecture {arch}""")
        )
        assert steps.index(collect) < steps.index(evidence) < steps.index(block)
        assert steps[-1]["uses"] == UPLOAD and steps[-1]["if"] == "always()"
        assert steps[-1]["with"]["path"] == "native-scan/"
        assert steps[-1]["with"]["if-no-files-found"] == "error"
        assert steps[-1]["with"]["name"] == f"native-scan-{arch}-${{{{ github.sha }}}}"
        for s in steps:
            assert set(s) <= {"uses", "with", "name", "run", "if"}
            assert not s.get("continue-on-error") and not s.get("env")
            assert s is steps[-1] or "if" not in s
    assert workflow.get("env") == {"REGISTRY": "ghcr.io"}
    assert "defaults" not in workflow


@pytest.fixture
def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/build.yml").read_text())


def test_actual_build_workflow_enforces_native_candidate_contract(workflow):
    check_contract(workflow)
    assert not list(ROOT.glob(".trivyignore*"))
    assert not list(ROOT.glob("trivy.y*ml"))
    assert not list(ROOT.glob(".trivy.y*ml"))


@pytest.mark.parametrize("arch", ["amd64", "arm64"])
@pytest.mark.parametrize(
    "mutation",
    [
        "job",
        "needs",
        "success",
        "runner",
        "archive",
        "digest",
        "verify",
        "config",
        "loaded",
        "pull",
        "rebuild",
        "warning",
        "continue",
        "conditional",
        "conditionalboolean",
        "or_success",
        "shell",
        "registry_auth",
    ],
)
def test_native_contract_rejects_unsafe_runtime_mutations(workflow, arch, mutation):
    changed = copy.deepcopy(workflow)
    jobs = changed["jobs"]
    job_id = f"native-runtime-{arch}"
    job = jobs[job_id]
    step = job["steps"][2]
    if mutation == "job":
        del jobs[job_id]
    elif mutation == "needs":
        jobs["attest-image-candidates"]["needs"].remove(job_id)
    elif mutation == "success":
        jobs["attest-image-candidates"]["if"] = jobs["attest-image-candidates"][
            "if"
        ].replace(f"&& needs.{job_id}.result == 'success'", "")
    elif mutation == "runner":
        job["runs-on"] = "ubuntu-24.04" if arch == "arm64" else "ubuntu-24.04-arm"
    elif mutation == "archive":
        job["steps"][1]["with"]["name"] = "ecm-wrong-candidate"
    elif mutation == "continue":
        step["continue-on-error"] = True
    elif mutation == "conditional":
        step["if"] = "false"
    elif mutation == "conditionalboolean":
        step["if"] = False
    elif mutation == "or_success":
        jobs["attest-image-candidates"]["if"] += " || true"
    elif mutation == "shell":
        step["shell"] = "true {0}"
    elif mutation == "registry_auth":
        job["permissions"]["packages"] = "write"
    else:
        other = "arm64" if arch == "amd64" else "amd64"
        replacements = {
            "digest": (
                f"needs.build-{arch}.outputs.digest",
                f"needs.build-{other}.outputs.digest",
            ),
            "verify": (
                "python scripts/candidate_image.py verify-archive",
                "echo omitted-verify",
            ),
            "config": ("runtime-config", "digest"),
            "loaded": (
                "python scripts/candidate_image.py verify-loaded",
                "echo unchecked",
            ),
            "pull": (
                'docker image inspect "$image_id"',
                "docker pull ghcr.io/example/ecm:dev",
            ),
            "rebuild": ("docker load --input", "docker build . #"),
            "warning": (
                "python scripts/native_image_gate.py",
                "echo python scripts/native_image_gate.py",
            ),
        }
        old, new = replacements[mutation]
        assert old in step["run"]
        step["run"] = step["run"].replace(old, new)
    assert changed != workflow
    with pytest.raises((AssertionError, KeyError)):
        check_contract(changed)


@pytest.mark.parametrize("arch", ["amd64", "arm64"])
@pytest.mark.parametrize(
    "mutation",
    [
        "unfixed",
        "severity",
        "ignore",
        "vex",
        "continue",
        "empty",
        "inventory",
        "scanner",
        "validate",
        "warning",
        "archive",
        "config",
        "conditionalboolean",
        "ignorecollect",
        "severitycollect",
        "globalenv",
        "defaultshell",
    ],
)
def test_native_contract_rejects_unsafe_scan_mutations(workflow, arch, mutation):
    changed = copy.deepcopy(workflow)
    job = changed["jobs"]["trivy-scan" if arch == "amd64" else "trivy-scan-arm64"]
    collect, block = [s for s in job["steps"] if s.get("uses") == TRIVY]
    if mutation == "unfixed":
        block["with"]["ignore-unfixed"] = True
    elif mutation == "severity":
        block["with"]["severity"] = "CRITICAL"
    elif mutation in {"ignore", "vex"}:
        block["with"]["trivyignores" if mutation == "ignore" else "vex"] = (
            "suppress.json"
        )
    elif mutation == "continue":
        block["continue-on-error"] = True
    elif mutation == "empty":
        collect["with"]["exit-code"] = "1"
    elif mutation == "inventory":
        del collect["with"]["list-all-pkgs"]
    elif mutation == "archive":
        job["steps"][1]["with"]["name"] = "ecm-wrong-candidate"
    elif mutation == "config":
        job["env"] = {"TRIVY_IGNORE_UNFIXED": "true"}
    elif mutation == "conditionalboolean":
        block["if"] = False
    elif mutation == "ignorecollect":
        collect["with"]["ignore-unfixed"] = True
    elif mutation == "severitycollect":
        collect["with"]["severity"] = "HIGH,CRITICAL"
    elif mutation == "globalenv":
        changed["env"]["TRIVY_VEX"] = "suppressed.json"
    elif mutation == "defaultshell":
        changed["defaults"] = {"run": {"shell": "true {0}"}}
    else:
        step = next(
            s
            for s in job["steps"]
            if s.get("name") == "Validate complete scan evidence"
        )
        old = (
            "trivy version"
            if mutation == "scanner"
            else "python scripts/check_native_image_scan.py"
        )
        assert old in step["run"]
        step["run"] = step["run"].replace(old, "echo warning-only")
    assert changed != workflow
    with pytest.raises((AssertionError, KeyError)):
        check_contract(changed)
