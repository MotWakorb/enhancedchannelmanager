"""Check final-image delivery against the frozen producers, not CVE clearance.

Stream archive hashes only. Signature/header/payload authentication belongs to
the trusted source-builder; no archive extraction, GPG, or source code execution.
"""

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import stat
import subprocess
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

RECIPE_SHA256 = "7ae24969cf1636679bdf766242ee2c716683b112241da009fe20899c76aa9a46"
PYTHON_SUMS = {
    "sources": "e21e0520773c78b5f16ee56ddb94073a4def902d8656b435fec9dc26eee8e2ab",
    # Complete sorted 556-entry manifest, including six newly audited ARM JSONs.
    "notices": "93e5d9125e15f9ae67c4e3ff9cfb5cd932adc2e2aec7da738d6723d75da1a006",
}
RESDET_SCRIPTS = {
    "build_resdet.py": "1f071637352c3e21abc94388dd4101c7d819998cc31b94cd5a34af939febf611",
}
PYTHON_INDEXES = {
    "x86_64": "ae61f69b63d43435e8afaa990f4c68a8e561ace7f51fab62b42792973e2a42cc",
    "aarch64": "7316c140d663a3723cff51744428e6f2c47d48627f70d7e857591b6c7954d02a",
}
FIELDS = ("name", "version", "release", "arch", "vendor", "sourcerpm", "license")
QUERY = "\\t".join("%{" + field.upper() + "}" for field in FIELDS) + "\\n"
VENDORS = {
    "openSUSE": "AD485664E901B867051AB15F35A2F86E29B700A4",
    "http://packman.links2linux.de": "F8875B880D518B6B8C530D1345A1D0671ABD1AFB",
}
# Independent consumer pins for the two authenticated historical AMD64 sources.
# Exact URL/hash scope is exercised by test_native_source_delivery.
EXACT_SOURCE_ROUTES = {
    ("openSUSE", "x86_64", "harfbuzz-14.6.0-1.1.src.rpm"): {
        "url": "https://slc-mirror.opensuse.org/source/tumbleweed/repo/oss/src/harfbuzz-14.6.0-1.1.src.rpm",
        "sha256": "53e21e5cb510895c46935f1f6325415846549316e200fb88a69d2a66b3fbc65f",
    },
    ("openSUSE", "x86_64", "openSUSE-release-20261023-4366.1.src.rpm"): {
        "url": "https://ftp.gwdg.de/pub/opensuse/source/tumbleweed/repo/oss/src/openSUSE-release-20261023-4366.1.src.rpm",
        "sha256": "0ca30fbc607834c8c5633ffe977f9d7bc1bd766a430976fec09185ae54d2869e",
    },
}
LIBRARIES = {
    "avcodec",
    "avdevice",
    "avfilter",
    "avformat",
    "avutil",
    "swresample",
    "swscale",
}


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _relative(name):
    assert isinstance(name, str) and name and "\\" not in name
    path = PurePosixPath(name)
    assert (
        path.parts
        and not path.is_absolute()
        and str(path) == name
        and ".." not in path.parts
    )
    assert all(ord(char) >= 32 for char in name), "control character in path"
    return path


def _path(root, name, *, link=False):
    relative = _relative(name)
    path = root / relative
    assert path.resolve().is_relative_to(root.resolve()), "path/symlink escape"
    for parent in relative.parents:
        assert not (root / parent).is_symlink(), "symlink directory"
    assert link or not path.is_symlink(), "unexpected symlink"
    return path


def _file(root, name):
    path = _path(root, name)
    assert path.is_file(), f"missing regular file: {name}"
    return path


def _hash(path, expected, *size):
    assert isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected), (
        "invalid SHA256"
    )
    assert sha256(path) == expected, f"SHA256 mismatch: {path}"
    if size:
        assert (
            type(size[0]) is int and size[0] >= 0 and path.stat().st_size == size[0]
        ), f"size mismatch: {path}"


def _json(root, name):
    path = _file(root, name)
    assert 0 < path.stat().st_size <= 32 * 1024**2, "empty/oversized JSON index"

    def unique(pairs):
        result = {}
        for key, value in pairs:
            assert key not in result, "duplicate JSON key"
            result[key] = value
        return result

    return json.loads(path.read_text(), object_pairs_hook=unique)


def _canonical(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _files(root):
    assert root.is_dir() and not root.is_symlink(), f"missing directory: {root}"
    result = set()
    for path in root.rglob("*"):
        name = str(path.relative_to(root))
        _path(root, name)
        assert path.is_dir() or path.is_file(), "nonregular delivery entry"
        if path.is_file():
            result.add(name)
    return result


def _inventory(text, architecture):
    """Same seven-column contract as collect_native_sources.parse_inventory."""
    packages, keys, seen = [], [], set()
    for line in text.splitlines():
        values = line.split("\t")
        assert len(values) == 7 and all(
            v and all(ord(c) >= 32 for c in v) for v in values
        )
        row = dict(zip(FIELDS, values))
        identity = tuple(values[:4])
        assert identity not in seen, "duplicate RPM identity"
        seen.add(identity)
        if row["name"] == "gpg-pubkey":
            assert all(
                re.fullmatch(r"[0-9a-f]{8}", row[f]) for f in ("version", "release")
            )
            assert [row[f] for f in ("arch", "vendor", "sourcerpm", "license")] == [
                "(none)",
                "(none)",
                "(none)",
                "pubkey",
            ], "invalid public-key exclusion"
            keys.append(row)
            continue
        assert all(
            re.fullmatch(r"[A-Za-z0-9_+~.-]+", row[f]) and row[f] not in (".", "..")
            for f in ("name", "version", "release")
        )
        assert row["arch"] in (architecture, "noarch") and row["vendor"] in VENDORS
        assert re.fullmatch(r"[A-Za-z0-9_+~.-]+\.src\.rpm", row["sourcerpm"])
        assert row["license"] != "(none)"
        packages.append(row)
    assert packages, "empty package inventory"
    return sorted(packages, key=lambda r: tuple(r[f] for f in FIELDS)), keys


def _rows(rows):
    assert isinstance(rows, list)
    assert all(isinstance(row, dict) and set(row) == set(FIELDS) for row in rows)
    return "".join("\t".join(row[f] for f in FIELDS) + "\n" for row in rows)


def _rpm_sources(root, architecture):
    directory = _path(root, "usr/share/ecm/sources/rpm")
    index = _json(directory, "index.json")
    assert type(index["schema"]) is int and index["schema"] == 1
    assert index["architecture"] == architecture, "RPM source architecture mismatch"
    packages, keys = _inventory(
        _rows(index["packages"]) + _rows(index["excluded_public_keys"]), architecture
    )
    assert packages == index["packages"] and keys == index["excluded_public_keys"]
    expected = _canonical(packages)
    assert index["package_set_sha256"] == expected, "package-set checksum mismatch"
    inventory = _file(directory, "inventory.tsv")
    _hash(inventory, index["inventory_sha256"])
    assert _inventory(inventory.read_text(), architecture) == (packages, keys)
    live = subprocess.run(
        ["/usr/bin/rpm", "--root", str(root), "-qa", "--qf", QUERY],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "LC_ALL": "C"},
    ).stdout
    actual, _ = _inventory(live, architecture)
    assert _canonical(actual) == expected and actual == packages, (
        "live RPM package-set mismatch"
    )
    sources = index["sources"]
    assert isinstance(sources, list) and sources, "empty source set"
    origins = {(row["sourcerpm"], row["vendor"]) for row in packages}
    seen, projection, paths = set(), [], {"index.json", "inventory.tsv"}
    for source in sources:
        name, vendor = source["filename"], source["vendor"]
        assert re.fullmatch(r"[A-Za-z0-9_+~.-]+\.src\.rpm", name)
        assert name not in seen and (name, vendor) in origins, (
            "duplicate/unexpected source origin"
        )
        seen.add(name)
        assert source["fingerprint"] == VENDORS[vendor], "untrusted source fingerprint"
        repository = (
            "https://ftp.gwdg.de/pub/linux/misc/packman/suse/openSUSE_Tumbleweed/Essentials/"
            if vendor != "openSUSE"
            else "https://download.opensuse.org/tumbleweed/repo/src-oss/"
            if architecture == "x86_64"
            else "https://download.opensuse.org/ports/aarch64/source/tumbleweed/repo/oss/"
        )
        route = EXACT_SOURCE_ROUTES.get((vendor, architecture, name))
        assert source["url"] == (route["url"] if route else repository + "src/" + name)
        if route:
            assert source["sha256"] == route["sha256"], (
                "pinned source checksum mismatch"
            )
        assert source["path"] == "sources/" + name
        assert source["authentication"] == "pinned-rpm-signature-and-exact-header"
        assert source["license"] and source["license"] != "(none)"
        _hash(_file(directory, source["path"]), source["sha256"], source["size"])
        assert source["size"] > 0
        paths.add(source["path"])
        payload = source["payload"]
        assert payload["declared_input_payload_complete"] is True
        assert payload["nosource"] == payload["nopatch"] == []
        files = payload["files"]
        assert files and payload["spec_names"] and payload["source"] + payload["patch"]
        names = set()
        for item in files:
            assert len(_relative(item["name"]).parts) == 1 and item["name"] not in names
            names.add(item["name"])
            assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
            assert type(item["size"]) is int and item["size"] >= 0
        assert payload["spec_names"] == sorted(
            name for name in names if name.endswith(".spec")
        )
        assert {
            urlsplit(name).path.rsplit("/", 1)[-1]
            for name in payload["source"] + payload["patch"]
        } <= names
        projection.append(
            {
                key: source[key]
                for key in ("filename", "vendor", "url", "sha256", "fingerprint")
            }
        )
    assert {(source["filename"], source["vendor"]) for source in sources} == origins, (
        "omitted source origin"
    )
    assert index["source_set_sha256"] == _canonical(projection), (
        "source-set checksum mismatch"
    )
    assert _files(directory) == paths, "unindexed/omitted RPM source files"
    return len(packages), len(sources)


def _owned(root, architecture):
    directory = _path(root, "usr/share/ecm/sources/owned")
    record = _json(root, "usr/share/ecm/provenance/native-build.json")
    arch = {"x86_64": "amd64", "aarch64": "arm64"}[architecture]
    assert (
        record["status"] == "PASS"
        and type(record["schema_version"]) is int
        and record["schema_version"] == 1
    )
    assert record["architecture"] == arch and record["rpm_architecture"] == architecture
    manifest = _file(directory, "recipe/manifest.json")
    _hash(manifest, RECIPE_SHA256)
    _hash(manifest, record["manifest_sha256"])
    recipe = _json(directory, "recipe/manifest.json")
    _hash(_file(directory, "recipe/build_native_runtime.py"), record["helper_sha256"])
    assert record["base_image"] == recipe["base_image"]
    assert record["source_identity"] == {key: recipe[key] for key in ("ffmpeg", "gosu")}
    listing = _file(root, "usr/share/ecm/provenance/native-sources.json")
    _hash(listing, record["source_files_sha256"])
    entries = _json(root, "usr/share/ecm/provenance/native-sources.json")
    assert isinstance(entries, list) and entries, "empty owned source index"
    paths = set()
    for entry in entries:
        name = entry["path"]
        assert name.startswith("sources/") and name not in paths, (
            "duplicate/invalid owned source path"
        )
        paths.add(name)
        _hash(
            _file(directory, name.removeprefix("sources/")),
            entry["sha256"],
            entry["size"],
        )
    actual_sources = set()
    for path in directory.rglob("*"):
        name = str(path.relative_to(directory))
        if name == "gosu/.dockerignore" and path.is_symlink():
            # Reviewed upstream archive alias, omitted by the producer's regular-
            # file index. Bind it to the exact indexed target, not an allow-links
            # policy for sources, recipe assets, RPMs or Python trees.
            assert os.readlink(path) == ".gitignore", "unreviewed gosu alias target"
            target = _file(directory, "gosu/.gitignore")
            assert path.resolve(strict=True) == target
            witness = next(
                (item for item in entries if item["path"] == "sources/gosu/.gitignore"),
                None,
            )
            assert witness is not None, "unindexed gosu alias target"
            _hash(path, witness["sha256"], witness["size"])
            continue
        _path(directory, name)
        assert path.is_dir() or path.is_file(), "nonregular owned source entry"
        if path.is_file():
            actual_sources.add("sources/" + name)
    assert actual_sources == paths, "owned source omission"
    for name, expected in (
        ("ffmpeg-8.1.3.tar.xz", recipe["ffmpeg"]["sha256"]),
        ("ffmpeg-8.1.3.tar.xz.asc", recipe["ffmpeg"]["signature"]["sha256"]),
        ("gosu.tar.gz", recipe["gosu"]["sha256"]),
        ("recipe/ffmpeg.args", recipe["configure"]["sha256"]),
        ("recipe/ffmpeg-release.asc", recipe["ffmpeg"]["key"]["sha256"]),
        ("recipe/packman.asc", recipe["packman_key"]["sha256"]),
    ):
        _hash(_file(directory, name), expected)
    assert _file(directory, "ffmpeg-config.log").stat().st_size > 0
    assert any(
        name.startswith("sources/gosu/") and name.endswith(".go") for name in paths
    )
    ffmpeg = record["ffmpeg"]
    assert (
        ffmpeg["configure_args"]
        == _file(directory, "recipe/ffmpeg.args").read_text().splitlines()
    )
    _hash(
        _file(directory, "ffmpeg-patched-source-files.json"),
        ffmpeg["patched_source_files_sha256"],
    )
    tree = _json(directory, "ffmpeg-patched-source-files.json")
    witnesses = {}
    for item in tree:
        name = str(_relative(item["path"]))
        assert name not in witnesses and re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
        witnesses[name] = item["sha256"]
    assert len(ffmpeg["patches"]) == len(recipe["ffmpeg_patches"]) > 0
    for proof, patch in zip(ffmpeg["patches"], recipe["ffmpeg_patches"]):
        assert {
            k: v for k, v in proof.items() if k not in ("witnesses", "apply_output")
        } == {k: v for k, v in patch.items() if k != "witnesses"}
        _hash(_file(directory, "recipe/" + patch["file"]), patch["sha256"])
        assert proof["apply_output"].strip()
        assert proof["witnesses"] == [
            {**w, "actual_after_sha256": w["after_sha256"]} for w in patch["witnesses"]
        ]
        assert all(
            witnesses[w["path"]] == w["after_sha256"] for w in patch["witnesses"]
        )
    gosu = record["gosu"]
    assert (
        gosu["compiler_version"].strip()
        == f"go version go{recipe['gosu']['go_version']} linux/{arch}"
    )
    modules = [m for m in gosu["modules"] if not m.get("Main")]
    assert len(modules) == len(recipe["gosu"]["modules"])
    assert {m["Path"]: m["Version"] for m in modules} == recipe["gosu"]["modules"]
    artifacts = record["owned_artifacts"]
    assert isinstance(artifacts, list) and artifacts, "empty owned artifact map"
    mapped = {}
    for item in artifacts:
        name = item["path"]
        assert name not in mapped, "duplicate owned artifact"
        mapped[name] = item
        path = _path(root, name, link=item["type"] == "symlink")
        if item["type"] == "symlink":
            assert path.is_symlink() and os.readlink(path) == item["target"]
            assert not Path(item["target"]).is_absolute() and path.resolve().is_file()
            assert str(path.resolve().relative_to(root)) in {
                r["path"] for r in artifacts if r["type"] == "file"
            }
            _hash(path, item["target_sha256"])
        else:
            assert item["type"] == "file" and not path.is_symlink() and path.is_file()
            _hash(path, item["sha256"], item["size"])
            assert type(item["mode"]) is int and 0 <= item["mode"] <= 0o777
            assert path.stat().st_mode & 0o7777 == item["mode"], (
                "owned artifact mode mismatch"
            )
    for name in ("ffmpeg", "ffprobe", "gosu"):
        item = mapped["usr/local/bin/" + name]
        proof = gosu if name == "gosu" else ffmpeg["programs"][name]
        assert item["type"] == "file" and item["mode"] & 0o111 and item["size"] > 0
        assert proof["sha256"] == item["sha256"] and proof["architecture"] == arch
    assert {
        path.name.split(".so", 1)[0].removeprefix("lib")
        for path in (root / "usr/local/lib64").glob("lib*.so*")
    } == LIBRARIES
    # Compare only producer-owned namespaces; resdet and the venv are separate.
    actual = {"usr/local/bin/" + name for name in ("ffmpeg", "ffprobe", "gosu")}
    actual.add("etc/ld.so.conf.d/ecm-native.conf")
    for prefix in (
        "usr/local/lib64",
        "usr/local/share/licenses/ffmpeg",
        "usr/local/share/licenses/gosu",
    ):
        base = _path(root, prefix)
        assert base.is_dir()
        for path in base.rglob("*"):
            _path(root, str(path.relative_to(root)), link=True)
            if not path.is_dir():
                actual.add(str(path.relative_to(root)))
    assert actual == set(mapped), "owned artifact omission/unindexed file"
    return len(artifacts)


def _resdet(root):
    directory = _path(root, "usr/share/ecm/sources/resdet")
    pins = _json(directory, "native-dependencies.json")["subjects"]["ecm"]["packages"]
    matches = [p for p in pins if p["id"] == "resdet"]
    assert len(matches) == 1
    package = matches[0]
    assert package["version"] == "2.4.3" and package["build"]["pixelMax"] == 8_847_360
    assert re.fullmatch(r"[0-9a-f]{40}", package["build"]["commit"])
    _hash(_file(directory, "resdet.tar.gz"), package["build"]["archiveSha256"])
    for name, expected in RESDET_SCRIPTS.items():
        _hash(_file(directory, name), expected)
    assert _file(directory, "generate_sbom.py").stat().st_size > 0


def _python(root, group, prefix):
    directory = _path(root, prefix)
    sums = _file(directory, "SHA256SUMS")
    # Pin the reviewed manifest itself: an empty/truncated self-consistent set
    # must not turn removed source/notices into an apparent successful delivery.
    _hash(sums, PYTHON_SUMS[group])
    names = set()
    for line in sums.read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        assert match is not None, "malformed Python checksum manifest"
        expected, name = match.groups()
        assert name not in names, "duplicate Python source/notice entry"
        names.add(name)
        path = _file(directory, name)
        assert path.stat().st_size > 0, "empty Python source/notice"
        _hash(path, expected)
    assert names, "empty Python source/notice set"
    delivered = _files(directory) - {"SHA256SUMS"}
    if group == "notices":
        # These are separately copied delivery metadata, not notice-tree files.
        delivered -= {"component-manifest.json", "SOURCE-AVAILABILITY.txt"}
        delivered = {
            name for name in delivered if not name.startswith("artifact-index/")
        }
    assert delivered == names, "unindexed/omitted Python source/notice file"
    return len(names)


def _distribution_name(name):
    assert isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name)
    return re.sub(r"[-_.]+", "-", name).lower()


def _python_artifacts(root, architecture):
    """Read wheel attribution/installed metadata only; never import extensions."""
    arch, machine, count = {"x86_64": ("amd64", 62, 39), "aarch64": ("arm64", 183, 36)}[
        architecture
    ]
    name = f"usr/share/doc/ecm/python-third-party/artifact-index/linux-{arch}.json"
    _hash(_file(root, name), PYTHON_INDEXES[architecture])
    index = _json(root, name)
    assert index["architecture"] == "linux/" + arch and index["elf_machine"] == machine
    assert (
        type(index["native_distribution_count"]) is int
        and index["native_distribution_count"] == 22
    )
    assert type(index["native_ELF_count"]) is int and index["native_ELF_count"] == count
    lock = _file(root, "app/requirements.txt")
    _hash(lock, index["lock_sha256"])
    locked = {}
    for line in lock.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)", line)
        assert match is not None, "unexpected unpinned app requirement"
        package, version = match.groups()
        package = _distribution_name(package)
        assert package not in locked, "duplicate locked distribution"
        locked[package] = version
    assert len(locked) == 74, "incomplete application lock"
    site = _path(root, "opt/venv/lib/python3.12/site-packages")
    installed = {}
    for dist in importlib.metadata.distributions(path=[str(site)]):
        package = _distribution_name(dist.metadata["Name"])
        assert package not in installed, "duplicate installed distribution"
        installed[package] = dist
    assert {name: dist.version for name, dist in installed.items()} == locked, (
        "installed distribution names/versions differ from app lock"
    )
    distributions, expected_files = set(), set()
    for item in index["native_distributions"]:
        package = _distribution_name(item["name"])
        assert package not in distributions and locked[package] == item["version"]
        distributions.add(package)
        assert (
            item["filename"].endswith(".whl")
            and urlsplit(item["url"]).scheme == "https"
        )
        assert urlsplit(item["url"]).hostname == "files.pythonhosted.org" and urlsplit(
            item["url"]
        ).path.endswith("/" + item["filename"])
        assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"]), (
            "missing approved wheel SHA256"
        )
        metadata = [
            str(path)
            for path in installed[package].files or []
            if str(path).endswith(".dist-info/METADATA")
        ]
        assert len(metadata) == 1, "missing/ambiguous installed METADATA"
        _hash(_file(site, metadata[0]), item["metadata_sha256"])
        assert item["native_files"], "empty native distribution inventory"
        for entry in item["native_files"]:
            path = entry["path"]
            assert path not in expected_files, "duplicate indexed native file"
            expected_files.add(path)
            # Recorded x86 entries are flat; the ARM audit retains its full ELF
            # observation object. Hashes bind DT_NEEDED without another parser.
            recorded_machine = entry.get(
                "elf_machine", entry.get("elf", {}).get("machine")
            )
            assert type(recorded_machine) is int and recorded_machine == machine
            binary = _file(site, path)
            _hash(binary, entry["sha256"])
            with binary.open("rb") as stream:
                header = stream.read(64)
            assert len(header) == 64 and header[:7] == b"\x7fELF\x02\x01\x01"
            assert int.from_bytes(header[18:20], "little") == machine, (
                "native ELF machine mismatch"
            )
    assert len(distributions) == 22 and len(expected_files) == count, (
        "incomplete native attribution inventory"
    )
    actual_files = set()
    for path in _files(site):
        with (site / path).open("rb") as stream:
            if stream.read(4) == b"\x7fELF":
                actual_files.add(path)
    assert actual_files == expected_files, "unindexed/omitted installed native ELF"
    return len(installed), len(distributions), len(expected_files)


def _filesystem_names(root):
    """Complete name/metadata traversal only; no contents or symlink following."""
    assert stat.S_ISDIR(root.lstat().st_mode), "search root must be a directory"
    excluded = {
        "/proc": "virtual proc filesystem",
        "/sys": "virtual sys filesystem",
        "/dev": "virtual device filesystem",
        "/native-image": "external read-only gate fixture mount",
    }
    patterns = {
        "cjson": r"cjson",
        "libsndfile": r"libsndfile|sndfile|sf_open",
        "perl_archive_tar": r"(^|/)perl[^/]*(/|$)|(^|/)archive/tar(\.pm|/|$)|\.pm$",
        "libtiff": r"libtiff|tiffcrop",
    }
    selectors = {
        name: re.compile(pattern, re.IGNORECASE) for name, pattern in patterns.items()
    }
    matches = {name: [] for name in patterns}
    pending, symlinks, observed = [root], [], []
    count, inventory = 0, hashlib.sha256()
    while pending:
        # Unlike os.walk's optional error handler / is_dir probes, every scandir,
        # lstat-equivalent and readlink error propagates. Never publish partial data.
        with os.scandir(pending.pop()) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                path = Path(entry.path)
                name = "/" + str(path.relative_to(root))
                if name in excluded:
                    observed.append(name)
                    continue
                mode = entry.stat(follow_symlinks=False).st_mode
                target = os.readlink(path) if stat.S_ISLNK(mode) else None
                kind = (
                    "symlink"
                    if target is not None
                    else "directory"
                    if stat.S_ISDIR(mode)
                    else "file"
                    if stat.S_ISREG(mode)
                    else "other"
                )
                row = {"path": name, "type": kind}
                if target is not None:
                    row["target"] = target
                    symlinks.append({"path": name, "target": target})
                elif stat.S_ISDIR(mode):
                    pending.append(path)
                count += 1
                inventory.update(
                    (
                        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                    ).encode()
                )
                for family, selector in selectors.items():
                    if selector.search(name) or (
                        target is not None and selector.search(target)
                    ):
                        matches[family].append(name)
    assert count > 0, "empty filesystem name-search scope"
    return {
        "status": "COMPLETE",
        "exit_status": 0,
        "errors": [],
        "method": "Python os.scandir, stat(follow_symlinks=False), readlink; case-insensitive path and symlink-target regex search",
        "scope": "Complete running-image rootfs names, including /app, /opt/venv and copied/unowned providers, except the four stated mounts. Names inside compressed archives and file contents are not searched. Matches are review data, not CVE judgments; nonmatches are not universal static-component absence proof.",
        "excluded": excluded,
        "observed_exclusions": sorted(observed),
        "path_count": count,
        "inventory_sha256": inventory.hexdigest(),
        "patterns": patterns,
        "matches": {name: sorted(paths) for name, paths in matches.items()},
        "symlinks": sorted(symlinks, key=lambda row: row["path"]),
    }


def native_source_and_artifact_integrity(root=Path("/")):
    """Root is solely a temp-tree unit seam; production always checks `/`."""
    root = Path(root).resolve(strict=True)
    architecture = platform.machine()
    assert architecture in ("x86_64", "aarch64"), "unsupported image architecture"
    packages, sources = _rpm_sources(root, architecture)
    artifacts = _owned(root, architecture)
    _resdet(root)
    python_sources = _python(root, "sources", "usr/share/ecm/sources/python")
    python_notices = _python(root, "notices", "usr/share/doc/ecm/python-third-party")
    installed, native_distributions, native_elfs = _python_artifacts(root, architecture)
    # Preserve checked small inputs, not reconstructed/canonical JSON hashes.
    bindings = {}
    for name, path in (
        ("rpm_index", "usr/share/ecm/sources/rpm/index.json"),
        ("native_build", "usr/share/ecm/provenance/native-build.json"),
        ("native_sources", "usr/share/ecm/provenance/native-sources.json"),
    ):
        bindings[name] = _json(root, path)
        bindings[name + "_sha256"] = sha256(_file(root, path))
    assert (
        bindings["native_sources_sha256"]
        == bindings["native_build"]["source_files_sha256"]
    )
    log_name = "usr/share/ecm/sources/owned/ffmpeg-config.log"
    data = _file(root, log_name).read_bytes()
    log_hash = hashlib.sha256(data).hexdigest()
    entry = next(
        item
        for item in bindings["native_sources"]
        if item["path"] == "sources/ffmpeg-config.log"
    )
    assert log_hash == entry["sha256"] and len(data) == entry["size"], (
        "config log/source index mismatch"
    )
    bindings["ffmpeg_config_log"] = {
        "path": "/" + log_name,
        "sha256": log_hash,
        "size": len(data),
        "text": data.decode("utf-8"),
        "scope": "Bound compiler-argument context for manual review of the non-CHECKED condition; diagnostic/test mentions are not a flag judgment or advisory clearance.",
    }
    return {
        "native_source_and_artifact_integrity": {
            "architecture": architecture,
            "rpm_packages": packages,
            "rpm_sources": sources,
            "owned_artifacts": artifacts,
            "python_sources": python_sources,
            "python_notices": python_notices,
            "python_installed_distributions": installed,
            "python_native_distributions": native_distributions,
            "python_native_ELFs": native_elfs,
            "source_bindings": bindings,
            "filesystem_name_search": _filesystem_names(root),
            "scope": "Delivered bytes, live RPM correspondence and architecture-specific Python attribution; trusted source-builder authentication is not repeated; not a vulnerability scan. Custom binaries require separate advisory review. Only this running image architecture is verified; no ARM execution is inferred from x86 data.",
        }
    }
