"""Disposable Docker CLI fixtures test the gate, not native image compatibility."""

import asyncio
import hashlib
import importlib.util
import json
import os
import re
import ssl
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
GATE = ROOT / "scripts/native_image_gate.py"
IMAGE = "sha256:" + "a" * 64


@pytest.fixture
def docker_fixture(tmp_path):
    executable = tmp_path / "docker-fixture"
    calls = tmp_path / "calls.jsonl"
    resources = tmp_path / "resources.json"
    resources.write_text(
        json.dumps(
            {"volumes": ["existing-user-config"], "containers": ["existing-user-app"]}
        )
    )
    executable.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json, os, pathlib, sys, time
args = sys.argv[1:]
case = os.environ.get("GATE_FIXTURE_CASE", "pass")
with open(os.environ["GATE_FIXTURE_CALLS"], "a") as output:
    output.write(json.dumps(args) + "\n")
resource_path = pathlib.Path(os.environ["GATE_FIXTURE_RESOURCES"])
resources = json.loads(resource_path.read_text())
if args[:2] == ["volume", "create"]:
    resources["volumes"].append(args[-1])
elif args[:2] == ["volume", "rm"]:
    resources["volumes"].remove(args[-1])
elif args[0] == "run":
    resources["containers"].append(args[args.index("--name") + 1])
elif args[0] == "rm":
    resources["containers"].remove(args[-1])
resource_path.write_text(json.dumps(resources))
image = "sha256:" + "a" * 64
health = ["CMD-SHELL", "/opt/venv/bin/python -c 'health()' || exit 1"]
if args[:2] == ["image", "inspect"]:
    if case == "image_blank":
        sys.exit(0)
    print(json.dumps([{"Id": image if case != "wrong_image" else "sha256:" + "b" * 64,
                       "Architecture": "amd64", "Os": "linux",
                       "Config": {"Entrypoint": ["/app/entrypoint.sh"],
                                  "Healthcheck": {"Test": health if case != "bad_healthcheck" else ["CMD", "python", "health"]}}}]))
elif args[:2] == ["container", "inspect"]:
    print(json.dumps([{"Image": image, "State": {"Running": True}}]))
elif args[:2] == ["volume", "create"]:
    if args[-1].endswith("-1571-config"):
        if case == "volume_create_error":
            sys.exit(9)
        if case == "volume_create_timeout":
            time.sleep(3)
    print(args[-1])
elif args[0] == "run" and "/native-image/runtime_checks.py" in args:
    if case == "runtime_missing_binary":
        sys.exit(127)
    if case == "runtime_blank":
        sys.exit(0)
    if case == "runtime_malformed":
        print("not json")
        sys.exit(0)
    checks = json.loads(pathlib.Path(os.environ["GATE_FIXTURE_CONTRACT"]).read_text())
    if case == "runtime_incomplete":
        checks.pop()
    missing = {
        "runtime_missing_system_ca": "system_default_ca_trust",
        "runtime_missing_certifi_ca": "certifi_ca_trust",
        "runtime_missing_hls_probe": "aes_hls_backend_relay_probe",
        "runtime_missing_hls_preview": "aes_hls_backend_relay_previews",
        "runtime_missing_inputs": "software_input_format_support",
        "runtime_missing_prober_ts": "production_prober_ts",
        "runtime_missing_prober_hls": "production_prober_aes_hls",
        "runtime_missing_prober_cancel": "production_prober_cancellation",
        "runtime_missing_rare_decode": "synthetic_rare_input_decode",
        "runtime_missing_sources": "native_source_and_artifact_integrity",
    }
    if case in missing and missing[case] in checks:
        checks.remove(missing[case])
    print(json.dumps({"status": "FAIL" if case == "runtime_fail" else "PASS",
                      "checks": checks, "metadata": {"synthetic_fixture": True},
                      "failures": {"contradictory_failure": "bad"} if case == "runtime_inconsistent" else {}}))
    if case == "runtime_nonzero":
        sys.exit(7)
elif args[0] == "exec" and "/native-image/bootstrap_checks.py" in args:
    uid = int(args[args.index("--uid") + 1])
    gid = int(args[args.index("--gid") + 1])
    if case == "bootstrap_blank":
        sys.exit(0)
    print(json.dumps({"status": "PASS", "uid": 0 if case == "wrong_identity" else uid,
                      "gid": gid, "phase": args[args.index("--phase") + 1]}))
    if case == "bootstrap_nonzero":
        sys.exit(7)
elif args[0] == "rm" and case == "cleanup_failure":
    sys.exit(9)
else:
    print("owned fixture resource")
"""
    )
    executable.chmod(0o700)
    environment = {
        **os.environ,
        "GATE_FIXTURE_CALLS": str(calls),
        "GATE_FIXTURE_CONTRACT": str(ROOT / "tests/native_image/checks.json"),
        "GATE_FIXTURE_RESOURCES": str(resources),
    }

    def invoke(case="pass", image=IMAGE, docker=None, report=None, gate=None):
        extra = ["--report", str(report)] if report else []
        result = subprocess.run(
            [
                sys.executable,
                str(gate or GATE),
                "--image",
                image,
                "--docker",
                str(docker or executable),
                "--startup-timeout",
                "0.1",
                *extra,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env={**environment, "GATE_FIXTURE_CASE": case},
        )
        recorded = (
            [json.loads(line) for line in calls.read_text().splitlines()]
            if calls.exists()
            else []
        )
        return result, recorded

    return invoke


def copy_gate(tmp_path, old, new):
    """Mutate a disposable mirror only; keep the real source untouched."""
    gate = tmp_path / "mirror/scripts/native_image_gate.py"
    gate.parent.mkdir(parents=True)
    source = GATE.read_text()
    assert source.count(old) == 1
    gate.write_text(source.replace(old, new, 1))
    contract = gate.parents[1] / "tests/native_image/checks.json"
    contract.parent.mkdir(parents=True)
    contract.write_text((ROOT / "tests/native_image/checks.json").read_text())
    return gate


@pytest.mark.parametrize("case", ["runtime_nonzero", "bootstrap_nonzero"])
def test_nonzero_exit_rejected_with_complete_pass_json(docker_fixture, case):
    result, _ = docker_fixture(case)
    assert result.returncode != 0, result.stdout
    assert not result.stdout.strip()
    assert "exited 7" in result.stderr
    assert "incomplete" not in result.stderr and "malformed" not in result.stderr


@pytest.mark.parametrize("case", ["runtime_nonzero", "bootstrap_nonzero"])
def test_complete_pass_fixture_exposes_status_check_removal(
    docker_fixture, tmp_path, case
):
    mutant = copy_gate(tmp_path, "if result.returncode:", "if False:")
    result, _ = docker_fixture(case, gate=mutant)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "PASS"
    assert report["runtime"]["checks"] == json.loads(
        (ROOT / "tests/native_image/checks.json").read_text()
    )
    assert report["runtime"]["failures"] == {}
    assert [(item["uid"], item["gid"]) for item in report["bootstrap"]] == [
        (1000, 1000),
        (1571, 1572),
    ]


@pytest.mark.parametrize("case", ["volume_create_error", "volume_create_timeout"])
def test_volume_create_side_effect_failure_cleans_only_owned_resources(
    docker_fixture, tmp_path, case
):
    gate = None
    if case == "volume_create_timeout":
        # Shorten only the disposable copy's CLI timeout, not the real gate.
        gate = copy_gate(tmp_path, "timeout: float = 60", "timeout: float = 1")
    result, calls = docker_fixture(case, gate=gate)
    assert result.returncode != 0 and not result.stdout.strip()
    assert ("timed out" if case.endswith("timeout") else "exited 9") in result.stderr
    created = [call[-1] for call in calls if call[:2] == ["volume", "create"]]
    removed = [call[-1] for call in calls if call[:2] == ["volume", "rm"]]
    assert len(created) == 2 and created[1].endswith("-1571-config")
    assert removed == list(reversed(created)), (
        "the failed create's side effect was not cleaned up"
    )
    started = [call[call.index("--name") + 1] for call in calls if call[0] == "run"]
    stopped = [call[-1] for call in calls if call[0] == "rm"]
    assert len(started) == 2 and set(stopped) == set(started)
    assert all(name.startswith("ecm-native-gate-") for name in removed + stopped)
    assert json.loads((tmp_path / "resources.json").read_text()) == {
        "volumes": ["existing-user-config"],
        "containers": ["existing-user-app"],
    }


def test_fixture_success_requires_runtime_and_both_bootstrap_lifecycles(docker_fixture):
    result, calls = docker_fixture()
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "PASS"
    assert report["image_id"] == IMAGE
    assert [(item["uid"], item["gid"]) for item in report["bootstrap"]] == [
        (1000, 1000),
        (1571, 1572),
    ]
    assert len([call for call in calls if call[0] == "restart"]) == 2
    assert len([call for call in calls if call[0] == "run"]) == 5
    for call in calls:
        assert call[0] not in {"build", "pull"}
        if call[0] == "run":
            assert call[call.index("--network") + 1] == "none"
            assert call[call.index("--pull") + 1] == "never"
            assert IMAGE in call
            assert any("target=/native-image,readonly" in part for part in call)
            if "/native-image/runtime_checks.py" in call:
                assert "-I" in call
    healthchecks = [call for call in calls if call[0] == "exec" and "/bin/sh" in call]
    assert len(healthchecks) == 6
    assert all(call[-1].startswith("/opt/venv/bin/python ") for call in healthchecks)
    removals = [
        call for call in calls if call[0] == "rm" or call[:2] == ["volume", "rm"]
    ]
    assert removals
    assert all(call[-1].startswith("ecm-native-gate-") for call in removals)


@pytest.mark.parametrize(
    "case",
    [
        "runtime_nonzero",
        "runtime_blank",
        "runtime_malformed",
        "runtime_incomplete",
        "runtime_fail",
        "wrong_image",
        "bad_healthcheck",
        "wrong_identity",
        "bootstrap_nonzero",
        "bootstrap_blank",
        "runtime_missing_binary",
        "cleanup_failure",
        "image_blank",
        "runtime_inconsistent",
        "runtime_missing_system_ca",
        "runtime_missing_certifi_ca",
        "runtime_missing_hls_probe",
        "runtime_missing_hls_preview",
        "runtime_missing_inputs",
        "runtime_missing_prober_ts",
        "runtime_missing_prober_hls",
        "runtime_missing_prober_cancel",
        "runtime_missing_rare_decode",
        "runtime_missing_sources",
    ],
)
def test_fixture_failures_never_emit_success(docker_fixture, case):
    result, _ = docker_fixture(case)
    assert result.returncode != 0
    assert not result.stdout.strip(), result.stdout
    assert "native image gate failed" in result.stderr.lower()


@pytest.mark.parametrize(
    "case", ["runtime_fail", "runtime_nonzero", "runtime_incomplete"]
)
def test_failure_evidence_is_explicitly_failed_not_success(
    docker_fixture, tmp_path, case
):
    path = tmp_path / "report.json"
    result, _ = docker_fixture(case, report=path)
    assert result.returncode != 0 and not result.stdout.strip()
    report = json.loads(path.read_text())
    assert report["status"] == report["runtime"]["status"] == "FAIL"


def test_missing_docker_fails_closed(docker_fixture, tmp_path):
    result, calls = docker_fixture(docker=tmp_path / "missing-docker")
    assert result.returncode != 0
    assert not result.stdout.strip()
    assert not calls


@pytest.mark.parametrize(
    "image", ["ecm:latest", "sha256:abc", "a" * 64, "sha256:" + "g" * 64, ""]
)
def test_only_full_immutable_image_ids_are_accepted(docker_fixture, image):
    result, calls = docker_fixture(image=image)
    assert result.returncode != 0
    assert not result.stdout.strip()
    assert not calls


@pytest.fixture
def runtime_checks():
    spec = importlib.util.spec_from_file_location(
        "native_runtime_checks", ROOT / "tests/native_image/runtime_checks.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("version", "accepted"),
    [
        ("getent (GNU libc) 2.44", True),
        ("getent (glibc-2.44-r9) 2.44", True),
        ("getent (Debian GLIBC 2.41-12+deb13u4) 2.41", True),
        ("BusyBox v1.38.0", False),
        ("getent (musl libc) 1.2.5", False),
        ("unrelated helper mentioning glibc", False),
    ],
)
def test_getent_checks_accept_recorded_gnu_libc_identities_only(
    runtime_checks, monkeypatch, version, accepted
):
    outputs = {
        (
            "getent",
            "passwd",
            "appuser",
        ): "appuser:x:1000:1000::/home/appuser:/bin/bash\n",
        ("getent", "group", "appuser"): "appuser:x:1000:\n",
        ("getent", "--version"): version + "\n",
    }
    monkeypatch.setattr(runtime_checks, "command", lambda *args: outputs[args])
    if accepted:
        assert runtime_checks.getent_checks() == {"getent": version}
    else:
        with pytest.raises(AssertionError):
            runtime_checks.getent_checks()


@pytest.mark.parametrize(
    ("version", "accepted"),
    [
        ("OpenSSL 4.0.3 29 Sep 2026", True),
        ("OpenSSL 4.0.1 9 Jun 2026", False),
        ("OpenSSL 4.0.2 25 Aug 2026", False),
        ("OpenSSL 3.5.3 16 Sep 2025", False),
        ("LibreSSL 4.0.3", False),
        ("OpenSSL 4.0.3-dev 29 Sep 2026", False),
        ("", False),
    ],
)
def test_native_python_operations_check_the_loaded_wheel_openssl(
    runtime_checks, monkeypatch, version, accepted
):
    from cryptography.hazmat.backends.openssl.backend import backend

    monkeypatch.setattr(backend, "openssl_version_text", lambda: version)
    if accepted:
        report = runtime_checks.python_operations()
        assert report["cryptography_openssl"]["loaded_version"] == version
        assert re.fullmatch(
            r"[0-9a-f]{64}", report["cryptography_openssl"]["extension_sha256"]
        )
    else:
        with pytest.raises(AssertionError, match="bundled OpenSSL"):
            runtime_checks.python_operations()


@pytest.mark.parametrize("suffix", ["", ".m3u8", ".ts", ".m4s", ".mp4", ".aac", ".vtt"])
def test_relay_assertion_accepts_only_the_expected_public_suffix(
    runtime_checks, suffix
):
    runtime_checks.assert_relay_url(
        "http://127.0.0.1:1234/resource/" + "a" * 32 + suffix, suffix
    )


@pytest.mark.parametrize(
    "url,suffix",
    [
        ("http://127.0.0.1:1234/resource/" + "a" * 32, ".ts"),
        ("http://127.0.0.1:1234/resource/" + "a" * 32 + ".mp4", ".ts"),
        ("http://127.0.0.1:1234/resource/" + "a" * 32 + ".key", ""),
        ("http://127.0.0.1:1234/resource/" + "a" * 32 + ".ts?token=private", ".ts"),
        ("http://user:password@127.0.0.1:1234/resource/" + "a" * 32 + ".ts", ".ts"),
        ("http://127.0.0.1:1234/provider/secret.ts", ".ts"),
        ("https://media.example/resource/" + "a" * 32 + ".ts", ".ts"),
    ],
)
def test_relay_assertion_rejects_missing_forged_or_exposed_provider_details(
    runtime_checks, url, suffix
):
    with pytest.raises(AssertionError):
        runtime_checks.assert_relay_url(url, suffix)


@pytest.mark.parametrize("store", ["system_default_ca_trust", "certifi_ca_trust"])
@pytest.mark.parametrize(
    "anchors,passes", [([b"g1"], True), ([b"g1", b"g3"], False), ([], False)]
)
def test_trust_checks_require_g3_absent_and_g1_present(
    runtime_checks, monkeypatch, store, anchors, passes
):
    monkeypatch.setattr(
        runtime_checks, "_COMPROMISED_G3", hashlib.sha256(b"g3").hexdigest()
    )
    monkeypatch.setattr(
        runtime_checks, "_UNAFFECTED_G1", hashlib.sha256(b"g1").hexdigest()
    )
    calls = []

    def context(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            verify_mode=ssl.CERT_REQUIRED,
            check_hostname=True,
            get_ca_certs=lambda *, binary_form: anchors if binary_form else [],
        )

    monkeypatch.setattr(runtime_checks.ssl, "create_default_context", context)
    if passes:
        result = getattr(runtime_checks, store)()
        assert result[store]["compromised_g3_trusted"] is False
        assert result[store]["unaffected_g1_trusted"] is True
    else:
        with pytest.raises(AssertionError):
            getattr(runtime_checks, store)()
    if store == "system_default_ca_trust":
        assert calls == [{}], (
            "system trust must honor the image's default CA environment"
        )
    else:
        import certifi

        assert calls == [{"cafile": certifi.where()}]


@pytest.fixture
def software_inventory():
    # Deliberately independent of the implementation's required-name constants.
    decoders = [
        "h264",
        "hevc",
        "av1",
        "vp8",
        "vp9",
        "mpeg2video",
        "mpeg4",
        "mjpeg",
        "png",
        "rawvideo",
        "aac",
        "aac_latm",
        "ac3",
        "eac3",
        "mp2",
        "mp3",
        "opus",
        "vorbis",
        "flac",
        "pcm_s16le",
        "libcodec2",
        "libjxl",
        "librsvg",
        "sonic",
        "gsm",
        "gsm_ms",
    ]
    demuxers = [
        "mpegts",
        "hls",
        "dash",
        "mov",
        "matroska",
        "webm",
        "aac",
        "ac3",
        "eac3",
        "flac",
        "mp3",
        "ogg",
        "wav",
        "codec2",
        "codec2raw",
        "jpegxl_pipe",
        "svg_pipe",
        "libgme",
        "libopenmpt",
    ]
    protocols = [
        "file",
        "pipe",
        "http",
        "https",
        "tcp",
        "tls",
        "crypto",
        "udp",
        "rtp",
        "rtmp",
    ]
    return {
        "decoders": "Decoders:\n"
        + "".join(f" A....D {name} synthetic fixture\n" for name in decoders),
        "demuxers": "Demuxers:\n"
        + "".join(f" D  {name} synthetic fixture\n" for name in demuxers),
        "protocols": "Input:\n" + "\n".join(protocols) + "\nOutput:\nhttp\nhttps\n",
    }


def test_software_input_inventory_preserves_registration_not_experimental_decoding(
    runtime_checks, software_inventory
):
    result = runtime_checks.validate_software_inputs(**software_inventory)
    assert (
        result["software_input_format_support"]["sonic"]
        == "registered; experimental decoding not enabled or claimed"
    )


@pytest.mark.parametrize(
    "kind,name",
    [
        ("decoders", "libcodec2"),
        ("decoders", "libjxl"),
        ("decoders", "librsvg"),
        ("decoders", "sonic"),
        ("demuxers", "libgme"),
        ("demuxers", "libopenmpt"),
        ("decoders", "h264"),
        ("decoders", "aac"),
        ("demuxers", "hls"),
        ("protocols", "https"),
        ("protocols", "crypto"),
    ],
)
def test_software_input_inventory_rejects_missing_formats(
    runtime_checks, software_inventory, kind, name
):
    software_inventory[kind] = "\n".join(
        line
        for line in software_inventory[kind].splitlines()
        if name not in line.split()
    )
    # A descriptor, output protocol or unrelated mention cannot stand in for
    # an actual input decoder/demuxer registration.
    software_inventory[kind] += f"\n{name} descriptor only\n"
    with pytest.raises(AssertionError, match=name):
        runtime_checks.validate_software_inputs(**software_inventory)


def test_documented_gsm_and_rawvideo_inventory_equivalents(
    runtime_checks, software_inventory
):
    software_inventory["decoders"] = (
        software_inventory["decoders"]
        .replace(" gsm ", " libgsm ")
        .replace(" gsm_ms ", " libgsm_ms ")
    )
    runtime_checks.validate_software_inputs(**software_inventory)
    software_inventory["decoders"] = software_inventory["decoders"].replace(
        " A....D rawvideo synthetic fixture\n", ""
    ) + "".join(
        f" V....D {name} original registration\n" for name in ("v308", "v408", "v410")
    )
    with pytest.raises(AssertionError, match="rawvideo"):
        # Rawvideo is independently needed for the normal software pipeline.
        runtime_checks.validate_software_inputs(**software_inventory)


def test_software_inventory_is_read_from_real_decoder_not_descriptor_command(
    runtime_checks, software_inventory, monkeypatch
):
    calls = []

    def inventory(*args):
        calls.append(args)
        return software_inventory[args[-1].removeprefix("-")]

    monkeypatch.setattr(runtime_checks, "command", inventory)
    runtime_checks.software_input_format_support()
    assert calls == [
        ("ffmpeg", "-hide_banner", option)
        for option in ("-decoders", "-demuxers", "-protocols")
    ]


@pytest.mark.parametrize(
    "change", ["timeout", "protocols", "provider_url", "experimental"]
)
def test_production_prober_observer_rejects_argument_changes(runtime_checks, change):
    args = [
        "ffprobe",
        "-v",
        "error",
        "-protocol_whitelist",
        "http,tcp,crypto",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-timeout",
        "5000000",
        "http://127.0.0.1:1234/resource/" + "a" * 32 + ".ts",
    ]
    if change == "timeout":
        args[10] = "1"
    elif change == "protocols":
        args[4] = "http,tcp,crypto,file"
    elif change == "provider_url":
        args[-1] = "https://user:secret@media.example/private.ts"
    else:
        args[1:1] = ["-strict", "experimental"]
    with pytest.raises(AssertionError):
        runtime_checks.assert_prober_argv(args, 5)


@pytest.mark.asyncio
async def test_child_observer_tracks_owned_handle_kill_and_reap(
    runtime_checks, monkeypatch
):
    real_spawn = asyncio.create_subprocess_exec
    owned = []

    async def stand_in(*_args, **kwargs):
        # Self-test only: test ownership bookkeeping with our own Python child,
        # not a claim that a native ffprobe or an image was exercised here.
        process = await real_spawn(
            sys.executable, "-c", "import time; time.sleep(30)", **kwargs
        )
        owned.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", stand_in)
    async with runtime_checks.observe_prober_children(5) as records:
        args = [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "http,tcp,crypto",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-timeout",
            "5000000",
            "http://127.0.0.1:1234/resource/" + "a" * 32 + ".ts",
        ]
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        assert records[0]["process"] is owned[0] is process
        process.kill()
        await asyncio.wait_for(process.wait(), timeout=3)
    assert records[0]["kill_calls"] == 1 and records[0]["reaped"] is True
    assert process.returncode == -9


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ts", "hls", "cancel"])
async def test_real_prober_method_orchestration_with_synthetic_child(
    runtime_checks, monkeypatch, tmp_path, mode
):
    # Exercise the real backend method, relay, parser and cancellation branches.
    # Only the native binary is replaced in this self-test. The image gate does
    # NOT substitute ffprobe, and must still be run on the final verified image.
    (tmp_path / "source.ts").write_bytes(b"synthetic media")
    (tmp_path / "segment000.ts").write_bytes(b"synthetic encrypted segment")
    (tmp_path / "synthetic-aes.key").write_bytes(bytes(range(16)))
    base = "http://fixture.invalid"
    (tmp_path / "playlist.m3u8").write_text(
        f'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="{base}/synthetic-aes.key"\n#EXTINF:1.0,\nsegment000.ts\n#EXT-X-ENDLIST\n'
    )
    real_spawn = asyncio.create_subprocess_exec
    owned = []
    child = r"""
import json, re, sys, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
body = opener.open(sys.argv[1], timeout=10).read()
hls = body.startswith(b"#EXTM3U")
if hls:
    for url in re.findall(r'http://[^\s"]+', body.decode()):
        opener.open(url, timeout=10).read()
print(json.dumps({"streams": [
    {"codec_type": "video", "codec_name": "h264", "width": 128, "height": 72},
    {"codec_type": "audio", "codec_name": "aac", "channels": 1}],
    "format": {"format_name": "hls" if hls else "mpegts"}}))
"""

    async def stand_in(*args, **kwargs):
        process = await real_spawn(sys.executable, "-c", child, args[-1], **kwargs)
        owned.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", stand_in)
    if mode == "cancel":
        result = await runtime_checks.production_prober_cancellation(tmp_path, base)
        assert result["production_prober_cancellation"] == {
            "owned_children": 1,
            "pid": owned[0].pid,
            "kill_calls": 1,
            "reaped": True,
            "returncode": -9,
        }
    else:
        result = await runtime_checks.production_prober(
            tmp_path, base, hls=mode == "hls"
        )
        report = result[
            "production_prober_aes_hls" if mode == "hls" else "production_prober_ts"
        ]
        assert report["resolution"] == "128x72" and report["reaped"] is True
    assert len(owned) == 1 and owned[0].returncode is not None


@pytest.fixture
def rare_baseline_outputs():
    # Recorded published bdef595b metadata and first framemd5 frame per fixture.
    # Hashes below describe recorded output, but are not compared across versions.
    records = {
        "synthetic.c2": (
            {
                "format_name": "codec2",
                "codec_type": "audio",
                "codec_name": "codec2",
                "sample_rate": "8000",
                "channels": 1,
            },
            "#media_type 0: audio\n#sample_rate 0: 8000\n#channel_layout_name 0: mono\n0, 0, 0, 320, 640, d808fe1cfcaffb2e272060cdeb6ff4cb\n",
        ),
        "synthetic.jxl": (
            {
                "format_name": "jpegxl_pipe",
                "codec_type": "video",
                "codec_name": "jpegxl",
                "width": 16,
                "height": 16,
            },
            "#media_type 0: video\n#dimensions 0: 16x16\n0, 0, 0, 1, 768, 6c9880e69c622981d507f8d5f8b125ba\n",
        ),
        "synthetic.svg": (
            {
                "format_name": "svg_pipe",
                "codec_type": "video",
                "codec_name": "svg",
                "width": 16,
                "height": 16,
            },
            "#media_type 0: video\n#dimensions 0: 16x16\n0, 0, 0, 1, 1024, 99d9a4031d220dedc34031c24cc0f15e\n",
        ),
        "synthetic.vgm": (
            {
                "format_name": "libgme",
                "codec_type": "audio",
                "codec_name": "pcm_s16le",
                "sample_rate": "44100",
                "channels": 2,
            },
            "#media_type 0: audio\n#sample_rate 0: 44100\n#channel_layout_name 0: stereo\n0, 0, 0, 128, 512, f355be31dccb052dcc26deb9b6678989\n",
        ),
        "synthetic.mod": (
            {
                "format_name": "libopenmpt",
                "codec_type": "audio",
                "codec_name": "pcm_f32le",
                "sample_rate": "48000",
                "channels": 2,
            },
            "#media_type 0: audio\n#sample_rate 0: 48000\n#channel_layout_name 0: stereo\n0, 0, 0, 256, 1024, 0f343b0931126a20f133d67c2b018a3b\n",
        ),
    }
    return records


@pytest.fixture
def rare_process(runtime_checks, rare_baseline_outputs, monkeypatch):
    controls, calls = {}, []

    def run(args, **kwargs):
        calls.append(args)
        assert kwargs["check"] is False and kwargs["timeout"] == 30
        assert "-strict" not in args and "experimental" not in args
        path = Path(args[-1] if args[0] == "ffprobe" else args[args.index("-i") + 1])
        assert path.read_bytes() == runtime_checks.load_rare_inputs()[path.name]["data"]
        expected, frames = rare_baseline_outputs[path.name]
        if args[0] == "ffprobe":
            metadata = {
                "streams": [
                    {
                        key: value
                        for key, value in expected.items()
                        if key != "format_name"
                    }
                ],
                "format": {"format_name": expected["format_name"]},
            }
            if controls.get("file") == path.name:
                if controls.get("field") == "format_name":
                    metadata["format"]["format_name"] = "mpegts"
                elif controls.get("field"):
                    metadata["streams"][0][controls["field"]] = "wrong"
            return subprocess.CompletedProcess(args, 0, json.dumps(metadata), "")
        assert args[0] == "ffmpeg" and args[-3:] == ("-f", "framemd5", "-")
        mode = controls.get("decode")
        if mode == "empty":
            frames = "\n".join(
                line for line in frames.splitlines() if line.startswith("#")
            )
        elif mode == "zero_size":
            frames = frames.rsplit(",", 2)[0] + ", 0, " + "0" * 32 + "\n"
        elif mode == "wrong_kind":
            frames = frames.replace("#media_type 0: audio", "#media_type 0: video")
        return subprocess.CompletedProcess(
            args, 7 if mode == "nonzero" else 0, frames, "synthetic decoder error"
        )

    monkeypatch.setattr(runtime_checks.subprocess, "run", run)
    return controls, calls


def test_recorded_baseline_rare_fixture_json_is_integrity_checked(
    runtime_checks, rare_baseline_outputs
):
    fixtures = runtime_checks.load_rare_inputs()
    assert set(fixtures) == set(rare_baseline_outputs)
    assert [len(fixtures[name]["data"]) for name in rare_baseline_outputs] == [
        63,
        64,
        115,
        131,
        2108,
    ]
    assert all(
        fixtures[name]["expected"] == record[0]
        for name, record in rare_baseline_outputs.items()
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_file",
        "missing_fixture",
        "unknown_name",
        "wrong_hash",
        "wrong_bytes_and_hash",
        "wrong_expected",
        "wrong_provenance",
        "invalid_base64",
    ],
)
def test_rare_fixture_manifest_rejects_invalid_or_substituted_inputs(
    runtime_checks, tmp_path, mutation
):
    path = tmp_path / "rare_inputs.json"
    if mutation != "missing_file":
        document = json.loads(
            (ROOT / "tests/native_image/rare_inputs.json").read_text()
        )
        fixture = document["fixtures"]["synthetic.c2"]
        if mutation == "missing_fixture":
            del document["fixtures"]["synthetic.c2"]
        elif mutation == "unknown_name":
            document["fixtures"]["../synthetic.c2"] = document["fixtures"].pop(
                "synthetic.c2"
            )
        elif mutation == "wrong_hash":
            fixture["sha256"] = "0" * 64
        elif mutation == "wrong_bytes_and_hash":
            fixture["base64"] = "AA=="
            fixture["sha256"] = hashlib.sha256(b"\0").hexdigest()
        elif mutation == "wrong_expected":
            fixture["expected"]["codec_name"] = "h264"
        elif mutation == "wrong_provenance":
            document["provenance"]["generator_sha256"] = "0" * 64
        else:
            fixture["base64"] = "!" * 84
        path.write_text(json.dumps(document))
    with pytest.raises(AssertionError):
        runtime_checks.load_rare_inputs(path)


def test_recorded_baseline_metadata_and_frames_satisfy_real_decode_check(
    runtime_checks, rare_process
):
    _, calls = rare_process
    report = runtime_checks.synthetic_rare_input_decode()["synthetic_rare_input_decode"]
    assert len(report) == 5 and all(
        item["decoded_frames"] > 0 for item in report.values()
    )
    assert len(calls) == 10 and sum(args[0] == "ffprobe" for args in calls) == 5


@pytest.mark.parametrize("mode", ["nonzero", "empty", "zero_size", "wrong_kind"])
def test_rare_decode_rejects_nonzero_or_nonframe_success(
    runtime_checks, rare_process, mode
):
    controls, _ = rare_process
    controls["decode"] = mode
    with pytest.raises(AssertionError):
        runtime_checks.synthetic_rare_input_decode()


@pytest.mark.parametrize(
    "name,field",
    [
        ("synthetic.c2", "format_name"),
        ("synthetic.c2", "codec_name"),
        ("synthetic.c2", "channels"),
        ("synthetic.jxl", "width"),
        ("synthetic.svg", "height"),
        ("synthetic.vgm", "sample_rate"),
        ("synthetic.mod", "codec_name"),
    ],
)
def test_rare_decode_rejects_wrong_format_or_metadata(
    runtime_checks, rare_process, name, field
):
    controls, _ = rare_process
    controls.update(file=name, field=field)
    with pytest.raises(AssertionError):
        runtime_checks.synthetic_rare_input_decode()
