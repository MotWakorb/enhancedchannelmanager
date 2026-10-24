"""Delivery integrity only: compact recorded RPM shapes, synthetic owned bytes.

RPM projection: full-native-source-capture/source-set/index.json, 2026-10-24,
206 packages + six key rows, 156 origins. Outer archive bytes below are tiny
stand-ins; payload witnesses are recorded, not invented or re-audited here.
"""

import copy
import hashlib
import importlib.util
import json
import re
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
RPM = "usr/share/ecm/sources/rpm"
OWNED = "usr/share/ecm/sources/owned"
PROOF = "usr/share/ecm/provenance"

# Literal projection of the real collector output (including its vendor spelling).
RECORDED = json.loads(r"""{
  "packages": [{"arch":"x86_64","license":"GPL-2.0-or-later",
    "name":"libx264-165","release":"1699.1.pm.18",
    "sourcerpm":"libx264-20250608.b35605ac-1699.1.pm.18.src.rpm",
    "vendor":"http://packman.links2linux.de","version":"20250608.b35605ac"}],
  "excluded_public_keys": [{"arch":"(none)","license":"pubkey",
    "name":"gpg-pubkey","release":"6a17fa38","sourcerpm":"(none)",
    "vendor":"(none)","version":"29b700a4"}],
  "sources": [{"authentication":"pinned-rpm-signature-and-exact-header",
    "filename":"libx264-20250608.b35605ac-1699.1.pm.18.src.rpm",
    "final_url":"https://ftp.gwdg.de/pub/linux/misc/packman/suse/openSUSE_Tumbleweed/Essentials/src/libx264-20250608.b35605ac-1699.1.pm.18.src.rpm",
    "fingerprint":"F8875B880D518B6B8C530D1345A1D0671ABD1AFB",
    "header_arch":["x86_64"],"license":"GPL-2.0-or-later",
    "path":"sources/libx264-20250608.b35605ac-1699.1.pm.18.src.rpm",
    "payload":{"declared_input_payload_complete":true,
      "files":[{"name":"baselibs.conf","sha256":"f3bedebee2a1d9c7c0d4733b7c8a9a8c4773982728d56861c6c0e0b6f36ab59a","size":12},
        {"name":"libx264.spec","sha256":"cf1a234d4921e26f33b82d2ad27790f7fd67a04968e8acd433838cb716d77ac2","size":13497},
        {"name":"x264-20250608.b35605ac.tar.xz","sha256":"d44e13fa7a3da4d168fd97a1bb0f3c01fc9ee351aa4d24d2f1d72369c155d95b","size":747276}],
      "nopatch":[],"nosource":[],"patch":[],
      "source":["baselibs.conf","x264-20250608.b35605ac.tar.xz"],
      "spec_names":["libx264.spec"]},
    "sha256":"9a4557fdef0db5e419865808cdeb7f140808d90c6089c26dd65cc6d4ac40524a","size":784155,
    "url":"https://ftp.gwdg.de/pub/linux/misc/packman/suse/openSUSE_Tumbleweed/Essentials/src/libx264-20250608.b35605ac-1699.1.pm.18.src.rpm",
    "vendor":"http://packman.links2linux.de"}]
}""")
FIELDS = ("name", "version", "release", "arch", "vendor", "sourcerpm", "license")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def put(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


def document(root, name, value):
    return put(root, name, json.dumps(value, indent=2))


def tsv(rows):
    return "".join("\t".join(row[key] for key in FIELDS) + "\n" for row in rows)


def install_wheels(root, gate, monkeypatch, architecture="amd64"):
    """Real audited index shapes; synthetic ELF headers, no wheel execution."""
    destination = root / "usr/share/doc/ecm/python-third-party/artifact-index"
    shutil.copytree(ROOT / "docker/python-third-party/artifact-index", destination)
    put(root, "app/requirements.txt", (ROOT / "backend/requirements.txt").read_bytes())
    index = json.loads((destination / f"linux-{architecture}.json").read_text())
    native = {
        re.sub(r"[-_.]+", "-", item["name"]).lower(): item
        for item in index["native_distributions"]
    }
    site = root / "opt/venv/lib/python3.12/site-packages"
    site.mkdir(parents=True)
    stand_ins = {}
    for line in (ROOT / "backend/requirements.txt").read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, version = line.split("==")
        item = native.get(name)
        metadata_dir = name.replace("-", "_") + f"-{version}.dist-info"
        metadata_name = metadata_dir + "/METADATA"
        path = put(
            site,
            metadata_name,
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        )
        records = [metadata_name]
        if item is not None:
            stand_ins[path] = (path.read_bytes(), item["metadata_sha256"])
            for entry in item["native_files"]:
                header = bytearray(64)
                header[:7] = b"\x7fELF\x02\x01\x01"
                header[18:20] = index["elf_machine"].to_bytes(2, "little")
                path = put(site, entry["path"], bytes(header) + entry["path"].encode())
                stand_ins[path] = (path.read_bytes(), entry["sha256"])
                records.append(entry["path"])
        put(site, metadata_dir + "/RECORD", "".join(f"{name},,\n" for name in records))
    real_sha = gate.sha256

    def simulated_sha(path):
        if path in stand_ins and path.read_bytes() == stand_ins[path][0]:
            return stand_ins[path][1]
        return real_sha(path)

    monkeypatch.setattr(gate, "sha256", simulated_sha)
    return index, site


@pytest.fixture
def gate():
    spec = importlib.util.spec_from_file_location(
        "source_checks", ROOT / "tests/native_image/source_checks.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def image(tmp_path, gate, monkeypatch):
    index = copy.deepcopy(RECORDED)
    index.update(schema=1, architecture="x86_64")
    source = index["sources"][0]
    data = b"synthetic complete archive stand-in, NOT a source RPM"
    source.update(sha256=digest(data), size=len(data))
    put(tmp_path, f"{RPM}/{source['path']}", data)
    inventory = tsv(index["packages"] + index["excluded_public_keys"])
    put(tmp_path, f"{RPM}/inventory.tsv", inventory)
    index["inventory_sha256"] = digest(inventory.encode())
    index["package_set_sha256"] = canonical(index["packages"])
    index["source_set_sha256"] = canonical(
        [
            {
                key: item[key]
                for key in ("filename", "vendor", "url", "sha256", "fingerprint")
            }
            for item in index["sources"]
        ]
    )
    document(tmp_path, f"{RPM}/index.json", index)
    monkeypatch.setattr(gate.platform, "machine", lambda: "x86_64")
    calls = []

    def rpm(args, **kwargs):
        calls.append(args)
        assert args == [
            "/usr/bin/rpm",
            "--root",
            str(tmp_path),
            "-qa",
            "--qf",
            "\\t".join("%{" + key.upper() + "}" for key in FIELDS) + "\\n",
        ]
        assert kwargs["check"] is True and kwargs["timeout"] == 30
        return type("Result", (), {"stdout": inventory})()

    monkeypatch.setattr(gate.subprocess, "run", rpm)
    # Copy the real frozen recipe and scripts, not a mock compliance schema.
    shutil.copytree(ROOT / "docker/native", tmp_path / OWNED / "recipe")
    shutil.copy2(ROOT / "scripts/build_native_runtime.py", tmp_path / OWNED / "recipe")
    recipe = json.loads((tmp_path / OWNED / "recipe/manifest.json").read_text())
    source_hashes = {}
    for name, sha in (
        ("ffmpeg-8.1.3.tar.xz", recipe["ffmpeg"]["sha256"]),
        ("ffmpeg-8.1.3.tar.xz.asc", recipe["ffmpeg"]["signature"]["sha256"]),
        ("gosu.tar.gz", recipe["gosu"]["sha256"]),
    ):
        path = put(tmp_path, f"{OWNED}/{name}", "synthetic " + name)
        source_hashes[path] = (path.read_bytes(), sha)
    real_sha = gate.sha256

    def archive_sha(path):
        # Only recognized unchanged stand-ins get the authentic archive pin.
        if path in source_hashes and path.read_bytes() == source_hashes[path][0]:
            return source_hashes[path][1]
        return real_sha(path)

    monkeypatch.setattr(gate, "sha256", archive_sha)
    patch = recipe["ffmpeg_patches"][0]
    tree = [
        {"path": w["path"], "sha256": w["after_sha256"]} for w in patch["witnesses"]
    ]
    document(tmp_path, f"{OWNED}/ffmpeg-patched-source-files.json", tree)
    put(tmp_path, f"{OWNED}/ffmpeg-config.log", "synthetic configure log")
    put(tmp_path, f"{OWNED}/gosu/main.go", "package main\nfunc main() {}\n")
    artifacts = []
    for name in ("ffmpeg", "ffprobe", "gosu"):
        put(tmp_path, "usr/local/bin/" + name, "synthetic " + name).chmod(0o755)
    for family in (
        "avcodec",
        "avdevice",
        "avfilter",
        "avformat",
        "avutil",
        "swresample",
        "swscale",
    ):
        path = put(
            tmp_path, f"usr/local/lib64/lib{family}.so.1.2.3", "synthetic " + family
        )
        path.with_name(f"lib{family}.so.1").symlink_to(path.name)
    for name in ("ffmpeg", "gosu"):
        put(tmp_path, f"usr/local/share/licenses/{name}/LICENSE", "synthetic notice")
    put(tmp_path, "etc/ld.so.conf.d/ecm-native.conf", "/usr/local/lib64\n")
    for path in sorted(tmp_path.rglob("*")):
        name = str(path.relative_to(tmp_path))
        if not name.startswith(("usr/local/", "etc/")) or path.is_dir():
            continue
        if path.is_symlink():
            artifacts.append(
                {
                    "path": name,
                    "type": "symlink",
                    "target": path.readlink().as_posix(),
                    "target_sha256": real_sha(path.resolve()),
                }
            )
        else:
            artifacts.append(
                {
                    "path": name,
                    "type": "file",
                    "sha256": real_sha(path),
                    "size": path.stat().st_size,
                    "mode": path.stat().st_mode & 0o777,
                }
            )
    files = [
        {
            "path": "sources/" + str(path.relative_to(tmp_path / OWNED)),
            "sha256": gate.sha256(path),
            "size": path.stat().st_size,
        }
        for path in sorted((tmp_path / OWNED).rglob("*"))
        if path.is_file()
    ]
    source_list = document(tmp_path, f"{PROOF}/native-sources.json", files)
    record = {
        "schema_version": 1,
        "status": "PASS",
        "architecture": "amd64",
        "rpm_architecture": "x86_64",
        "base_image": recipe["base_image"],
        "manifest_sha256": real_sha(tmp_path / OWNED / "recipe/manifest.json"),
        "helper_sha256": real_sha(tmp_path / OWNED / "recipe/build_native_runtime.py"),
        "source_identity": {key: recipe[key] for key in ("ffmpeg", "gosu")},
        "source_files_sha256": real_sha(source_list),
        "owned_artifacts": artifacts,
        "ffmpeg": {
            "programs": {
                name: {
                    "sha256": real_sha(tmp_path / "usr/local/bin" / name),
                    "architecture": "amd64",
                }
                for name in ("ffmpeg", "ffprobe")
            },
            "configure_args": (ROOT / "docker/native/ffmpeg.args")
            .read_text()
            .splitlines(),
            "patches": [
                {
                    **patch,
                    "apply_output": "patching file libavformat/rtsp.c\n",
                    "witnesses": [
                        {**w, "actual_after_sha256": w["after_sha256"]}
                        for w in patch["witnesses"]
                    ],
                }
            ],
            "patched_source_files_sha256": real_sha(
                tmp_path / OWNED / "ffmpeg-patched-source-files.json"
            ),
        },
        "gosu": {
            "sha256": real_sha(tmp_path / "usr/local/bin/gosu"),
            "architecture": "amd64",
            "compiler_version": "go version go1.26.10 linux/amd64\n",
            "modules": [
                {"Path": "github.com/tianon/gosu", "Main": True},
                *[
                    {"Path": name, "Version": version}
                    for name, version in recipe["gosu"]["modules"].items()
                ],
            ],
        },
    }
    document(tmp_path, f"{PROOF}/native-build.json", record)
    resdet = "usr/share/ecm/sources/resdet"
    for name in ("build_resdet.py", "generate_sbom.py"):
        shutil.copy2(ROOT / "scripts" / name, put(tmp_path, f"{resdet}/{name}", ""))
    pins = json.loads((ROOT / "sbom/native-dependencies.json").read_text())
    resdet_bytes = b"synthetic full resdet archive"
    pins["subjects"]["ecm"]["packages"][0]["build"]["archiveSha256"] = digest(
        resdet_bytes
    )
    document(tmp_path, f"{resdet}/native-dependencies.json", pins)
    put(tmp_path, f"{resdet}/resdet.tar.gz", resdet_bytes)
    # Real manifests AND real accessible source/notice files, no archive parsing.
    shutil.copytree(
        ROOT / "docker/python-third-party/sources",
        tmp_path / "usr/share/ecm/sources/python",
    )
    shutil.copytree(
        ROOT / "docker/python-third-party/notices",
        tmp_path / "usr/share/doc/ecm/python-third-party",
    )
    # Parent is regenerating the real notice manifest after adding six ARM
    # metadata files. Model that coherent delivery, without editing its files or
    # accepting an incomplete manifest in the runtime helper.
    notices = tmp_path / "usr/share/doc/ecm/python-third-party"
    sums = "".join(
        f"{digest(path.read_bytes())}  {path.relative_to(notices)}\n"
        for path in sorted(notices.rglob("*"))
        if path.is_file() and path.name != "SHA256SUMS"
    )
    (notices / "SHA256SUMS").write_text(sums)
    assert digest(sums.encode()) == gate.PYTHON_SUMS["notices"]
    install_wheels(tmp_path, gate, monkeypatch)
    return tmp_path, index, record, calls


def rewrite(image, area, change):
    root, index, record, _ = image
    value = index if area == "index" else record
    change(value)
    document(
        root,
        f"{RPM}/index.json" if area == "index" else f"{PROOF}/native-build.json",
        value,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_index",
        "empty_sources",
        "empty_packages",
        "wrong_arch",
        "wrong_fingerprint",
        "duplicate_source",
        "omitted_origin",
        "source_escape",
        "symlink_archive",
        "archive_tamper",
        "empty_hash",
        "wrong_package_hash",
        "wrong_source_hash",
        "unvalidated_key",
        "payload_incomplete",
        "payload_omission",
        "extra_archive",
        "pass_only",
        "empty_artifacts",
        "missing_binary",
        "artifact_tamper",
        "artifact_mode",
        "artifact_symlink",
        "duplicate_artifact",
        "source_list_tamper",
        "source_tamper",
        "recipe_missing",
        "patch_witness",
        "patch_order",
        "compiler",
        "modules",
        "resdet_archive",
        "resdet_pin",
        "missing_build_script",
        "python_source",
        "python_notice",
        "empty_sums",
        "omitted_sum",
        "unindexed_notice",
        "source_archive_tamper",
    ],
)
def test_dangerous_delivery_mutants_fail_closed(gate, image, mutation):
    root, index, _record, _ = image
    source = index["sources"][0]
    archive = root / RPM / source["path"]
    if mutation == "missing_index":
        (root / RPM / "index.json").unlink()
    elif mutation == "empty_sources":
        rewrite(image, "index", lambda x: x.update(sources=[]))
    elif mutation == "empty_packages":
        rewrite(image, "index", lambda x: x.update(packages=[]))
    elif mutation == "wrong_arch":
        rewrite(image, "index", lambda x: x.update(architecture="aarch64"))
    elif mutation == "wrong_fingerprint":
        rewrite(image, "index", lambda x: x["sources"][0].update(fingerprint="0" * 40))
    elif mutation == "duplicate_source":
        rewrite(image, "index", lambda x: x["sources"].append(copy.deepcopy(source)))
    elif mutation == "omitted_origin":
        rewrite(
            image,
            "index",
            lambda x: x["packages"][0].update(sourcerpm="absent-1-1.src.rpm"),
        )
    elif mutation == "source_escape":
        rewrite(image, "index", lambda x: x["sources"][0].update(path="../escape"))
    elif mutation == "symlink_archive":
        archive.unlink()
        archive.symlink_to(put(root, "outside", b"untrusted"))
    elif mutation == "archive_tamper":
        archive.write_bytes(b"truncated archive")
    elif mutation == "empty_hash":
        rewrite(image, "index", lambda x: x["sources"][0].update(sha256=""))
    elif mutation in ("wrong_package_hash", "wrong_source_hash"):
        rewrite(
            image,
            "index",
            lambda x: x.update(
                {
                    "package_set_sha256"
                    if mutation == "wrong_package_hash"
                    else "source_set_sha256": "0" * 64
                }
            ),
        )
    elif mutation == "unvalidated_key":
        rewrite(
            image, "index", lambda x: x["excluded_public_keys"][0].update(license="GPL")
        )
    elif mutation == "payload_incomplete":
        rewrite(
            image,
            "index",
            lambda x: x["sources"][0]["payload"].update(
                declared_input_payload_complete=False
            ),
        )
    elif mutation == "payload_omission":
        rewrite(
            image, "index", lambda x: x["sources"][0]["payload"].update(nosource=[1])
        )
    elif mutation == "extra_archive":
        put(root, f"{RPM}/sources/unindexed.src.rpm", b"unexpected")
    elif mutation == "pass_only":
        document(root, f"{PROOF}/native-build.json", {"status": "PASS"})
    elif mutation == "empty_artifacts":
        rewrite(image, "record", lambda x: x.update(owned_artifacts=[]))
    elif mutation == "missing_binary":
        (root / "usr/local/bin/ffprobe").unlink()
    elif mutation == "artifact_tamper":
        (root / "usr/local/bin/ffmpeg").write_bytes(b"substituted")
    elif mutation == "artifact_mode":
        (root / "usr/local/bin/gosu").chmod(0o4755)
    elif mutation == "artifact_symlink":
        path = root / "usr/local/lib64/libavcodec.so.1"
        path.unlink()
        path.symlink_to("/etc/passwd")
    elif mutation == "duplicate_artifact":
        rewrite(
            image,
            "record",
            lambda x: x["owned_artifacts"].append(x["owned_artifacts"][0]),
        )
    elif mutation == "source_list_tamper":
        document(root, f"{PROOF}/native-sources.json", [])
    elif mutation == "source_tamper":
        (root / OWNED / "gosu/main.go").write_text("substituted source")
    elif mutation == "recipe_missing":
        (root / OWNED / "recipe/build_native_runtime.py").unlink()
    elif mutation == "patch_witness":
        rewrite(
            image,
            "record",
            lambda x: x["ffmpeg"]["patches"][0]["witnesses"][0].update(
                actual_after_sha256="0" * 64
            ),
        )
    elif mutation == "patch_order":
        rewrite(image, "record", lambda x: x["ffmpeg"]["patches"][0].update(order=2))
    elif mutation == "compiler":
        rewrite(
            image,
            "record",
            lambda x: x["gosu"].update(
                compiler_version="go version go1.26.9 linux/amd64\n"
            ),
        )
    elif mutation == "modules":
        rewrite(image, "record", lambda x: x["gosu"].update(modules=[]))
    elif mutation == "resdet_archive":
        (root / "usr/share/ecm/sources/resdet/resdet.tar.gz").write_bytes(
            b"wrong full archive"
        )
    elif mutation == "resdet_pin":
        path = root / "usr/share/ecm/sources/resdet/native-dependencies.json"
        pins = json.loads(path.read_text())
        pins["subjects"]["ecm"]["packages"][0]["build"]["archiveSha256"] = ""
        path.write_text(json.dumps(pins))
    elif mutation == "missing_build_script":
        (root / "usr/share/ecm/sources/resdet/build_resdet.py").unlink()
    elif mutation in ("python_source", "python_notice"):
        directory = root / (
            "usr/share/ecm/sources/python"
            if mutation == "python_source"
            else "usr/share/doc/ecm/python-third-party"
        )
        name = (directory / "SHA256SUMS").read_text().splitlines()[0].split("  ")[1]
        (directory / name).write_bytes(b"substituted source or notice")
    elif mutation in ("empty_sums", "omitted_sum"):
        path = root / "usr/share/ecm/sources/python/SHA256SUMS"
        path.write_text(
            ""
            if mutation == "empty_sums"
            else "\n".join(path.read_text().splitlines()[1:])
        )
    elif mutation == "unindexed_notice":
        put(
            root,
            "usr/share/doc/ecm/python-third-party/unindexed.txt",
            "unlisted notice",
        )
    else:
        (root / OWNED / "ffmpeg-8.1.3.tar.xz").write_bytes(b"wrong ffmpeg archive")
    with pytest.raises((AssertionError, OSError, ValueError, KeyError, TypeError)):
        gate.native_source_and_artifact_integrity(root)


def test_complete_delivery_binds_live_rpm_database_and_actual_paths(gate, image):
    root, index, _, calls = image
    result = gate.native_source_and_artifact_integrity(root)[
        "native_source_and_artifact_integrity"
    ]
    assert result["architecture"] == "x86_64"
    assert result["rpm_packages"] == result["rpm_sources"] == 1
    assert result["python_sources"] == 5 and result["python_notices"] >= 448
    bindings = result["source_bindings"]
    assert bindings["rpm_index"] == index
    assert bindings["rpm_index_sha256"] == digest(
        (root / RPM / "index.json").read_bytes()
    )
    record = root / PROOF / "native-build.json"
    assert bindings["native_build"] == json.loads(record.read_text())
    assert bindings["native_build_sha256"] == digest(record.read_bytes())
    assert calls, "source-builder inventory must not replace the live RPM query"
    assert "not a vulnerability scan" in result["scope"]


def test_changed_live_rpm_database_cannot_reuse_recorded_inventory(
    gate, image, monkeypatch
):
    root, index, _, _ = image
    changed = copy.deepcopy(index["packages"])
    changed[0]["version"] = "different"
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"stdout": tsv(changed)})(),
    )
    with pytest.raises(AssertionError, match="live RPM"):
        gate.native_source_and_artifact_integrity(root)


def test_frozen_recipe_and_python_manifest_pins_match_real_files(gate):
    assert gate.sha256(ROOT / "docker/native/manifest.json") == gate.RECIPE_SHA256
    for group, expected in gate.PYTHON_SUMS.items():
        directory = ROOT / f"docker/python-third-party/{group}"
        if group == "notices":
            # Verify all actual bytes, including the six ARM JSONs pending the
            # parent's real SHA256SUMS regeneration. No source files are edited.
            sums = "".join(
                f"{digest(path.read_bytes())}  {path.relative_to(directory)}\n"
                for path in sorted(directory.rglob("*"))
                if path.is_file() and path.name != "SHA256SUMS"
            )
            assert digest(sums.encode()) == expected
        else:
            assert gate.sha256(directory / "SHA256SUMS") == expected
    for name, expected in gate.RESDET_SCRIPTS.items():
        assert gate.sha256(ROOT / "scripts" / name) == expected


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "omitted", "escape", "size_none", "extra_file", "symlink_parent"],
)
def test_rehashed_owned_source_index_cannot_hide_bad_delivery(gate, image, mutation):
    root, _, record, _ = image
    path = root / PROOF / "native-sources.json"
    entries = json.loads(path.read_text())
    if mutation == "duplicate":
        entries.append(entries[0])
    elif mutation == "omitted":
        entries.pop()
    elif mutation == "escape":
        entries[0]["path"] = "sources/../outside"
    elif mutation == "size_none":
        entries[0]["size"] = None
    elif mutation == "extra_file":
        put(root, f"{OWNED}/unindexed.txt", "unindexed source")
    else:
        directory = root / OWNED / "gosu"
        directory.rename(root / "moved-source")
        directory.symlink_to(root / "moved-source", target_is_directory=True)
    path.write_text(json.dumps(entries))
    record["source_files_sha256"] = digest(path.read_bytes())
    document(root, f"{PROOF}/native-build.json", record)
    with pytest.raises(AssertionError):
        gate.native_source_and_artifact_integrity(root)


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_arch",
        "wrong_sources",
        "omitted_artifact",
        "source_identity",
        "wrong_program_hash",
        "patch_tree",
        "no_patch",
        "duplicate_modules",
    ],
)
def test_self_consistent_pass_record_still_requires_producer_contract(
    gate, image, mutation
):
    root, _, record, _ = image
    if mutation == "wrong_arch":
        record["architecture"] = "arm64"
    elif mutation == "wrong_sources":
        record["source_identity"]["ffmpeg"]["version"] = "8.1.2"
    elif mutation == "omitted_artifact":
        record["owned_artifacts"] = [
            a
            for a in record["owned_artifacts"]
            if not a["path"].endswith("libavcodec.so.1")
        ]
    elif mutation == "source_identity":
        record["source_identity"]["gosu"]["commit"] = "0" * 40
    elif mutation == "wrong_program_hash":
        record["ffmpeg"]["programs"]["ffprobe"]["sha256"] = "0" * 64
    elif mutation == "patch_tree":
        path = root / OWNED / "ffmpeg-patched-source-files.json"
        tree = json.loads(path.read_text())
        tree[0]["sha256"] = "0" * 64
        path.write_text(json.dumps(tree))
        record["ffmpeg"]["patched_source_files_sha256"] = digest(path.read_bytes())
        listing = root / PROOF / "native-sources.json"
        entries = json.loads(listing.read_text())
        entry = next(
            e
            for e in entries
            if e["path"] == "sources/ffmpeg-patched-source-files.json"
        )
        entry.update(sha256=digest(path.read_bytes()), size=path.stat().st_size)
        listing.write_text(json.dumps(entries))
        record["source_files_sha256"] = digest(listing.read_bytes())
    elif mutation == "no_patch":
        record["ffmpeg"]["patches"] = []
    else:
        record["gosu"]["modules"].append(record["gosu"]["modules"][-1])
    document(root, f"{PROOF}/native-build.json", record)
    with pytest.raises(AssertionError):
        gate.native_source_and_artifact_integrity(root)


@pytest.mark.parametrize(
    "rows",
    [
        "",
        "gpg-pubkey\t29b700a4\t6a17fa38\t(none)\t(none)\t(none)\tGPL\n",
        "libx264\t1\t1\tx86_64\tunknown\tx264-1-1.src.rpm\tGPL\n",
    ],
)
def test_live_inventory_cannot_bypass_validation(gate, image, monkeypatch, rows):
    root = image[0]
    monkeypatch.setattr(
        gate.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": rows})()
    )
    with pytest.raises(AssertionError):
        gate.native_source_and_artifact_integrity(root)


def test_live_rpm_nonzero_is_not_a_successful_inventory(gate, image, monkeypatch):
    def failed(*args, **kwargs):
        raise gate.subprocess.CalledProcessError(1, args[0], output="plausible stdout")

    monkeypatch.setattr(gate.subprocess, "run", failed)
    with pytest.raises(gate.subprocess.CalledProcessError):
        gate.native_source_and_artifact_integrity(image[0])


def test_only_validated_live_public_key_rows_can_be_excluded(gate, image, monkeypatch):
    root, index, _, _ = image
    key = copy.deepcopy(index["excluded_public_keys"][0])
    key["sourcerpm"] = "hidden-installed-package-1-1.src.rpm"
    rows = tsv(index["packages"] + [key])
    monkeypatch.setattr(
        gate.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": rows})()
    )
    with pytest.raises(AssertionError, match="public-key"):
        gate.native_source_and_artifact_integrity(root)


def test_runtime_contract_retains_27_checks_and_requires_delivery_as_28th():
    checks = json.loads((ROOT / "tests/native_image/checks.json").read_text())
    assert len(checks) == len(set(checks)) == 28
    assert checks[-1] == "native_source_and_artifact_integrity"
    assert {
        "synthetic_rare_input_decode",
        "production_prober_ts",
        "production_prober_aes_hls",
        "production_prober_cancellation",
        "system_default_ca_trust",
        "certifi_ca_trust",
    } <= set(checks)


@pytest.mark.parametrize("architecture,expected", [("amd64", 39), ("arm64", 36)])
def test_recorded_architecture_indexes_bind_all_installed_native_files(
    gate, tmp_path, monkeypatch, architecture, expected
):
    install_wheels(tmp_path, gate, monkeypatch, architecture)
    assert gate._python_artifacts(
        tmp_path, "x86_64" if architecture == "amd64" else "aarch64"
    ) == (74, 22, expected)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_index",
        "wrong_arch",
        "empty_inventory",
        "omitted_file",
        "duplicate_file",
        "wrong_lock",
        "wrong_version",
        "duplicate_dist",
        "missing_dist",
        "elf_tamper",
        "elf_machine",
        "metadata_tamper",
        "path_escape",
        "symlink_escape",
        "unindexed_elf",
        "fake_pass",
    ],
)
def test_python_attribution_dangerous_mutants_fail(
    gate, tmp_path, monkeypatch, mutation
):
    index, site = install_wheels(tmp_path, gate, monkeypatch)
    manifest = (
        tmp_path
        / "usr/share/doc/ecm/python-third-party/artifact-index/linux-amd64.json"
    )
    entry = index["native_distributions"][0]["native_files"][0]
    native = site / entry["path"]
    if mutation == "missing_index":
        manifest.unlink()
    elif mutation == "wrong_arch":
        index["architecture"] = "linux/arm64"
    elif mutation == "empty_inventory":
        index["native_distributions"] = []
        index["native_distribution_count"] = index["native_ELF_count"] = 0
    elif mutation == "omitted_file":
        index["native_distributions"][0]["native_files"].pop()
        index["native_ELF_count"] -= 1
    elif mutation == "duplicate_file":
        index["native_distributions"][0]["native_files"].append(entry)
        index["native_ELF_count"] += 1
    elif mutation == "wrong_lock":
        (tmp_path / "app/requirements.txt").write_text("not the app lock")
    elif mutation == "wrong_version":
        path = next(site.glob("aiohttp-*.dist-info/METADATA"))
        path.write_text(path.read_text().replace("3.14.5", "3.14.4"))
    elif mutation == "duplicate_dist":
        shutil.copytree(
            next(site.glob("aiohttp-*.dist-info")), site / "duplicate-1.dist-info"
        )
    elif mutation == "missing_dist":
        next(site.glob("aiohttp-*.dist-info/METADATA")).unlink()
    elif mutation == "elf_tamper":
        native.write_bytes(native.read_bytes() + b"substituted")
    elif mutation == "elf_machine":
        # Keep the hash stand-in returning the approved digest to isolate the
        # independent machine check (a hash comparison alone is insufficient).
        data = bytearray(native.read_bytes())
        data[18:20] = (183).to_bytes(2, "little")
        native.write_bytes(data)
        real_sha = gate.sha256
        monkeypatch.setattr(
            gate,
            "sha256",
            lambda path: entry["sha256"] if path == native else real_sha(path),
        )
    elif mutation == "metadata_tamper":
        path = next(site.glob("aiohttp-*.dist-info/METADATA"))
        path.write_bytes(path.read_bytes() + b"Summary: substituted\n")
    elif mutation == "path_escape":
        entry["path"] = "../outside.so"
    elif mutation == "symlink_escape":
        native.unlink()
        native.symlink_to(put(tmp_path, "outside.so", b"untrusted"))
    elif mutation == "unindexed_elf":
        put(site, "unexpected.so", native.read_bytes())
    else:
        index = {"status": "PASS"}
    if mutation in {
        "wrong_arch",
        "empty_inventory",
        "omitted_file",
        "duplicate_file",
        "path_escape",
        "fake_pass",
    }:
        manifest.write_text(json.dumps(index))
        # Exercise semantic validation as well as the pinned-index check.
        monkeypatch.setitem(
            gate.PYTHON_INDEXES, "x86_64", digest(manifest.read_bytes())
        )
    with pytest.raises((AssertionError, OSError, KeyError, TypeError, ValueError)):
        gate._python_artifacts(tmp_path, "x86_64")
