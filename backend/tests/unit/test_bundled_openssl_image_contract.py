"""MCP must execute the crypto check in the exact per-arch scanned OCI image."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/check_bundled_openssl.py"
TRIVY = "aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25"
UPLOAD = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"


@pytest.fixture
def checker():
    spec = importlib.util.spec_from_file_location("bundled_openssl", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def subject():
    identity = {
        "ManifestDigest": "sha256:" + "1" * 64,
        "ConfigDigest": "sha256:" + "2" * 64,
        "OS": "linux",
        "Architecture": "amd64",
        "RootFSDiffIDs": ["sha256:" + "3" * 64],
    }
    loaded = [
        {
            "Id": identity["ConfigDigest"],
            "Os": "linux",
            "Architecture": "amd64",
            "RootFS": {"Type": "layers", "Layers": identity["RootFSDiffIDs"]},
            "Config": {"User": "appuser:appgroup"},
        }
    ]
    return identity, loaded, "a" * 40


def test_subject_positive_control(checker, subject):
    identity, loaded, sha = subject
    checker.validate_subject(
        identity, loaded, sha, identity["ConfigDigest"], sha, "amd64"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "empty",
        "not-object",
        "manifest",
        "config",
        "os",
        "arch",
        "layers",
        "loaded-empty",
        "loaded-id",
        "loaded-arch",
        "loaded-layers",
        "root-user",
        "source-empty",
        "source-wrong",
        "expected-tag",
        "expected-other",
    ],
)
def test_subject_substitutions_fail_closed(checker, subject, mutation):
    identity, loaded, sha = subject
    expected = identity["ConfigDigest"]
    source = sha
    if mutation == "empty":
        identity = {}
    elif mutation == "not-object":
        identity = []
    elif mutation == "manifest":
        identity["ManifestDigest"] = "latest"
    elif mutation == "config":
        identity["ConfigDigest"] = ""
    elif mutation == "os":
        identity["OS"] = "windows"
    elif mutation == "arch":
        identity["Architecture"] = "arm64"
    elif mutation == "layers":
        identity["RootFSDiffIDs"] = []
    elif mutation == "loaded-empty":
        loaded = []
    elif mutation == "loaded-id":
        loaded[0]["Id"] = "sha256:" + "4" * 64
    elif mutation == "loaded-arch":
        loaded[0]["Architecture"] = "arm64"
    elif mutation == "loaded-layers":
        loaded[0]["RootFS"]["Layers"] = []
    elif mutation == "root-user":
        loaded[0]["Config"]["User"] = "root"
    elif mutation == "source-empty":
        source = ""
    elif mutation == "source-wrong":
        source = "b" * 40
    elif mutation == "expected-tag":
        expected = "mcp:latest"
    elif mutation == "expected-other":
        expected = "sha256:" + "4" * 64
    with pytest.raises(ValueError):
        checker.validate_subject(identity, loaded, source, expected, sha, "amd64")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "OpenSSL 4.0.1 9 Jun 2026",
        "OpenSSL 4.0.2 25 Aug 2026",
        "LibreSSL 4.0.3",
        "OpenSSL 3.5.8",
        "OpenSSL 4.0.3-dev",
        "OpenSSL 4.0.3 31 Feb 2026",
        "OpenSSL 4.0.3\nOpenSSL 4.0.1",
    ],
)
def test_runtime_library_identity_is_fail_closed(checker, text):
    with pytest.raises(ValueError):
        checker.validate_openssl(text)


@pytest.mark.parametrize("text", ["OpenSSL 4.0.3 29 Sep 2026", "OpenSSL 4.0.10"])
def test_fixed_openssl_positive_control(checker, text):
    checker.validate_openssl(text)


@pytest.mark.parametrize(("arch", "machine"), [("amd64", 62), ("arm64", 183)])
def test_native_extension_reports_architecture_and_hash(
    checker, tmp_path, arch, machine
):
    path = tmp_path / "_rust.abi3.so"
    path.write_bytes(
        b"\x7fELF\x02\x01\x01" + bytes(9) + struct.pack("<HH", 3, machine) + bytes(44)
    )
    report = checker.inspect_extension(path, arch)
    assert report["architecture"] == arch
    assert report["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert report["path"] == str(path)


@pytest.mark.parametrize("payload", [b"", b"not ELF", b"\x7fELF" + bytes(60)])
def test_native_extension_missing_or_wrong_library_fails(checker, tmp_path, payload):
    path = tmp_path / "_rust.abi3.so"
    path.write_bytes(payload)
    with pytest.raises(ValueError):
        checker.inspect_extension(path, "amd64")


def test_native_extension_wrong_architecture_fails(checker, tmp_path):
    path = tmp_path / "_rust.abi3.so"
    path.write_bytes(
        b"\x7fELF\x02\x01\x01" + bytes(9) + struct.pack("<HH", 3, 183) + bytes(44)
    )
    with pytest.raises(ValueError):
        checker.inspect_extension(path, "amd64")


@pytest.mark.parametrize(
    "mutation", ["root", "platform", "cryptography", "metadata", "openssl"]
)
def test_real_runtime_check_rejects_dangerous_mutants(checker, monkeypatch, mutation):
    import cryptography
    from cryptography.hazmat.backends.openssl.backend import backend

    monkeypatch.setattr(checker.os, "getuid", lambda: 1000)
    monkeypatch.setattr(checker.os, "getgid", lambda: 1000)
    monkeypatch.setattr(checker.platform, "machine", lambda: "x86_64")
    if mutation == "root":
        monkeypatch.setattr(checker.os, "getuid", lambda: 0)
    elif mutation == "platform":
        monkeypatch.setattr(checker.platform, "machine", lambda: "aarch64")
    elif mutation == "cryptography":
        monkeypatch.setattr(cryptography, "__version__", "50.0.1")
    elif mutation == "metadata":
        monkeypatch.setattr(checker, "version", lambda _: "50.0.1")
    elif mutation == "openssl":
        monkeypatch.setattr(backend, "openssl_version_text", lambda: "OpenSSL 4.0.2")
    with pytest.raises(ValueError):
        checker.check_runtime("amd64")


@pytest.mark.parametrize("contents", ["", "[]", "{broken", "null"])
def test_cli_denies_malformed_host_identity(tmp_path, contents):
    identity = tmp_path / "identity.json"
    identity.write_text(contents)
    loaded = tmp_path / "loaded.json"
    loaded.write_text("[]")
    source = tmp_path / "source.txt"
    source.write_text("a" * 40)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--identity",
            str(identity),
            "--loaded-image",
            str(loaded),
            "--source-sha",
            str(source),
            "--expected-image-id",
            "sha256:" + "2" * 64,
            "--expected-source-sha",
            "a" * 40,
            "--architecture",
            "amd64",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert json.loads(result.stdout)["status"] == "FAIL"


@pytest.mark.parametrize("mutation", [None, "identity", "source", "runtime"])
def test_main_requires_subject_binding_and_successful_runtime(
    checker, subject, tmp_path, monkeypatch, capsys, mutation
):
    identity, loaded, source = subject
    expected_image = identity["ConfigDigest"]
    identity_path = tmp_path / "identity.json"
    loaded_path = tmp_path / "loaded.json"
    source_path = tmp_path / "source.txt"
    identity_path.write_text(json.dumps({} if mutation == "identity" else identity))
    loaded_path.write_text(json.dumps(loaded))
    source_path.write_text("" if mutation == "source" else source)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--identity",
            str(identity_path),
            "--loaded-image",
            str(loaded_path),
            "--source-sha",
            str(source_path),
            "--expected-image-id",
            expected_image,
            "--expected-source-sha",
            source,
            "--architecture",
            "amd64",
        ],
    )
    calls = []

    def runtime(arch):
        calls.append(arch)
        if mutation == "runtime":
            raise ValueError("synthetic operation failure")
        return {"openssl": "OpenSSL 4.0.3", "synthetic_fixture": True}

    monkeypatch.setattr(checker, "check_runtime", runtime)
    code = checker.main()
    receipt = json.loads(capsys.readouterr().out)
    if mutation is None:
        assert code == 0 and receipt["status"] == "PASS"
        assert receipt["identity"] == identity and receipt["source_sha"] == source
        assert receipt["runtime"]["openssl"] == "OpenSSL 4.0.3"
        assert calls == ["amd64"]
    else:
        assert code == 1 and receipt["status"] == "FAIL"
        assert calls == (["amd64"] if mutation == "runtime" else [])


def runtime_commands(arch):
    return f"""set -euo pipefail
mkdir -p mcp-native-crypto
python scripts/candidate_image.py verify-archive /tmp/mcp-{arch}.oci.tar --expected '${{{{ needs.build-mcp-{arch}.outputs.digest }}}}'
python scripts/candidate_image.py runtime-config /tmp/mcp-{arch}.oci.tar --expected '${{{{ needs.build-mcp-{arch}.outputs.digest }}}}' --architecture {arch} > mcp-native-crypto/identity.json
test "$(skopeo --version)" = "skopeo version 1.13.3"
skopeo copy oci-archive:/tmp/mcp-{arch}.oci.tar docker-archive:/tmp/trivy-mcp-{arch}-scan.docker.tar
docker load --input /tmp/trivy-mcp-{arch}-scan.docker.tar
image_id=$(jq -er '.ConfigDigest' mcp-native-crypto/identity.json)
docker image inspect "$image_id" > mcp-native-crypto/loaded-image.json
python scripts/candidate_image.py verify-loaded mcp-native-crypto/identity.json --inspect mcp-native-crypto/loaded-image.json
printf '%s\\n' '${{{{ github.sha }}}}' > mcp-native-crypto/source-sha.txt
docker run --rm --pull never --read-only --network none --cap-drop ALL --security-opt no-new-privileges --mount "type=bind,source=$PWD/scripts/check_bundled_openssl.py,target=/check_bundled_openssl.py,readonly" --mount "type=bind,source=$PWD/mcp-native-crypto,target=/mcp-native-crypto,readonly" --entrypoint /usr/local/bin/python "$image_id" -I -B /check_bundled_openssl.py --identity /mcp-native-crypto/identity.json --loaded-image /mcp-native-crypto/loaded-image.json --source-sha /mcp-native-crypto/source-sha.txt --expected-image-id "$image_id" --expected-source-sha '${{{{ github.sha }}}}' --architecture {arch} > mcp-native-crypto/receipt.json"""


def check_workflow(workflow):
    for arch, runner in [("amd64", "ubuntu-24.04"), ("arm64", "ubuntu-24.04-arm")]:
        job_id = f"trivy-scan-mcp-{arch}"
        job = workflow["jobs"][job_id]
        assert job["runs-on"] == runner
        assert job["needs"] == [f"build-mcp-{arch}"]
        assert set(job) == {"name", "runs-on", "needs", "steps"}
        steps = job["steps"]
        assert len(steps) == 5
        assert steps[0] == {
            "uses": "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
        }
        assert steps[1] == {
            "uses": "actions/download-artifact@634f93cb2916e3fdff6788551b99b062d0335ce0",
            "with": {"name": f"mcp-{arch}-candidate", "path": "/tmp"},
        }
        assert set(steps[2]) == {"name", "run"}
        assert steps[2]["run"].strip() == runtime_commands(arch)
        assert steps[3] == {
            "uses": TRIVY,
            "with": {
                "input": f"/tmp/trivy-mcp-{arch}-scan.docker.tar",
                "format": "table",
                "exit-code": "1",
                "vuln-type": "os,library",
                "severity": "CRITICAL,HIGH",
            },
        }
        assert steps[4] == {
            "uses": UPLOAD,
            "if": "always()",
            "with": {
                "name": f"mcp-native-crypto-{arch}-${{{{ github.sha }}}}",
                "path": "mcp-native-crypto/",
                "retention-days": 14,
                "if-no-files-found": "error",
            },
        }
        attest = workflow["jobs"]["attest-image-candidates"]
        assert job_id in attest["needs"]
        assert f"needs.{job_id}.result == 'success'" in attest["if"]


@pytest.fixture
def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/build.yml").read_text())


def test_actual_workflow_checks_both_scanned_mcp_images(workflow):
    check_workflow(workflow)


@pytest.mark.parametrize("arch", ["amd64", "arm64"])
@pytest.mark.parametrize(
    "mutation",
    [
        "verify-archive",
        "runtime-config",
        "skopeo copy",
        "docker load",
        "image_id=",
        "docker image inspect",
        "verify-loaded",
        "source-sha.txt",
        "docker run",
        "--read-only",
        "--network none",
        "/usr/local/bin/python",
        "--expected-image-id",
        "--expected-source-sha",
        "--architecture",
        "ignore-failure",
        "conditional",
        "scan",
    ],
)
def test_per_arch_gate_rejects_removed_or_weakened_steps(workflow, arch, mutation):
    changed = copy.deepcopy(workflow)
    steps = changed["jobs"][f"trivy-scan-mcp-{arch}"]["steps"]
    # Establish the baseline first: a broken baseline must not make mutants pass.
    check_workflow(workflow)
    if mutation == "conditional":
        steps[2]["if"] = "false"
    elif mutation == "scan":
        steps[3]["with"]["exit-code"] = "0"
    elif mutation == "ignore-failure":
        steps[2]["run"] += " || true"
    else:
        lines = steps[2]["run"].splitlines()
        steps[2]["run"] = "\n".join(line for line in lines if mutation not in line)
        assert (
            steps[2]["run"]
            != workflow["jobs"][f"trivy-scan-mcp-{arch}"]["steps"][2]["run"]
        )
    with pytest.raises(AssertionError):
        check_workflow(changed)
