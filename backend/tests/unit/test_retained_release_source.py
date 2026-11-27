"""One hash-addressed, authenticated source input, never a partial generic cache."""

import hashlib
import importlib.util
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PIN = "93ac0c47def1693d0cf9869269c236d68e6ad9fe7456e1f8f2bb6fdec74f7123"
NAME = "openSUSE-release-20261105-4389.1.src.rpm"
GOOD = b"synthetic retained archive stand-in, not an RPM"
ORIGINS = [(NAME, "openSUSE")]


@pytest.fixture
def collector():
    spec = importlib.util.spec_from_file_location(
        "retained_release_collector", ROOT / "scripts/collect_native_sources.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_hash(path):
    data = path.read_bytes()
    return PIN if data == GOOD else hashlib.sha256(data).hexdigest()


def inventory(tmp_path, other=False):
    path = tmp_path / "inventory.tsv"
    text = (
        f"openSUSE-release\t20261105\t4389.1\tx86_64\topenSUSE\t{NAME}\tBSD-3-Clause\n"
    )
    if other:
        text += "zlib\t1.3.1\t3.3\tx86_64\topenSUSE\tzlib-1.3.1-3.3.src.rpm\tZlib\n"
    path.write_text(text)
    return path


def test_real_reviewed_archive_is_retained_unchanged_in_existing_cache():
    path = ROOT / "docker/native-cache" / PIN
    assert path.is_file() and not path.is_symlink()
    assert path.stat().st_size == 3391198
    assert hashlib.sha256(path.read_bytes()).hexdigest() == PIN


@pytest.mark.parametrize(
    "defect",
    [
        "corrupt",
        "missing",
        "wrong-name",
        "symlink",
        "directory",
        "wrong-arch",
        "wrong-vendor",
        "wrong-origin",
    ],
)
def test_retained_input_is_one_exact_regular_hash_addressed_tuple(
    collector, monkeypatch, tmp_path, defect
):
    path = tmp_path / PIN
    path.write_bytes(GOOD)
    monkeypatch.setattr(collector, "sha256", fake_hash)
    arch, origins = "x86_64", ORIGINS
    if defect == "corrupt":
        path.write_bytes(b"corrupt")
    elif defect == "missing":
        path.unlink()
    elif defect == "wrong-name":
        renamed = tmp_path / "unapproved.src.rpm"
        path.rename(renamed)
        path = renamed
    elif defect == "symlink":
        target = tmp_path / "target"
        path.rename(target)
        path.symlink_to(target)
    elif defect == "directory":
        path.unlink()
        path.mkdir()
        (path / PIN).write_bytes(GOOD)
        (path / "unapproved-extra.src.rpm").write_bytes(b"extra")
    elif defect == "wrong-arch":
        arch = "aarch64"
    elif defect == "wrong-vendor":
        origins = [(NAME, "http://packman.links2linux.de")]
    elif defect == "wrong-origin":
        origins = [("openSUSE-release-20261106-4391.1.src.rpm", "openSUSE")]
    with pytest.raises(ValueError, match="retained release source"):
        collector.retained_release_source(path, arch, origins)


def test_retained_cli_does_not_accept_an_additional_source(collector, tmp_path):
    with pytest.raises(SystemExit):
        collector.main(
            [
                "--arch",
                "x86_64",
                "--out",
                str(tmp_path / "out"),
                "--retained-release-source",
                str(tmp_path / PIN),
                str(tmp_path / "extra"),
            ]
        )


@pytest.mark.parametrize(
    "machine,expected",
    [
        ("x86_64", ["--retained-release-source", "/tmp/native-cache/" + PIN]),
        ("aarch64", []),
        ("riscv64", None),
    ],
)
def test_actual_docker_case_supplies_only_the_amd_handoff(machine, expected):
    text = (ROOT / "docker/native/Dockerfile.suse").read_text().split(" AS source-builder\n", 1)[1]
    prefix = text.split("RUN ", 1)[1].split("    && rpm -q", 1)[0]
    prefix = (
        prefix.replace("\\\n", " ")
        .replace('"$(uname -m)"', shlex.quote(machine))
        .strip()
    )
    probe = shlex.join(
        [sys.executable, "-c", "import json,sys; print(json.dumps(sys.argv[1:]))"]
    )
    result = subprocess.run(
        ["/bin/sh", "-ec", prefix + "; " + probe + ' "$@"'],
        capture_output=True,
        text=True,
        check=False,
    )
    if expected is None:
        assert result.returncode != 0
    else:
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == expected


def test_generator_contract_rejects_missing_or_arm_retained_handoff():
    spec = importlib.util.spec_from_file_location(
        "retained_release_sbom", ROOT / "scripts/generate_sbom.py"
    )
    sbom = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbom)
    text = (ROOT / "docker/native/Dockerfile.suse").read_text()
    sbom.validate_historical_suse_docker_contract(text, "Dockerfile.suse")
    for before, after in [
        ("set -- --retained-release-source /tmp/native-cache/" + PIN, "set --"),
        (
            "aarch64) set -- ;;",
            "aarch64) set -- --retained-release-source /tmp/native-cache/"
            + PIN
            + " ;;",
        ),
    ]:
        mutant = text.replace(before, after)
        assert mutant != text
        with pytest.raises(sbom.SbomError):
            sbom.validate_historical_suse_docker_contract(mutant, "Dockerfile.suse")


@pytest.mark.parametrize("signature_failure", [False, True])
def test_copy_pin_precedes_key_import_and_normal_verification_is_not_skipped(
    collector, monkeypatch, tmp_path, signature_failure
):
    retained = tmp_path / PIN
    retained.write_bytes(GOOD)
    output = tmp_path / "out"
    monkeypatch.setattr(collector, "sha256", fake_hash)
    verified = []

    def key(*args):
        assert (output / "sources" / (NAME + ".partial")).read_bytes() == GOOD
        return tmp_path / "isolated-db"

    def verify(path, name, vendor, database, seconds):
        verified.append(name)
        assert path.read_bytes() == GOOD and database == tmp_path / "isolated-db"
        if signature_failure:
            raise ValueError("signature rejected")
        return {
            "filename": name,
            "vendor": vendor,
            "sha256": PIN,
            "fingerprint": collector.REPOSITORIES[vendor]["fingerprint"],
        }

    monkeypatch.setattr(collector, "prepare_key", key)
    monkeypatch.setattr(collector, "verify_source", verify)
    monkeypatch.setattr(
        collector,
        "download",
        lambda *a: pytest.fail(
            "retained source must never trigger a download/fallback"
        ),
    )
    args = [
        "--inventory",
        str(inventory(tmp_path)),
        "--arch",
        "x86_64",
        "--out",
        str(output),
        "--suse-key",
        str(tmp_path / "suse.asc"),
        "--retained-release-source",
        str(retained),
    ]
    if signature_failure:
        with pytest.raises(ValueError, match="signature rejected"):
            collector.main(args)
        assert not (output / "index.json").exists()
    else:
        collector.main(args)
        source = json.loads((output / "index.json").read_text())["sources"][0]
        assert source["retrieval"] == "reverified-retained-source"
        assert source["retained_sha256"] == PIN and source["final_url"] is None
        assert source["url"] == collector.source_url("openSUSE", "x86_64", NAME)
    assert verified == [NAME]


def test_tampered_copy_fails_before_key_import_with_no_network_fallback(
    collector, monkeypatch, tmp_path
):
    retained = tmp_path / PIN
    retained.write_bytes(GOOD)
    monkeypatch.setattr(collector, "sha256", fake_hash)
    monkeypatch.setattr(
        collector.shutil,
        "copyfile",
        lambda src, dst: dst.write_bytes(b"changed during copy"),
    )
    monkeypatch.setattr(
        collector,
        "prepare_key",
        lambda *a: pytest.fail("copy hash must precede key import"),
    )
    monkeypatch.setattr(collector, "download", lambda *a: pytest.fail("no fallback"))
    with pytest.raises(ValueError, match="retained release source"):
        collector.main(
            [
                "--inventory",
                str(inventory(tmp_path)),
                "--arch",
                "x86_64",
                "--out",
                str(tmp_path / "out"),
                "--suse-key",
                str(tmp_path / "suse.asc"),
                "--retained-release-source",
                str(retained),
            ]
        )
    assert not (tmp_path / "out/index.json").exists()


def test_other_origins_stay_fresh_and_generic_cache_still_needs_complete_expected_index(
    collector, monkeypatch, tmp_path
):
    retained = tmp_path / PIN
    retained.write_bytes(GOOD)
    monkeypatch.setattr(collector, "sha256", fake_hash)
    monkeypatch.setattr(collector, "prepare_key", lambda *a: tmp_path / "db")
    requests = []

    def download(url, path, budget):
        requests.append(url)
        path.write_bytes(b"ordinary fresh source")
        return {"url": url, "final_url": url}

    def verify(path, name, vendor, database, seconds):
        return {
            "filename": name,
            "vendor": vendor,
            "sha256": fake_hash(path),
            "fingerprint": collector.REPOSITORIES[vendor]["fingerprint"],
        }

    monkeypatch.setattr(collector, "download", download)
    monkeypatch.setattr(collector, "verify_source", verify)
    args = [
        "--inventory",
        str(inventory(tmp_path, other=True)),
        "--arch",
        "x86_64",
        "--out",
        str(tmp_path / "out"),
        "--suse-key",
        str(tmp_path / "suse.asc"),
        "--retained-release-source",
        str(retained),
    ]
    collector.main(args)
    assert requests == [
        collector.source_url("openSUSE", "x86_64", "zlib-1.3.1-3.3.src.rpm")
    ]
    expected = tmp_path / "expected.json"
    expected.write_text(
        json.dumps(
            {
                "architecture": "x86_64",
                "sources": [
                    {
                        "filename": NAME,
                        "url": collector.source_url("openSUSE", "x86_64", NAME),
                        "sha256": PIN,
                    }
                ],
            }
        )
    )
    with pytest.raises(ValueError, match="cover every selected origin"):
        collector.main(
            [*args, "--cache-dir", str(tmp_path), "--expected-sha256", str(expected)]
        )
