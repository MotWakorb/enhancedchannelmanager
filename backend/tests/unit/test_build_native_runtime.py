"""Offline controls for the maintained, architecture-pinned native source build."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ASSETS = ROOT / "docker/native"
FIXTURES = ROOT / "backend/tests/fixtures/native_runtime"


@pytest.fixture
def builder():
    spec = importlib.util.spec_from_file_location(
        "build_native_runtime", ROOT / "scripts/build_native_runtime.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_has_locations_and_architecture_not_source_overrides(builder):
    parser = builder._parser()
    assert {a.dest for a in parser._actions} == {
        "help",
        "manifest",
        "work_dir",
        "output_dir",
        "cache_dir",
        "arch",
    }
    with pytest.raises(SystemExit):
        parser.parse_args(["--source-url", "https://attacker.invalid/a"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--source-sha256", "0" * 64])


@pytest.mark.parametrize("arch", ["amd64", "arm64"])
def test_real_two_arch_manifest_has_exact_public_inputs(builder, arch):
    recipe = builder.load_recipe(ASSETS / "manifest.json", arch)
    selected = recipe["architectures"][arch]
    assert selected["rpm_arch"] == {"amd64": "x86_64", "arm64": "aarch64"}[arch]
    assert {p["name"] for p in selected["codec_rpms"]} == {
        "libx264-165",
        "libx264-devel",
        "libx265-215",
        "libx265-devel",
        "libhdr10plus-4_1",
    }
    sources = {s["filename"] for s in selected["source_rpms"]}
    assert {p["sourcerpm"] for p in selected["codec_rpms"]} == sources
    go = [p for p in selected["builder_rpms"] if p["name"] == "go1.26"]
    if arch == "amd64":
        assert len(go) == 1 and go[0]["version"] == "1.26.10-1.1"
    else:
        assert not go
        assert selected["go_toolchain"] == _arm_go_descriptor()
    assert recipe["gosu"]["go_version"] == "1.26.10"
    assert recipe["gosu"]["modules"]["golang.org/x/sys"] == "v0.44.0"


def test_recorded_rpm_headers_really_match_both_arch_manifests(builder):
    manifest = json.loads((ASSETS / "manifest.json").read_text())
    rows = (FIXTURES / "rpm-headers.tsv").read_text().splitlines()
    for arch in ["amd64", "arm64"]:
        for expected in manifest["architectures"][arch]["codec_rpms"]:
            row = next(
                line
                for line in rows
                if line.split("\t")[:3]
                == [expected["name"], expected["version"], expected["arch"]]
            )
            assert (
                builder.validate_rpm_header(row, expected)["sourcerpm"]
                == expected["sourcerpm"]
            )


def test_recorded_primary_metadata_preserves_exact_hdr_dependency():
    import xml.etree.ElementTree as ET

    ns = {
        "c": "http://linux.duke.edu/metadata/common",
        "r": "http://linux.duke.edu/metadata/rpm",
    }
    recipe = json.loads((ASSETS / "manifest.json").read_text())
    fixture = json.loads((FIXTURES / "package-primary-records.json").read_text())
    assert {r["arch"] for r in fixture} == {"x86_64", "aarch64"}
    for row in fixture:
        element = ET.fromstring(row["primary_xml"])
        assert element.findtext("c:arch", namespaces=ns) == row["arch"]
        if element.findtext("c:name", namespaces=ns) != "libx265-devel":
            continue
        dependency = next(
            r
            for r in element.findall("c:format/r:requires/r:entry", ns)
            if r.attrib["name"] == "libhdr10plus-4_1"
        )
        arch = "amd64" if row["arch"] == "x86_64" else "arm64"
        expected = next(
            p
            for p in recipe["architectures"][arch]["codec_rpms"]
            if p["name"] == "libhdr10plus-4_1"
        )
        assert (
            expected["version"]
            == dependency.attrib["ver"] + "-" + dependency.attrib["rel"]
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("url", "https://attacker.invalid/ffmpeg.tar.xz"),
        ("sha256", "0" * 64),
        ("fingerprint", "0" * 40),
    ],
)
def test_changed_release_identity_is_not_a_source_override(
    builder, tmp_path, field, value
):
    recipe = json.loads((ASSETS / "manifest.json").read_text())
    recipe["ffmpeg"][field] = value
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(recipe))
    with pytest.raises(builder.BuildError, match="identity|pinned|fingerprint"):
        builder.load_recipe(manifest, "amd64")


def test_wrong_arch_manifest_and_unknown_arch_fail_closed(builder, tmp_path):
    recipe = json.loads((ASSETS / "manifest.json").read_text())
    recipe["architectures"]["arm64"]["codec_rpms"][0]["arch"] = "x86_64"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(recipe))
    with pytest.raises(builder.BuildError, match="architecture"):
        builder.load_recipe(manifest, "arm64")
    with pytest.raises(builder.BuildError, match="architecture"):
        builder.load_recipe(ASSETS / "manifest.json", "ppc64le")


def test_omitted_hdr_split_cannot_reach_package_solver(builder, tmp_path):
    recipe = json.loads((ASSETS / "manifest.json").read_text())
    recipe["architectures"]["amd64"]["codec_rpms"] = [
        p
        for p in recipe["architectures"]["amd64"]["codec_rpms"]
        if p["name"] != "libhdr10plus-4_1"
    ]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(recipe))
    with pytest.raises(builder.BuildError, match="codec|HDR|dependency"):
        builder.load_recipe(path, "amd64")


def test_source_hash_fixture_is_actual_verified_release_data():
    fixture = json.loads((FIXTURES / "source-identities.json").read_text())
    assert (
        fixture["ffmpeg"]["sha256"]
        == "7138d28c96d9d3e3af4ee3d8cad72741f8ffb40da90c1112235dea3ecd3178a3"
    )
    assert (
        fixture["gosu"]["sha256"]
        == "33d7537d588ea49458b9509bcf4554bdf5ceacc66da71e5caa1058ea3b689c3b"
    )


def test_cache_checksum_mismatch_is_not_download_or_execution_permission(
    builder, tmp_path, monkeypatch
):
    item = {
        "url": "https://ffmpeg.org/releases/ffmpeg-8.1.3.tar.xz",
        "sha256": "0" * 64,
    }
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / item["sha256"]).write_bytes(b"tampered")
    monkeypatch.setattr(
        builder,
        "urlopen",
        lambda *_a, **_k: pytest.fail(
            "invalid cached input must fail, not fetch a replacement"
        ),
    )
    with pytest.raises(builder.BuildError, match="checksum"):
        builder.fetch_input(item, tmp_path / "archive", cache)


def test_good_cached_input_is_still_hashed(builder, tmp_path, monkeypatch):
    data = b"original synthetic offline unit bytes"
    digest = hashlib.sha256(data).hexdigest()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / digest).write_bytes(data)
    monkeypatch.setattr(
        builder, "urlopen", lambda *_a, **_k: pytest.fail("network in unit test")
    )
    output = tmp_path / "archive"
    builder.fetch_input(
        {"url": "https://ffmpeg.org/releases/ffmpeg-8.1.3.tar.xz", "sha256": digest},
        output,
        cache,
    )
    assert output.read_bytes() == data


def test_signature_is_exact_valid_fingerprint_not_just_good_signature(builder):
    valid = (FIXTURES / "ffmpeg-signature.txt").read_text()
    fingerprint = "FCF986EA15E6E293A5644F10B4322F04D67658D8"
    builder.validate_signature_status(valid, fingerprint)
    for status in [
        valid.replace(fingerprint, "0" * 40),
        "[GNUPG:] GOODSIG B4322F04D67658D8 text",
        valid + "\n[GNUPG:] BADSIG bad",
    ]:
        with pytest.raises(builder.BuildError, match="signature"):
            builder.validate_signature_status(status, fingerprint)


def test_wrong_binary_rpm_architecture_is_rejected_before_install(builder):
    manifest = json.loads((ASSETS / "manifest.json").read_text())
    expected = manifest["architectures"]["arm64"]["codec_rpms"][0]
    row = "\t".join(
        [
            expected["name"],
            expected["version"],
            "x86_64",
            expected["sourcerpm"],
            "(none)",
        ]
    )
    with pytest.raises(builder.BuildError, match="RPM.*identity|architecture"):
        builder.validate_rpm_header(row, expected)


def test_unresolved_linkage_is_a_build_failure(builder):
    with pytest.raises(builder.BuildError, match="dependency"):
        builder.parse_ldd("libcodec2.so.1.2 => not found\n")
    assert builder.parse_ldd(
        "libc.so.6 => /usr/lib64/libc.so.6 (0x1)\n/lib64/ld-linux-x86-64.so.2 (0x2)\n"
    ) == ["/usr/lib64/libc.so.6", "/lib64/ld-linux-x86-64.so.2"]


def test_environment_cannot_override_source_compiler_module_or_config_identity(
    builder, tmp_path, monkeypatch
):
    for key in [
        "FFMPEG_URL",
        "FFMPEG_SHA256",
        "CC",
        "CFLAGS",
        "GOFLAGS",
        "GOTOOLCHAIN",
        "GOPROXY",
        "GOSUMDB",
        "LD_LIBRARY_PATH",
        "PKG_CONFIG_PATH",
    ]:
        monkeypatch.setenv(key, "attacker-controlled")
    env = builder.build_environment(tmp_path, "amd64")
    assert env["GOTOOLCHAIN"] == "local"
    assert env["GOPROXY"] == "https://proxy.golang.org"
    assert env["GOSUMDB"] == "sum.golang.org"
    assert env["CC"] == "/usr/bin/gcc"
    assert "attacker-controlled" not in env.values()
    assert "PKG_CONFIG_PATH" not in env and "LD_LIBRARY_PATH" not in env


@pytest.mark.parametrize(
    "version",
    [
        "go version go1.27.2 linux/amd64",
        "go version go1.26.9 linux/amd64",
        "go version go1.26.10 linux/arm64",
    ],
)
def test_wrong_or_vulnerable_compiler_never_passes(builder, version):
    with pytest.raises(builder.BuildError, match="Go.*version|architecture"):
        builder.validate_go_version(version, "amd64")


def test_native_program_version_and_arch_are_verified_before_pass(
    builder, tmp_path, monkeypatch
):
    binary = tmp_path / "ffmpeg"
    binary.write_bytes(b"\x7fELFowned-test")
    header = (FIXTURES / "ffmpeg-readelf.txt").read_text()

    def run(command, **_kwargs):
        return (
            header
            if command[0] == "/usr/bin/readelf"
            else "ffmpeg version 9.0.2 Copyright"
        )

    monkeypatch.setattr(builder, "run", run)
    with pytest.raises(builder.BuildError, match="version"):
        builder.verify_program(binary, "ffmpeg", "amd64", {})
    with pytest.raises(builder.BuildError, match="architecture"):
        builder.verify_elf(binary, "arm64", {})


def test_actual_numa_warning_is_not_a_version_override(builder, tmp_path, monkeypatch):
    binary = tmp_path / "ffmpeg"
    binary.write_bytes(b"\x7fELFowned-test")
    header = (FIXTURES / "ffmpeg-readelf.txt").read_text()
    version = (FIXTURES / "ffmpeg-version-with-warning.txt").read_text()
    monkeypatch.setattr(
        builder,
        "run",
        lambda command, **_k: header if command[0] == "/usr/bin/readelf" else version,
    )
    assert (
        builder.verify_program(binary, "ffmpeg", "amd64", {})["version_output"]
        == version
    )
    version = version.replace("ffmpeg version 8.1.3 ", "ffmpeg version 9.0.2 ")
    with pytest.raises(builder.BuildError, match="version"):
        builder.verify_program(binary, "ffmpeg", "amd64", {})


def test_command_metadata_stdout_is_not_contaminated_by_stderr(
    builder, tmp_path, capsys
):
    text = builder.run(
        [
            sys.executable,
            "-c",
            "import sys; print('metadata'); print('independent warning', file=sys.stderr)",
        ],
        cwd=tmp_path,
        env={},
    )
    assert text == "metadata\n"
    assert "independent warning" in capsys.readouterr().err


def _owned_tree(tmp_path):
    root = tmp_path / "root"
    for name in ["ffmpeg", "ffprobe", "gosu"]:
        path = root / "usr/local/bin" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("original " + name).encode())
    return root


@pytest.mark.parametrize("mutation", ["replace", "missing", "extra", "symlink"])
def test_final_artifact_map_is_checked_before_publishing_pass(
    builder, tmp_path, mutation
):
    root = _owned_tree(tmp_path)
    artifacts = builder.owned_artifacts(root)
    binary = root / "usr/local/bin/ffmpeg"
    if mutation == "replace":
        binary.write_bytes(b"different executable")
    elif mutation == "missing":
        binary.unlink()
    elif mutation == "extra":
        (root / "unexpected").write_bytes(b"unbound")
    else:
        binary.unlink()
        binary.symlink_to("/bin/sh")
    with pytest.raises(builder.BuildError, match="artifact|symlink"):
        builder.publish_record(tmp_path, {"owned_artifacts": artifacts})
    assert not (tmp_path / "build-output.json").exists()


def test_owned_map_good_case_is_byte_bound(builder, tmp_path):
    root = _owned_tree(tmp_path)
    artifacts = builder.owned_artifacts(root)
    builder.verify_artifact_map(root, artifacts)
    assert len(artifacts) == 3
    assert all(len(p["sha256"]) == 64 for p in artifacts)
    builder.publish_record(tmp_path, {"owned_artifacts": artifacts})
    assert json.loads((tmp_path / "build-output.json").read_text())["status"] == "PASS"


def test_archive_traversal_and_escaping_links_fail_before_extract(builder, tmp_path):
    for name, target in [("../escape", None), ("source/link", "../../escape")]:
        archive = tmp_path / (hashlib.sha256(name.encode()).hexdigest() + ".tar.gz")
        with tarfile.open(archive, "w:gz") as bundle:
            member = tarfile.TarInfo(name)
            if target:
                member.type, member.linkname = tarfile.SYMTYPE, target
                bundle.addfile(member)
            else:
                member.size = 1
                bundle.addfile(member, io.BytesIO(b"x"))
        with pytest.raises(builder.BuildError, match="archive"):
            builder.safe_extract(archive, tmp_path / "source")


def test_manifest_config_must_be_reviewed_exact_file(builder, tmp_path):
    recipe = json.loads((ASSETS / "manifest.json").read_text())
    recipe["configure"]["sha256"] = "0" * 64
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(recipe))
    with pytest.raises(builder.BuildError, match="config|asset|checksum"):
        builder.load_recipe(path, "amd64")


def _patch_fixture(tmp_path):
    source = tmp_path / "source"
    target = source / "libavformat/rtsp.c"
    target.parent.mkdir(parents=True)
    before, after = (
        b"original synthetic unit input\n",
        b"bounded synthetic unit input\n",
    )
    target.write_bytes(before)
    patch = tmp_path / "patches/CVE-2026-107696.patch"
    patch.parent.mkdir()
    patch.write_text(
        "--- a/libavformat/rtsp.c\n+++ b/libavformat/rtsp.c\n@@ -1 +1 @@\n-original synthetic unit input\n+bounded synthetic unit input\n"
    )
    record = {
        "order": 1,
        "id": "CVE-2026-107696",
        "upstream_commit": "f5d4fd186c68913240c4f6e9cda98443e1518b34",
        "upstream_url": "https://github.com/FFmpeg/FFmpeg/commit/f5d4fd186c68913240c4f6e9cda98443e1518b34",
        "file": "patches/CVE-2026-107696.patch",
        "sha256": hashlib.sha256(patch.read_bytes()).hexdigest(),
        "witnesses": [
            {
                "path": "libavformat/rtsp.c",
                "before_sha256": hashlib.sha256(before).hexdigest(),
                "after_sha256": hashlib.sha256(after).hexdigest(),
            }
        ],
    }
    return source, target, after, record


@pytest.mark.parametrize("mutation", ["missing", "checksum", "order", "commit", "path"])
def test_unreviewed_missing_or_reordered_patch_is_not_an_override(
    builder, tmp_path, monkeypatch, mutation
):
    _source, _target, _after, record = _patch_fixture(tmp_path)
    records = [record]
    # Synthetic unit authority only. Production pins the parent's exact patch.
    monkeypatch.setattr(builder, "REVIEWED_PATCHES", json.loads(json.dumps(records)))
    if mutation == "missing":
        records = []
    elif mutation == "checksum":
        record["sha256"] = "0" * 64
    elif mutation == "order":
        record["order"] = 2
    elif mutation == "commit":
        record["upstream_commit"] = "0" * 40
    else:
        record["file"] = "../../unreviewed.patch"
    with pytest.raises(builder.BuildError, match="patch"):
        builder.validate_patch_plan(records, tmp_path)


@pytest.mark.parametrize("mutation", ["before", "after", "extra-file"])
def test_patch_source_witness_mismatch_fails_before_pass(
    builder, tmp_path, monkeypatch, mutation
):
    source, target, after, record = _patch_fixture(tmp_path)
    if mutation == "before":
        target.write_bytes(b"different starting source")
    elif mutation == "after":
        record["witnesses"][0]["after_sha256"] = "0" * 64
    else:
        patch = tmp_path / record["file"]
        patch.write_text(
            patch.read_text()
            + "--- a/unbound.c\n+++ b/unbound.c\n@@ -0,0 +1 @@\n+unbound\n"
        )
        record["sha256"] = hashlib.sha256(patch.read_bytes()).hexdigest()
    monkeypatch.setattr(builder, "REVIEWED_PATCHES", json.loads(json.dumps([record])))
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        target.write_bytes(after)
        return "patching file libavformat/rtsp.c\n"

    monkeypatch.setattr(builder, "run", run)
    with pytest.raises(builder.BuildError, match="patch|witness"):
        builder.apply_patches([record], tmp_path, source, {})
    if mutation in ["before", "extra-file"]:
        assert not calls


def test_exact_ordered_patch_records_actual_output_source_binding(
    builder, tmp_path, monkeypatch
):
    source, target, after, record = _patch_fixture(tmp_path)
    monkeypatch.setattr(builder, "REVIEWED_PATCHES", json.loads(json.dumps([record])))
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        target.write_bytes(after)
        return "patching file libavformat/rtsp.c\n"

    monkeypatch.setattr(builder, "run", run)
    proof = builder.apply_patches([record], tmp_path, source, {})
    assert calls and "--fuzz=0" in calls[0]
    assert (
        proof[0]["witnesses"][0]["actual_after_sha256"]
        == hashlib.sha256(after).hexdigest()
    )


def test_actual_patch_asset_and_witnesses_are_the_reviewed_upstream_bytes(builder):
    recipe = json.loads((ASSETS / "manifest.json").read_text())
    assert recipe["ffmpeg_patches"] == builder.REVIEWED_PATCHES
    builder.validate_patch_plan(recipe["ffmpeg_patches"], ASSETS)
    assert (
        recipe["ffmpeg_patches"][0]["sha256"]
        == "7518b281a719aa41c5cdd1f259eaf38f6b1e3fb28c09ec254c441abbaad69095"
    )


def test_arm_source_repository_is_not_an_x86_equivalence(builder, tmp_path):
    recipe = json.loads((ASSETS / "manifest.json").read_text())
    recipe["architectures"]["arm64"]["official_source_repository"] = recipe[
        "architectures"
    ]["amd64"]["official_source_repository"]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(recipe))
    with pytest.raises(builder.BuildError, match="source repository/architecture"):
        builder.load_recipe(path, "amd64")


@pytest.mark.parametrize("failure", ["checksum", "signature"])
def test_unverified_source_never_reaches_extraction_or_build(
    builder, tmp_path, monkeypatch, failure
):
    work, sources, root = tmp_path / "work", tmp_path / "sources", tmp_path / "root"
    for path in [work, sources, root]:
        path.mkdir()
    recipe = json.loads((ASSETS / "manifest.json").read_text())
    monkeypatch.setattr(
        builder,
        "safe_extract",
        lambda *_a: pytest.fail("unverified source was extracted"),
    )
    monkeypatch.setattr(builder, "_verify_key", lambda *_a: None)

    def fetch(_item, destination, _cache):
        if failure == "checksum":
            raise builder.BuildError("source checksum mismatch")
        destination.write_bytes(b"synthetic signature failure input")

    monkeypatch.setattr(builder, "fetch_input", fetch)
    monkeypatch.setattr(builder, "run", lambda *_a, **_k: "[GNUPG:] BADSIG bad\n")
    with pytest.raises(builder.BuildError, match=failure):
        builder._ffmpeg(recipe, ASSETS, work, sources, root, {}, None, "amd64")


def test_actual_configuration_framing_preserves_exact_flags(builder):
    text = (FIXTURES / "ffmpeg-buildconf-parent.txt").read_text()
    expected = (ASSETS / "ffmpeg.args").read_text().splitlines()
    builder.validate_configuration(text, expected)
    for bad in [
        text + "\nunrecognized argument\n",
        text.replace("Exiting with exit code 0", "Exiting with exit code 1"),
        text + "\nconfiguration:\n",
        text.replace("--enable-libgme\n", ""),
        text + "\n--enable-nonfree\n",
        text.replace("--enable-libgme", "--disable-libgme"),
    ]:
        with pytest.raises(builder.BuildError, match="configuration"):
            builder.validate_configuration(bad, expected)


def _arm_go_descriptor():
    release = json.loads((FIXTURES / "go-catalogue.json").read_text())["release"]
    sdk = next(f for f in release["files"] if f["kind"] == "archive")
    source = next(f for f in release["files"] if f["kind"] == "source")
    return {
        "url": "https://go.dev/dl/" + sdk["filename"],
        "sha256": sdk["sha256"],
        "size": sdk["size"],
        "version": sdk["version"],
        "os": sdk["os"],
        "arch": sdk["arch"],
        "elf_machine": 183,
        "source": {
            "url": "https://go.dev/dl/" + source["filename"],
            "sha256": source["sha256"],
            "size": source["size"],
        },
    }


def test_official_catalogue_arm_descriptor_is_valid_without_executing_arm(builder):
    descriptor = _arm_go_descriptor()
    builder.validate_arm_go_descriptor(descriptor)
    assert descriptor["version"] == "go1.26.10"
    assert descriptor["os"] == "linux" and descriptor["arch"] == "arm64"
    assert descriptor["elf_machine"] == 183


@pytest.mark.parametrize(
    "field,value",
    [
        ("arch", "amd64"),
        ("os", "darwin"),
        ("sha256", "0" * 64),
        ("size", 1),
        ("version", "go1.26.9"),
        ("elf_machine", 62),
        ("url", "https://untrusted.invalid/go.tar.gz"),
        ("source", {}),
    ],
)
def test_arm_sdk_descriptor_cannot_override_published_pins(builder, field, value):
    descriptor = _arm_go_descriptor()
    descriptor[field] = value
    with pytest.raises(builder.BuildError, match="Go.*metadata|Go.*identity"):
        builder.validate_arm_go_descriptor(descriptor)


@pytest.mark.parametrize("field", ["sha256", "size", "version", "arch", "source"])
def test_arm_sdk_requires_every_pinned_metadata_field(builder, field):
    descriptor = _arm_go_descriptor()
    del descriptor[field]
    with pytest.raises(builder.BuildError, match="Go.*metadata|Go.*identity"):
        builder.validate_arm_go_descriptor(descriptor)


def _arm_go_tree(tmp_path, version="go1.26.10", machine=183):
    sdk = tmp_path / "sdk"
    (sdk / "bin").mkdir(parents=True)
    (sdk / "VERSION").write_text(version + "\ntime synthetic-unit-fixture\n")
    header = bytearray(64)
    header[:7] = b"\x7fELF\x02\x01\x01"
    header[18:20] = machine.to_bytes(2, "little")
    (sdk / "bin/go").write_bytes(header)
    return sdk


@pytest.mark.parametrize(
    "version,machine", [("go1.26.9", 183), ("go1.27.2", 183), ("go1.26.10", 62)]
)
def test_arm_sdk_version_and_numeric_elf_machine_fail_before_execution(
    builder, tmp_path, version, machine
):
    sdk = _arm_go_tree(tmp_path, version, machine)
    with pytest.raises(builder.BuildError, match="Go SDK.*VERSION|Go SDK.*ELF"):
        builder.validate_arm_go_tree(sdk)


def test_arm_sdk_tree_is_checked_statically_without_running_go(
    builder, tmp_path, monkeypatch
):
    sdk = _arm_go_tree(tmp_path)
    monkeypatch.setattr(
        builder, "run", lambda *_a, **_k: pytest.fail("ARM execution on x86")
    )
    proof = builder.validate_arm_go_tree(sdk)
    assert proof["version"] == "go1.26.10" and proof["elf_machine"] == 183


def test_arm_sdk_host_guard_precedes_download_or_execution(
    builder, tmp_path, monkeypatch
):
    monkeypatch.setattr(builder.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        builder, "fetch_input", lambda *_a: pytest.fail("wrong host downloaded SDK")
    )
    with pytest.raises(builder.BuildError, match="architecture"):
        builder.prepare_arm_go(
            _arm_go_descriptor(), tmp_path, tmp_path / "sources", None
        )


def test_arm_sdk_corrupt_cache_never_reaches_extract_or_execution(
    builder, tmp_path, monkeypatch
):
    descriptor = _arm_go_descriptor()
    monkeypatch.setattr(builder.platform, "machine", lambda: "aarch64")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / descriptor["sha256"]).write_bytes(b"corrupt")
    monkeypatch.setattr(
        builder, "safe_extract", lambda *_a: pytest.fail("unchecked SDK extracted")
    )
    monkeypatch.setattr(
        builder, "urlopen", lambda *_a, **_k: pytest.fail("network in unit")
    )
    with pytest.raises(builder.BuildError, match="checksum"):
        builder.prepare_arm_go(descriptor, tmp_path, tmp_path / "sources", cache)


def test_pinned_download_size_is_checked_even_for_correct_cached_bytes(
    builder, tmp_path
):
    data = b"original synthetic unit size fixture"
    digest = hashlib.sha256(data).hexdigest()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / digest).write_bytes(data)
    with pytest.raises(builder.BuildError, match="size"):
        builder.fetch_input(
            {"url": "https://go.dev/dl/unit", "sha256": digest, "size": len(data) + 1},
            tmp_path / "output",
            cache,
        )


def test_arm_sdk_keeps_only_matching_source_not_compiler_in_retained_output(
    builder, tmp_path, monkeypatch
):
    work, sources, cache = tmp_path / "work", tmp_path / "sources", tmp_path / "cache"
    for directory in [work, sources, cache]:
        directory.mkdir()
    fake_tree = _arm_go_tree(tmp_path)
    (fake_tree / "LICENSE").write_text("Original synthetic BSD notice fixture\n")
    archive = tmp_path / "sdk.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(fake_tree, arcname="go")
    descriptor = _arm_go_descriptor()
    descriptor.update(
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        size=archive.stat().st_size,
    )
    source_bytes = b"original synthetic source-archive storage fixture"
    descriptor["source"].update(
        sha256=hashlib.sha256(source_bytes).hexdigest(), size=len(source_bytes)
    )
    (cache / descriptor["sha256"]).write_bytes(archive.read_bytes())
    (cache / descriptor["source"]["sha256"]).write_bytes(source_bytes)
    # Unit-only authority for synthetic bytes; the production descriptor is fixed.
    monkeypatch.setattr(builder, "ARM_GO_TOOLCHAIN", descriptor)
    monkeypatch.setattr(builder.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(
        builder, "run", lambda *_a, **_k: pytest.fail("ARM execution on x86")
    )
    go, proof = builder.prepare_arm_go(descriptor, work, sources, cache)
    assert Path(go).is_relative_to(work)
    assert sorted(p.name for p in sources.iterdir()) == ["go1.26.10.src.tar.gz"]
    assert (sources / "go1.26.10.src.tar.gz").read_bytes() == source_bytes
    assert (
        proof["elf_machine"] == 183
        and proof["source_archive_sha256"] == descriptor["source"]["sha256"]
    )


def test_go_sdk_download_rejects_untrusted_redirect(builder, tmp_path, monkeypatch):
    response = io.BytesIO(b"untrusted redirect")
    response.url = "https://mirror.invalid/go.tar.gz"
    monkeypatch.setattr(builder, "urlopen", lambda *_a, **_k: response)
    with pytest.raises(builder.BuildError, match="official publisher"):
        builder.fetch_input(_arm_go_descriptor(), tmp_path / "archive", None)
