"""Bounded source delivery tests; native RPM integration is explicitly opt-in."""

import copy
import hashlib
import importlib.util
import io
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "backend/tests/fixtures/native_sources"
spec = importlib.util.spec_from_file_location(
    "collect_native_sources", ROOT / "scripts/collect_native_sources.py"
)
collector = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = collector
spec.loader.exec_module(collector)


def test_recorded_inventory_and_actual_vendor_key_bindings():
    rows, excluded = collector.parse_inventory(
        (FIXTURES / "inventory.tsv").read_text(), "x86_64"
    )
    assert len(rows) == 4
    assert len(excluded) == 1
    assert {row["vendor"] for row in rows} == {
        "openSUSE",
        "http://packman.links2linux.de",
    }
    assert {
        vendor: repo["fingerprint"] for vendor, repo in collector.REPOSITORIES.items()
    } == {
        "openSUSE": "AD485664E901B867051AB15F35A2F86E29B700A4",
        "http://packman.links2linux.de": "F8875B880D518B6B8C530D1345A1D0671ABD1AFB",
    }


@pytest.mark.parametrize(
    "change",
    [
        {"arch": "(none)"},
        {"arch": "aarch64"},
        {"sourcerpm": "../bad.src.rpm"},
        {"sourcerpm": "bad.nosrc.rpm"},
        {"sourcerpm": "(none)"},
        {"vendor": "Packman"},
        {"license": ""},
        {"name": "../bad"},
        {"version": ""},
        {"release": "(none)"},
    ],
)
def test_inventory_rejects_missing_unsafe_or_unapproved_fields(change):
    row = dict(
        zip(
            collector.FIELDS,
            (FIXTURES / "inventory.tsv").read_text().splitlines()[2].split("\t"),
        )
    )
    row.update(change)
    with pytest.raises(ValueError):
        collector.parse_inventory(
            "\t".join(row[field] for field in collector.FIELDS), "x86_64"
        )


def test_inventory_rejects_empty_duplicates_and_false_key_exemptions():
    text = (FIXTURES / "inventory.tsv").read_text()
    for bad in [
        "",
        text + text,
        text.splitlines()[0].replace("pubkey", "MIT"),
        text.splitlines()[0].replace("(none)", "x86_64", 1),
        text.splitlines()[0],
        text.replace("\topenSUSE\t", "\topenSUSE\textra\t"),
    ]:
        with pytest.raises(ValueError):
            collector.parse_inventory(bad, "x86_64")


def test_arm_inventory_is_not_x86_evidence():
    with pytest.raises(ValueError, match="architecture"):
        collector.parse_inventory((FIXTURES / "inventory.tsv").read_text(), "aarch64")


def test_arm_source_feed_is_documented_and_same_filename_is_not_byte_identity():
    arm = json.loads((FIXTURES / "arm64-provenance.json").read_text())
    x86 = json.loads((FIXTURES / "provenance.json").read_text())
    filename = arm["coreutils"]["filename"]
    assert (
        collector.source_url("openSUSE", "aarch64", filename)
        == arm["source_repository"] + "src/" + filename
    )
    assert (
        collector.source_url("openSUSE", "x86_64", filename) == x86["coreutils"]["url"]
    )
    assert arm["coreutils"]["sha256"] != x86["coreutils"]["sha256"]


def test_expected_hashes_bind_architecture_and_source_feed(tmp_path):
    filename = "coreutils-9.12-1.1.src.rpm"
    expected = {
        "architecture": "x86_64",
        "sources": [
            {
                "filename": filename,
                "url": collector.source_url("openSUSE", "x86_64", filename),
                "sha256": "0" * 64,
            }
        ],
    }
    path = tmp_path / "expected.json"
    path.write_text(json.dumps(expected))
    with pytest.raises(ValueError, match="architecture"):
        collector.expected_hashes(path, "aarch64", [(filename, "openSUSE")])
    expected["architecture"] = (
        "aarch64"  # Relabeling cannot silently retain the x86 feed.
    )
    path.write_text(json.dumps(expected))
    with pytest.raises(ValueError, match="repository"):
        collector.expected_hashes(path, "aarch64", [(filename, "openSUSE")])


def test_rpmkeys_requires_pinned_signature_and_payload_digest():
    pin = collector.REPOSITORIES["openSUSE"]["fingerprint"]
    good = "Header V3 RSA/SHA512 Signature, key ID 29b700a4: OK\nPayload SHA256 digest: OK\n"
    collector.check_rpmkeys(good, pin)
    for bad in [
        "",
        "Payload SHA256 digest: OK",
        good.replace("29b700a4", "1abd1afb"),
        good.replace("digest: OK", "digest: BAD"),
        good + "NOKEY",
        good.splitlines()[0],
    ]:
        with pytest.raises(ValueError):
            collector.check_rpmkeys(bad, pin)


def test_real_small_srpm_payload_and_omitted_declared_inputs():
    provenance = json.loads((FIXTURES / "provenance.json").read_text())[
        "system_user_root"
    ]
    body = (FIXTURES / "system-user-root-20190513-2.20.src.rpm").read_bytes()
    assert len(body) == provenance["size"]
    assert hashlib.sha256(body).hexdigest() == provenance["sha256"]
    assert hashlib.sha512(body).hexdigest() == provenance["primary_sha512"]
    audit = collector.audit_payload(FIXTURES / "system-user-root-20190513-2.20.src.rpm")
    assert audit["source"] == ["system-user-root.conf"]
    assert audit["patch"] == []
    assert audit["nosource"] == audit["nopatch"] == []
    assert audit["spec_names"] == ["system-user-root.spec"]
    assert {item["name"]: item["sha256"] for item in audit["files"]} == {
        "system-user-root.conf": "b3145f497c41a7e25e196afe0656cce0892afb1a766cd7a68af99a4692cf0cca",
        "system-user-root.spec": "ad93460f4cecd4cf9fb1da45602eef7d6b0349cd78692dd080e85302f6f75245",
    }
    tags = {"1018": audit["source"], "1019": audit["patch"]}
    names = {item["name"] for item in audit["files"]}
    collector.complete_payload(tags, names)
    for missing in names:
        with pytest.raises(ValueError):
            collector.complete_payload(tags, names - {missing})
    for tag in ("1051", "1052"):
        with pytest.raises(ValueError, match="NoSource/NoPatch"):
            collector.complete_payload({**tags, tag: [0]}, names)


def cpio(entries):
    result = bytearray()
    for name, body, mode in [*entries, ("TRAILER!!!", b"", 0)]:
        encoded = name.encode() + b"\0"
        fields = [0, mode, 0, 0, 1, 0, len(body), 0, 0, 0, 0, len(encoded), 0]
        result.extend(b"070701" + b"".join(f"{value:08x}".encode() for value in fields))
        result.extend(encoded)
        result.extend(b"\0" * (-(110 + len(encoded)) % 4))
        result.extend(body)
        result.extend(b"\0" * (-len(body) % 4))
    return bytes(result)


@pytest.mark.parametrize(
    "entries",
    [
        [("../a", b"x", 0o100644)],
        [("/a", b"x", 0o100644)],
        [("dir/a", b"x", 0o100644)],
        [("a\\b", b"x", 0o100644)],
        [("a", b"x", 0o120777)],
        [("a", b"x", 0o100644)] * 2,
        [("a.spec", b"NoSource: 0\n", 0o100644)],
        [("a.spec", b"NoPatch: 0\n", 0o100644)],
    ],
)
def test_payload_rejects_traversal_duplicates_links_and_spec_omissions(entries):
    with pytest.raises(ValueError):
        collector.read_payload(io.BytesIO(cpio(entries)), 1024 * 1024, 5)


def test_payload_size_truncation_extra_data_and_deadline_are_bounded():
    data = cpio([("a.spec", b"Name: a\n", 0o100644)])
    for body, limit, seconds in [
        (data, 8, 5),
        (data[:-1], 1024, 5),
        (data + b"hidden", 1024, 5),
        (data, 1024, 0),
    ]:
        with pytest.raises(ValueError):
            collector.read_payload(io.BytesIO(body), limit, seconds)


def test_redirect_handler_rejects_downgrade_before_following():
    with pytest.raises(ValueError, match="HTTPS"):
        collector.HTTPSRedirect().redirect_request(
            None, None, 302, "Found", {}, "http://example.test/a"
        )


def test_budget_rejects_nonfinite_time_and_enforces_exhaustion(monkeypatch):
    for seconds in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            collector.Budget(1, 1, seconds)
    monkeypatch.setattr(collector.time, "monotonic", lambda: 1)
    budget = collector.Budget(1, 1, 1)
    monkeypatch.setattr(collector.time, "monotonic", lambda: 3)
    with pytest.raises(ValueError, match="time"):
        budget.consume(1)


def test_rpm_header_rejects_duplicate_tags_and_invalid_offsets():
    head = b"\x8e\xad\xe8\x01\0\0\0\0"
    for indices in [
        struct.pack(">IIII", 1000, 6, 99, 1),
        struct.pack(">IIII", 1000, 6, 0, 1) * 2,
    ]:
        blob = head + struct.pack(">II", len(indices) // 16, 2) + indices + b"a\0"
        with pytest.raises(ValueError):
            collector.rpm_header(io.BytesIO(blob))


def test_signed_header_identity_does_not_accept_closest_release_or_wrong_vendor():
    path = FIXTURES / "system-user-root-20190513-2.20.src.rpm"
    tags = collector.source_header(path)
    collector.check_identity(tags, path.name, "openSUSE")
    assert tags["1106"] == [1]
    for name, vendor in [
        (path.name.replace("2.20", "2.19"), "openSUSE"),
        (path.name, "http://packman.links2linux.de"),
    ]:
        with pytest.raises(ValueError):
            collector.check_identity(tags, name, vendor)
    altered = copy.deepcopy(tags)
    altered["1106"] = [0]
    with pytest.raises(ValueError):
        collector.check_identity(altered, path.name, "openSUSE")


def test_declared_patch_must_be_physically_present():
    tags = {"1018": ["source.tar.xz"], "1019": ["fix.patch"]}
    names = {"package.spec", "source.tar.xz", "fix.patch"}
    collector.complete_payload(tags, names)
    with pytest.raises(ValueError, match="missing declared"):
        collector.complete_payload(tags, names - {"fix.patch"})


def test_prepare_key_checks_full_pin_before_rpm_import(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return "fpr:::::::::" + "0" * 40 + ":\n"

    monkeypatch.setattr(collector, "run", fake_run)
    with pytest.raises(ValueError, match="fingerprint"):
        collector.prepare_key(FIXTURES / "suse-key.asc", "openSUSE", tmp_path)
    assert len(calls) == 1
    assert calls[0][0] == "gpg"


def fake_native(monkeypatch):
    """Replace native processes, not identity/payload/hash checks."""
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "gpg":
            return (
                "fpr:::::::::"
                + collector.REPOSITORIES["openSUSE"]["fingerprint"]
                + ":\n"
            )
        if "-Kv" in command:
            return "Header V3 RSA/SHA512 Signature, key ID 29b700a4: OK\nPayload SHA256 digest: OK\n"
        return ""

    monkeypatch.setattr(collector, "run", fake_run)
    return calls


def test_collect_retains_bytes_and_binds_all_installed_rows(monkeypatch, tmp_path):
    calls = fake_native(monkeypatch)
    inventory = (FIXTURES / "inventory.tsv").read_text().splitlines()[1] + "\n"
    inv = tmp_path / "inventory.tsv"
    inv.write_text(inventory)
    copied = []

    def fetch(url, destination, budget):
        copied.append(url)
        body = (FIXTURES / "system-user-root-20190513-2.20.src.rpm").read_bytes()
        budget.consume(len(body))
        destination.write_bytes(body)
        return {"url": url, "final_url": url}

    monkeypatch.setattr(collector, "download", fetch)
    output = tmp_path / "out"
    collector.main(
        [
            "--inventory",
            str(inv),
            "--arch",
            "x86_64",
            "--out",
            str(output),
            "--suse-key",
            str(FIXTURES / "suse-key.asc"),
        ]
    )
    index = json.loads((output / "index.json").read_text())
    assert index["packages"][0]["license"] == "MIT"  # Not a GPL-only selector.
    assert index["packages"][0]["arch"] == "noarch"
    assert len(index["package_set_sha256"]) == len(index["source_set_sha256"]) == 64
    assert index["inventory_sha256"] == hashlib.sha256(inv.read_bytes()).hexdigest()
    source = index["sources"][0]
    assert source["fingerprint"] == collector.REPOSITORIES["openSUSE"]["fingerprint"]
    assert source["authentication"] == "pinned-rpm-signature-and-exact-header"
    assert (output / "sources" / source["filename"]).read_bytes() == (
        FIXTURES / source["filename"]
    ).read_bytes()
    assert len(copied) == 1
    assert any("-Kv" in call for call in calls)
    assert index["architecture"] == "x86_64"
    assert "image_digest" not in index


def test_live_query_targets_runtime_root_not_builder_db(monkeypatch, tmp_path):
    fake_native(monkeypatch)
    original = collector.run
    queries = []

    def query(command, **kwargs):
        if "-qa" in command:
            queries.append(command)
            return (FIXTURES / "inventory.tsv").read_text().splitlines()[1] + "\n"
        return original(command, **kwargs)

    monkeypatch.setattr(collector, "run", query)

    def fetch(url, path, budget):
        path.write_bytes(
            (FIXTURES / "system-user-root-20190513-2.20.src.rpm").read_bytes()
        )
        return {"url": url, "final_url": url}

    monkeypatch.setattr(collector, "download", fetch)
    collector.main(
        [
            "--rpm-root",
            str(tmp_path / "runtime"),
            "--arch",
            "x86_64",
            "--out",
            str(tmp_path / "out"),
            "--suse-key",
            str(FIXTURES / "suse-key.asc"),
        ]
    )
    assert queries == [
        ["rpm", "--root", str(tmp_path / "runtime"), "-qa", "--qf", collector.QUERY]
    ]


def test_count_bound_fails_before_output(monkeypatch, tmp_path):
    fake_native(monkeypatch)
    args = [
        "--inventory",
        str(FIXTURES / "inventory.tsv"),
        "--arch",
        "x86_64",
        "--out",
        str(tmp_path / "out"),
    ]
    with pytest.raises(ValueError, match="count"):
        collector.main([*args, "--max-downloads", "1"])
    assert not (tmp_path / "out").exists()


def test_existing_output_is_not_reused_or_overwritten(monkeypatch, tmp_path):
    fake_native(monkeypatch)
    inventory = tmp_path / "inventory.tsv"
    inventory.write_text((FIXTURES / "inventory.tsv").read_text().splitlines()[1])
    output = tmp_path / "out"
    output.mkdir()
    (output / "index.json").write_text("existing user data")
    with pytest.raises(FileExistsError):
        collector.main(
            [
                "--inventory",
                str(inventory),
                "--arch",
                "x86_64",
                "--out",
                str(output),
                "--suse-key",
                str(FIXTURES / "suse-key.asc"),
            ]
        )
    assert (output / "index.json").read_text() == "existing user data"


def test_failed_signature_leaves_no_success_index(monkeypatch, tmp_path):
    fake_native(monkeypatch)

    def bad_run(command, **kwargs):
        if command[0] == "gpg":
            return (
                "fpr:::::::::"
                + collector.REPOSITORIES["openSUSE"]["fingerprint"]
                + ":\n"
            )
        return "Payload SHA256 digest: BAD\n"

    monkeypatch.setattr(collector, "run", bad_run)
    monkeypatch.setattr(
        collector,
        "download",
        lambda url, path, budget: path.write_bytes(
            (FIXTURES / "system-user-root-20190513-2.20.src.rpm").read_bytes()
        ),
    )
    inv = tmp_path / "inv"
    inv.write_text((FIXTURES / "inventory.tsv").read_text().splitlines()[1])
    with pytest.raises(ValueError):
        collector.main(
            [
                "--inventory",
                str(inv),
                "--arch",
                "x86_64",
                "--out",
                str(tmp_path / "out"),
                "--suse-key",
                str(FIXTURES / "suse-key.asc"),
            ]
        )
    assert not (tmp_path / "out/index.json").exists()


def test_download_enforces_url_bytes_count_and_time(monkeypatch, tmp_path):
    for count, size, seconds in [(0, 100, 1), (1, 0, 1), (1, 100, 0)]:
        with pytest.raises(ValueError):
            collector.Budget(count, size, seconds)

    class Response(io.BytesIO):
        url = "https://mirror.example/source.rpm"

        def geturl(self):
            return self.url

    monkeypatch.setattr(
        collector.urllib.request.OpenerDirector,
        "open",
        lambda *args, **kwargs: Response(b"a" * 20),
    )
    url = collector.source_url("openSUSE", "x86_64", "a-1-1.src.rpm")
    for number, (candidate, budget) in enumerate(
        [
            (url, collector.Budget(1, 10, 1)),
            ("file:///etc/passwd", collector.Budget(1, 100, 1)),
        ]
    ):
        with pytest.raises(ValueError):
            collector.download(candidate, tmp_path / str(number), budget)
    collector.download(url, tmp_path / "a", collector.Budget(1, 100, 1))
    assert (tmp_path / "a").read_bytes() == b"a" * 20
    budget = collector.Budget(1, 100, 1)
    collector.download(url, tmp_path / "first", budget)
    with pytest.raises(ValueError, match="count"):
        collector.download(url, tmp_path / "second", budget)
    Response.url = "http://mirror.example/source.rpm"
    with pytest.raises(ValueError):
        collector.download(url, tmp_path / "b", collector.Budget(1, 100, 1))


def test_cache_requires_expected_hash_and_rechecks_signature(monkeypatch, tmp_path):
    calls = fake_native(monkeypatch)
    archive = FIXTURES / "system-user-root-20190513-2.20.src.rpm"
    inv = tmp_path / "inv"
    inv.write_text((FIXTURES / "inventory.tsv").read_text().splitlines()[1])
    hashes = tmp_path / "hashes.json"
    expected = {
        "architecture": "x86_64",
        "sources": [
            {
                "filename": archive.name,
                "url": collector.source_url("openSUSE", "x86_64", archive.name),
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            }
        ],
    }
    hashes.write_text(json.dumps(expected))
    args = [
        "--inventory",
        str(inv),
        "--arch",
        "x86_64",
        "--suse-key",
        str(FIXTURES / "suse-key.asc"),
        "--cache-dir",
        str(FIXTURES),
        "--expected-sha256",
        str(hashes),
    ]
    monkeypatch.setattr(
        collector, "download", lambda *args: pytest.fail("cache should be used")
    )
    collector.main([*args, "--out", str(tmp_path / "good")])
    assert any("-Kv" in call for call in calls)
    expected["sources"][0]["sha256"] = "0" * 64
    hashes.write_text(json.dumps(expected))
    with pytest.raises(ValueError, match="checksum"):
        collector.main([*args, "--out", str(tmp_path / "bad")])
    with pytest.raises(ValueError, match="expected"):
        collector.main([*args[:-2], "--out", str(tmp_path / "missing")])


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("ECM_NATIVE_SOURCE_INTEGRATION") != "1",
    reason="opt-in: needs rpm/rpmkeys/gpg and exact coreutils archive or public network",
)
def test_actual_signed_coreutils_source_and_tamper_rejection(tmp_path):
    """Once opted in, missing tools/archive/network FAIL rather than silently skip."""
    key = FIXTURES / "suse-key.asc"
    database = collector.prepare_key(key, "openSUSE", tmp_path / "trust")
    archive = tmp_path / "coreutils-9.12-1.1.src.rpm"
    local = os.environ.get("ECM_COREUTILS_SRPM")
    if local:
        archive.write_bytes(Path(local).read_bytes())
    else:
        collector.download(
            collector.source_url("openSUSE", "x86_64", archive.name),
            archive,
            collector.Budget(1, 8_000_000, 120),
        )
    provenance = json.loads((FIXTURES / "provenance.json").read_text())
    assert (
        hashlib.sha256(archive.read_bytes()).hexdigest()
        == provenance["coreutils"]["sha256"]
    )
    verified = collector.verify_source(archive, archive.name, "openSUSE", database)
    assert len(verified["payload"]["patch"]) == 17
    assert "coreutils.spec" in verified["payload"]["spec_names"]
    inv = tmp_path / "inventory.tsv"
    inv.write_text((FIXTURES / "inventory.tsv").read_text().splitlines()[2] + "\n")
    hashes = tmp_path / "hashes.json"
    hashes.write_text(
        json.dumps(
            {
                "architecture": "x86_64",
                "sources": [
                    {
                        "filename": archive.name,
                        "url": provenance["coreutils"]["url"],
                        "sha256": provenance["coreutils"]["sha256"],
                    }
                ],
            }
        )
    )
    collector.main(
        [
            "--inventory",
            str(inv),
            "--arch",
            "x86_64",
            "--out",
            str(tmp_path / "delivery"),
            "--suse-key",
            str(key),
            "--cache-dir",
            str(tmp_path),
            "--expected-sha256",
            str(hashes),
        ]
    )
    delivered = json.loads((tmp_path / "delivery/index.json").read_text())
    assert delivered["sources"][0]["sha256"] == provenance["coreutils"]["sha256"]
    assert (
        tmp_path / "delivery/sources" / archive.name
    ).read_bytes() == archive.read_bytes()
    arm = json.loads((FIXTURES / "arm64-provenance.json").read_text())
    row = arm["inventory_rows"][0].split("\t")
    inv.write_text("\t".join([*row[:4], "openSUSE", *row[4:]]) + "\n")
    hashes.write_text(
        json.dumps(
            {
                "architecture": "aarch64",
                "sources": [
                    {
                        "filename": archive.name,
                        "url": collector.source_url(
                            "openSUSE", "aarch64", archive.name
                        ),
                        "sha256": arm["coreutils"]["sha256"],
                    }
                ],
            }
        )
    )
    with pytest.raises(ValueError, match="checksum"):
        collector.main(
            [
                "--inventory",
                str(inv),
                "--arch",
                "aarch64",
                "--out",
                str(tmp_path / "wrong-arch-cache"),
                "--suse-key",
                str(key),
                "--cache-dir",
                str(tmp_path),
                "--expected-sha256",
                str(hashes),
            ]
        )
    tampered = bytearray(archive.read_bytes())
    tampered[-24] ^= 1
    archive.write_bytes(tampered)
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        collector.verify_source(archive, archive.name, "openSUSE", database)
