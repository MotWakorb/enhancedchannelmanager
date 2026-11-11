"""Physical capture is independent of a scanner's caller-controlled package list."""

import copy
import gzip
import hashlib
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def language(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("alpine_language_inventory")


@pytest.fixture
def physical(language, tmp_path):
    # Protocol-only synthetic root; real published bytes are tested separately.
    root = tmp_path / "image"
    root.mkdir()
    for directory in language.PYTHON_ROOTS:
        (root / directory).mkdir(parents=True)
    for name in ["alpha", "beta"]:
        info = root / language.PYTHON_ROOTS[0] / f"{name}-1.0.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n"
        )
        (info / "RECORD").write_text("protocol witness\n")
    return root


def test_physical_python_catalogue_not_report_names(language, physical):
    rows, witness = language.capture_python(physical)
    assert {r["Name"] for r in rows} == {"alpha", "beta"}
    assert all(r["FilePath"].endswith("/METADATA") for r in rows)
    assert witness


@pytest.mark.parametrize("defect", ["drop", "extra", "version", "path", "duplicate"])
def test_exact_catalogue_join_rejects_missing_false_extra_and_wrong_version(
    language, physical, defect
):
    rows, _ = language.capture_python(physical)
    report = {
        "Results": [
            {
                "Class": "lang-pkgs",
                "Type": "python-pkg",
                "Packages": copy.deepcopy(rows),
            }
        ]
    }
    expected = {"python": rows, "go": []}
    language.check_catalogue(report, expected)
    packages = report["Results"][0]["Packages"]
    if defect == "drop":
        packages.pop()
    elif defect == "extra":
        packages.append(
            {"Name": "fabricated", "Version": "1.0", "FilePath": "false/METADATA"}
        )
    elif defect == "duplicate":
        packages.append(copy.deepcopy(packages[0]))
    else:
        packages[0]["Version" if defect == "version" else "FilePath"] = "false"
    with pytest.raises(language.InputError, match="catalogue"):
        language.check_catalogue(report, expected)


@pytest.mark.parametrize("defect", ["bytes", "inode", "symlink", "new-metadata"])
def test_physical_root_recheck_rejects_source_byte_inode_or_discovery_drift(
    language, physical, defect
):
    before = language.capture_python(physical)
    path = physical / language.PYTHON_ROOTS[0] / "alpha-1.0.dist-info/RECORD"
    if defect == "bytes":
        path.write_text("changed source bytes\n")
    elif defect == "inode":
        replacement = path.with_suffix(".new")
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)
    elif defect == "symlink":
        path.unlink()
        path.symlink_to("METADATA")
    else:
        info = path.parent.with_name("gamma-1.0.dist-info")
        info.mkdir()
        (info / "METADATA").write_text("Name: gamma\nVersion: 1.0\n")
    with pytest.raises(language.InputError, match="physical|canonical"):
        language.recheck_python(physical, before)


def test_capture_anchor_cannot_be_replaced_by_artifact_self_digest(language):
    capture = {"schema_version": 1, "identity": {}, "python": [], "go": []}
    with pytest.raises(language.InputError, match="independent"):
        language.validate_capture(capture, "0" * 64, {})


def test_go_buildinfo_rejects_arbitrary_non_elf_bytes(language):
    with pytest.raises(language.InputError, match="ELF"):
        language.go_catalogue(b"fake module list", "amd64")


def test_missing_physical_metadata_is_not_silently_omitted(language, physical):
    path = physical / language.PYTHON_ROOTS[0] / "alpha-1.0.dist-info/METADATA"
    path.unlink()
    with pytest.raises(language.InputError, match="metadata missing"):
        language.capture_python(physical)


def test_actual_retained_license_directory_is_not_an_installed_distribution(
    language, physical
):
    # Real 0066 retained wheel notices contain license-only .dist-info directories.
    # Their bytes remain in the tree seal, but directory names are not packages.
    path = physical / language.PYTHON_ROOTS[2] / "notice-only-1.0.dist-info"
    path.mkdir()
    notice = path / "LICENSE"
    notice.write_text("retained notice bytes\n")
    before = language.capture_python(physical)
    assert {row["Name"] for row in before[0]} == {"alpha", "beta"}
    notice.write_text("changed retained source notice\n")
    with pytest.raises(language.InputError, match="physical"):
        language.recheck_python(physical, before)


def test_unreadable_physical_directory_is_not_silently_omitted(language, physical):
    path = physical / language.PYTHON_ROOTS[0] / "alpha-runtime-source"
    path.mkdir()
    path.chmod(0)
    try:
        with pytest.raises(language.InputError, match="unreadable physical"):
            language.capture_python(physical)
    finally:
        path.chmod(0o755)


def test_capture_cli_refuses_writable_root_before_reading_identity(
    language, physical, tmp_path
):
    output = tmp_path / "capture.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/alpine_language_inventory.py"),
            "--root",
            str(physical),
            "--identity",
            str(tmp_path / "missing.json"),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1 and "read-only" in result.stderr
    assert not output.exists()


def test_actual_published_gosu_elf_buildinfo_is_independent_catalogue(language):
    fixtures = ROOT / "backend/tests/fixtures/alpine"
    data = gzip.decompress((fixtures / "published-0066-amd64-gosu.elf.gz").read_bytes())
    document = json.loads(
        gzip.decompress(
            (fixtures / "published-0066-amd64-physical-language.json.gz").read_bytes()
        )
    )
    assert (
        hashlib.sha256(data).hexdigest()
        == document["physical_witnesses"]["gosu"]["sha256"]
    )
    assert [
        {**r, "Target": language.GOSU} for r in language.go_catalogue(data, "amd64")
    ] == document["go"]
    with pytest.raises(language.InputError, match="ELF"):
        language.go_catalogue(data, "arm64")


@pytest.mark.parametrize(
    "defect",
    [
        "duplicate-magic",
        "pointer-format",
        "compiler",
        "framing",
        "replacement",
        "wrong-platform",
    ],
)
def test_actual_go_elf_buildinfo_malformed_or_unsupported_is_denied(language, defect):
    fixtures = ROOT / "backend/tests/fixtures/alpine"
    data = bytearray(
        gzip.decompress((fixtures / "published-0066-amd64-gosu.elf.gz").read_bytes())
    )
    magic = b"\xff Go buildinf:"
    offset = data.find(magic)
    if defect == "duplicate-magic":
        data.extend(magic)
    elif defect == "pointer-format":
        data[offset + 15] = 0
    elif defect == "compiler":
        data[offset + 33] = 0
    elif defect == "framing":
        _, end = language.go_string(data, offset + 32)
        data[end + 2] = 0
    elif defect == "replacement":
        start = data.index(b"dep\t", offset)
        data[start : start + 3] = b"=>\t"
    else:
        start = data.index(b"GOARCH=amd64", offset)
        data[start : start + 12] = b"GOARCH=arm64"
    with pytest.raises(language.InputError, match="Go"):
        language.go_catalogue(bytes(data), "amd64")
