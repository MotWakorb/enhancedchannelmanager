#!/usr/bin/env python3
"""Build the reviewed two-architecture FFmpeg/gosu recipe inside a SUSE builder.

Only locations and target architecture are CLI inputs. Source identities, keys,
configuration, compiler and module versions are reviewed, checked-in inputs.
This installs builder RPMs: do not invoke it on a host or application runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import threading
from pathlib import Path, PurePosixPath
from urllib.request import urlopen

BASE = "registry.opensuse.org/opensuse/tumbleweed@sha256:c807eabd03acb6f5dfb6a23fd58ed93700d11283a255cd18184f57815e19fc55"
PACKMAN = (
    "https://ftp.gwdg.de/pub/linux/misc/packman/suse/openSUSE_Tumbleweed/Essentials/"
)
FFMPEG_SHA = "7138d28c96d9d3e3af4ee3d8cad72741f8ffb40da90c1112235dea3ecd3178a3"
FFMPEG_FPR = "FCF986EA15E6E293A5644F10B4322F04D67658D8"
GOSU_COMMIT = "6456aaa0f3c854d199d0f037f068eb97515b7513"
GOSU_SHA = "33d7537d588ea49458b9509bcf4554bdf5ceacc66da71e5caa1058ea3b689c3b"
PACKMAN_FPR = "F8875B880D518B6B8C530D1345A1D0671ABD1AFB"
CONFIG_SHA = "8815506c9fd2a54139123c49a3b4c06facfb06008b125662aff3b7dc323ab8d3"
ARM_GO_TOOLCHAIN = {
    "url": "https://go.dev/dl/go1.26.10.linux-arm64.tar.gz",
    "sha256": "3f4227ce7b206a513e794f1393adb473a07b46e94d18bd15d1c684bbb7319797",
    "size": 63827526,
    "version": "go1.26.10",
    "os": "linux",
    "arch": "arm64",
    "elf_machine": 183,
    "source": {
        "url": "https://go.dev/dl/go1.26.10.src.tar.gz",
        "sha256": "fd63e883ba2f2bfca93c1646b31ebef63e2b1e4d2b317c9789d976a68d81f36f",
        "size": 34164741,
    },
}
REVIEWED_PATCHES = [
    {
        "order": 1,
        "id": "CVE-2026-107696",
        "upstream_commit": "f5d4fd186c68913240c4f6e9cda98443e1518b34",
        "upstream_url": "https://github.com/FFmpeg/FFmpeg/commit/f5d4fd186c68913240c4f6e9cda98443e1518b34",
        "file": "patches/CVE-2026-107696.patch",
        "sha256": "7518b281a719aa41c5cdd1f259eaf38f6b1e3fb28c09ec254c441abbaad69095",
        "witnesses": [
            {
                "path": "libavformat/rtsp.c",
                "before_sha256": "7ed3d7cd85feee1a63737054dbf9d2e7294f8a9e7851aaf72c829082987b4495",
                "after_sha256": "9e181b7cea30a6fbbd9c1bfa4f63ec2c9834001f9804edffb6d2b01984747145",
            }
        ],
    }
]
MODULES = {"github.com/moby/sys/user": "v0.4.1", "golang.org/x/sys": "v0.44.0"}
ARCHES = {
    "amd64": ("x86_64", "Advanced Micro Devices X86-64"),
    "arm64": ("aarch64", "AArch64"),
}
CODECS = {
    "libx264-165",
    "libx264-devel",
    "libx265-215",
    "libx265-devel",
    "libhdr10plus-4_1",
}
BUILDERS = {
    "gcc",
    "gcc-c++",
    "make",
    "binutils",
    "patch",
    "nasm",
    "pkgconf-pkg-config",
    "gawk",
    "tar",
    "xz",
    "gzip",
    "gpg2",
    "python312-base",
    "go1.26",
    "libaom-devel",
    "dav1d-devel",
    "fontconfig-devel",
    "freetype2-devel",
    "fribidi-devel",
    "harfbuzz-devel",
    "libass-devel",
    "codec2-devel",
    "libjxl-devel",
    "librsvg-devel",
    "libgme-devel",
    "libopenmpt-devel",
    "libmp3lame-devel",
    "libopus-devel",
    "libvorbis-devel",
    "libvpx-devel",
    "libwebp-devel",
    "zimg-devel",
    "libxml2-devel",
    "libopenssl-3-devel",
    "zlib-devel",
    "libbz2-devel",
    "xz-devel",
    "libgsm-devel",
    "openjpeg2-devel",
    "speex-devel",
    "libtheora-devel",
    "libtwolame-devel",
    "libshine-devel",
    "xvidcore-devel",
    "rav1e-devel",
    "SVT-AV1-devel",
    "snappy-devel",
}
LIBRARIES = {
    "libavcodec": "avcodec_version",
    "libavdevice": "avdevice_version",
    "libavfilter": "avfilter_version",
    "libavformat": "avformat_version",
    "libavutil": "avutil_version",
    "libswresample": "swresample_version",
    "libswscale": "swscale_version",
}
RPM_FORMAT = (
    "%{NAME}\t%{EPOCHNUM}\t%{VERSION}\t%{RELEASE}\t%{ARCH}\t%{SOURCERPM}\t%{LICENSE}\n"
)


class BuildError(RuntimeError):
    """A reviewed input or produced artifact failed a required build check."""


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BuildError(message)


def load_recipe(path: Path, arch: str) -> dict:
    try:
        recipe = json.loads(path.read_text())
        _require(
            arch in ARCHES and set(recipe["architectures"]) == set(ARCHES),
            "unsupported architecture",
        )
        _require(
            recipe["schema_version"] == 1 and recipe["base_image"] == BASE,
            "pinned recipe/base identity mismatch",
        )
        expected_ffmpeg = {
            "version": "8.1.3",
            "url": "https://ffmpeg.org/releases/ffmpeg-8.1.3.tar.xz",
            "sha256": FFMPEG_SHA,
            "signature": {
                "url": "https://ffmpeg.org/releases/ffmpeg-8.1.3.tar.xz.asc",
                "sha256": "5f2880c7fea01f8694d30cb9d06362f77241ee67ce5926b157a226b18f08a538",
            },
            "key": {
                "file": "ffmpeg-release.asc",
                "sha256": "397b3becedcd5a98769967ff1ff8501ddc89f8368b8f766e4701377d7dbaabe5",
            },
            "fingerprint": FFMPEG_FPR,
        }
        expected_gosu = {
            "version": "1.19",
            "commit": GOSU_COMMIT,
            "url": "https://codeload.github.com/tianon/gosu/tar.gz/" + GOSU_COMMIT,
            "sha256": GOSU_SHA,
            "modules": MODULES,
            "go_version": "1.26.10",
        }
        expected_key = {
            "file": "packman.asc",
            "sha256": "9ad00d51db9644a12945ce780f5e013eaa1d72d5d212b040d296925b9aec50c7",
            "fingerprint": PACKMAN_FPR,
        }
        _require(
            recipe["ffmpeg"] == expected_ffmpeg,
            "pinned FFmpeg source/signing identity mismatch",
        )
        _require(
            recipe["gosu"] == expected_gosu,
            "pinned gosu/module/compiler identity mismatch",
        )
        _require(
            recipe["packman_key"] == expected_key,
            "pinned Packman fingerprint/key identity mismatch",
        )
        _require(
            recipe["configure"] == {"file": "ffmpeg.args", "sha256": CONFIG_SHA},
            "pinned configure asset checksum mismatch",
        )
        for target, item in recipe["architectures"].items():
            rpm_arch = ARCHES[target][0]
            repository = (
                "https://download.opensuse.org/tumbleweed/repo/oss/"
                if target == "amd64"
                else "https://download.opensuse.org/ports/aarch64/tumbleweed/repo/oss/"
            )
            _require(
                item["rpm_arch"] == rpm_arch
                and item["official_repository"] == repository,
                "repository/architecture mismatch",
            )
            source_repository = (
                "https://download.opensuse.org/tumbleweed/repo/src-oss/"
                if target == "amd64"
                else "https://download.opensuse.org/ports/aarch64/source/tumbleweed/repo/oss/"
            )
            _require(
                item["official_source_repository"] == source_repository,
                "source repository/architecture mismatch",
            )
            _require(
                len(item["codec_rpms"]) == 5
                and {p["name"] for p in item["codec_rpms"]} == CODECS,
                "codec/HDR split dependency set mismatch",
            )
            expected_builders = BUILDERS if target == "amd64" else BUILDERS - {"go1.26"}
            _require(
                len(item["builder_rpms"]) == len(expected_builders)
                and {p["name"] for p in item["builder_rpms"]} == expected_builders,
                "builder dependency set mismatch",
            )
            if target == "arm64":
                validate_arm_go_descriptor(item.get("go_toolchain"))
            for group, prefix in [
                ("codec_rpms", PACKMAN + rpm_arch + "/"),
                ("builder_rpms", repository),
            ]:
                for package in item[group]:
                    _require(
                        package["arch"]
                        in (
                            [rpm_arch]
                            if group == "codec_rpms"
                            else [rpm_arch, "noarch"]
                        ),
                        "RPM architecture mismatch",
                    )
                    _require(
                        bool(re.fullmatch(r"[A-Za-z0-9+_.-]+", package["name"]))
                        and bool(
                            re.fullmatch(r"[A-Za-z0-9+_.~-]+", package["version"])
                        ),
                        "unsafe RPM identity",
                    )
                    _require(
                        package["url"].startswith(prefix)
                        and package["url"].endswith(
                            f"/{package['name']}-{package['version']}.{package['arch']}.rpm"
                        ),
                        "RPM URL/identity mismatch",
                    )
                    if group == "codec_rpms":
                        _require(
                            bool(re.fullmatch(r"[0-9a-f]{64}", package["sha256"])),
                            "RPM checksum identity missing",
                        )
            sources = item["source_rpms"]
            _require(
                len(sources) == 2
                and {p["sourcerpm"] for p in item["codec_rpms"]}
                == {p["filename"] for p in sources},
                "codec corresponding source RPM mismatch",
            )
            for source in sources:
                _require(
                    source["url"] == PACKMAN + "src/" + source["filename"]
                    and bool(
                        re.fullmatch(r"[A-Za-z0-9+_.-]+\.src\.rpm", source["filename"])
                    ),
                    "source RPM identity mismatch",
                )
                _require(
                    bool(re.fullmatch(r"[0-9a-f]{64}", source["sha256"])),
                    "source RPM checksum missing",
                )
        _require(
            next(
                p
                for p in recipe["architectures"]["amd64"]["builder_rpms"]
                if p["name"] == "go1.26"
            )["version"]
            == "1.26.10-1.1",
            "pinned Go compiler version unavailable/mismatch for selected architecture",
        )
        for asset in [
            recipe["ffmpeg"]["key"],
            recipe["packman_key"],
            recipe["configure"],
        ]:
            _require(
                sha256(path.parent / asset["file"]) == asset["sha256"],
                "config/key asset checksum mismatch",
            )
        return recipe
    except (OSError, KeyError, TypeError, ValueError, StopIteration) as exc:
        raise BuildError(f"invalid native recipe/config asset: {exc}") from exc


def build_environment(work: Path, arch: str) -> dict[str, str]:
    _require(arch in ARCHES, "unsupported architecture")
    return {
        "PATH": "/usr/lib64/go/1.26/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(work / "home"),
        "LANG": "C",
        "LC_ALL": "C",
        "CC": "/usr/bin/gcc",
        "CXX": "/usr/bin/g++",
        "AR": "/usr/bin/ar",
        "CFLAGS": "-O2",
        "CXXFLAGS": "-O2",
        "CPPFLAGS": "",
        "LDFLAGS": "",
        "MAKEFLAGS": "",
        "GOTOOLCHAIN": "local",
        "GOPROXY": "https://proxy.golang.org",
        "GOSUMDB": "sum.golang.org",
        "GOPATH": str(work / "gopath"),
        "GOMODCACHE": str(work / "modcache"),
        "GOCACHE": str(work / "gocache"),
        "GOFLAGS": "-p=4",
        "GOMAXPROCS": "4",
        "CGO_ENABLED": "0",
        "GOOS": "linux",
        "GOARCH": arch,
    }


def run(command: list[str], *, cwd: Path, env: dict[str, str]) -> str:
    print("+ " + shlex.join(command), flush=True)
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        def drain_errors():
            with process.stderr as errors:
                for line in errors:
                    print(line, end="", file=sys.stderr, flush=True)

        errors = threading.Thread(target=drain_errors)
        errors.start()
        lines = []
        for line in process.stdout:
            print(line, end="", flush=True)
            lines.append(line)
        process.stdout.close()
        code = process.wait()
        errors.join()
        _require(code == 0, f"native command failed ({code}): {shlex.join(command)}")
        return "".join(lines)
    except OSError as exc:
        raise BuildError(f"native command unavailable: {command[0]}") from exc


def fetch_input(item: dict, destination: Path, cache: Path | None) -> None:
    cached = cache / item["sha256"] if cache else None
    if cached and cached.exists():
        _require(sha256(cached) == item["sha256"], "cached input checksum mismatch")
        shutil.copyfile(cached, destination)
    else:
        _require(item["url"].startswith("https://"), "source URL must use HTTPS")
        with (
            urlopen(item["url"], timeout=120) as response,
            destination.open("wb") as output,
        ):
            _require(
                response.url.startswith("https://"), "source redirect must use HTTPS"
            )
            if item["url"].startswith("https://go.dev/dl/"):
                _require(
                    response.url
                    in [
                        item["url"],
                        "https://dl.google.com/go/" + item["url"].rsplit("/", 1)[1],
                    ],
                    "Go download redirected outside official publisher",
                )
            shutil.copyfileobj(response, output)
    _require(sha256(destination) == item["sha256"], "source/RPM checksum mismatch")
    if "size" in item:
        _require(
            destination.stat().st_size == item["size"], "pinned input size mismatch"
        )


def validate_arm_go_descriptor(descriptor: dict | None) -> None:
    _require(descriptor == ARM_GO_TOOLCHAIN, "pinned ARM Go metadata/identity mismatch")


def validate_arm_go_tree(sdk: Path) -> dict:
    try:
        version = (sdk / "VERSION").read_text().splitlines()
        _require(bool(version) and version[0] == "go1.26.10", "Go SDK VERSION mismatch")
        with (sdk / "bin/go").open("rb") as stream:
            header = stream.read(64)
        _require(
            len(header) == 64
            and header[:7] == b"\x7fELF\x02\x01\x01"
            and int.from_bytes(header[18:20], "little") == 183,
            "Go SDK ELF architecture mismatch",
        )
        return {
            "version": version[0],
            "elf_machine": 183,
            "version_file_sha256": sha256(sdk / "VERSION"),
            "compiler_sha256": sha256(sdk / "bin/go"),
        }
    except OSError as exc:
        raise BuildError("Go SDK VERSION/ELF input missing") from exc


def prepare_arm_go(
    descriptor: dict, work: Path, sources: Path, cache: Path | None
) -> tuple[str, dict]:
    validate_arm_go_descriptor(descriptor)
    _require(
        platform.machine() == "aarch64",
        "ARM Go SDK requires matching native host architecture",
    )
    archive, sdk = work / "go-sdk.tar.gz", work / "go-sdk"
    fetch_input(descriptor, archive, cache)
    safe_extract(archive, sdk)
    proof = validate_arm_go_tree(sdk)
    # Keep matching source, not the SDK archive or extracted compiler binaries.
    source_archive = sources / "go1.26.10.src.tar.gz"
    fetch_input(descriptor["source"], source_archive, cache)
    proof.update(
        {
            "kind": "official-go-sdk",
            "metadata": descriptor,
            "archive_sha256": sha256(archive),
            "source_archive_sha256": sha256(source_archive),
        }
    )
    return str(sdk / "bin/go"), proof


def validate_signature_status(text: str, fingerprint: str) -> None:
    signatures = re.findall(r"^\[GNUPG:\] VALIDSIG ([0-9A-F]+) ", text, re.MULTILINE)
    _require(
        signatures == [fingerprint]
        and not re.search(r"\b(BADSIG|ERRSIG|EXPSIG|EXPKEYSIG|REVKEYSIG)\b", text),
        "release signature/fingerprint verification failed",
    )


def safe_extract(archive: Path, source: Path) -> None:
    staging = source.with_name(source.name + "-extract")
    with tarfile.open(archive) as bundle:
        members = bundle.getmembers()
        roots = set()
        for member in members:
            path = PurePosixPath(member.name)
            _require(
                not path.is_absolute()
                and bool(path.parts)
                and ".." not in path.parts
                and (member.isdir() or member.isreg() or member.issym()),
                "unsafe source archive member",
            )
            if member.issym():
                link = PurePosixPath(member.linkname)
                _require(
                    not link.is_absolute() and ".." not in link.parts,
                    "unsafe archive symlink",
                )
            roots.add(path.parts[0])
        _require(len(roots) == 1, "unsafe archive root")
        staging.mkdir()
        bundle.extractall(staging, filter="data")
    (staging / roots.pop()).rename(source)
    staging.rmdir()


def validate_patch_plan(records: list[dict], assets: Path) -> None:
    _require(
        records == REVIEWED_PATCHES, "unreviewed patch identity, checksum or order"
    )
    _require(
        isinstance(records, list) and len(records) == 1,
        "required reviewed RTSP patch input missing",
    )
    record = records[0]
    commit = "f5d4fd186c68913240c4f6e9cda98443e1518b34"
    _require(
        record.get("order") == 1
        and record.get("id") == "CVE-2026-107696"
        and record.get("upstream_commit") == commit
        and record.get("upstream_url")
        == "https://github.com/FFmpeg/FFmpeg/commit/" + commit,
        "reviewed patch identity/order mismatch",
    )
    _require(
        record.get("file") == "patches/CVE-2026-107696.patch", "unreviewed patch path"
    )
    path = assets / record["file"]
    _require(
        path.is_file()
        and not path.is_symlink()
        and sha256(path) == record.get("sha256"),
        "reviewed patch file/checksum mismatch",
    )
    witnesses = record.get("witnesses", [])
    _require(bool(witnesses), "patch source witnesses missing")
    paths = set()
    for witness in witnesses:
        name = PurePosixPath(witness["path"])
        _require(
            not name.is_absolute() and bool(name.parts) and ".." not in name.parts,
            "unsafe patch witness path",
        )
        _require(
            all(
                re.fullmatch(r"[0-9a-f]{64}", witness.get(field, ""))
                for field in ["before_sha256", "after_sha256"]
            )
            and witness["before_sha256"] != witness["after_sha256"],
            "patch source witness hashes invalid",
        )
        paths.add(str(name))
    touched = []
    for line in path.read_text().splitlines():
        if line.startswith(("--- ", "+++ ")):
            name = line[4:].split("\t", 1)[0]
            _require(
                name.startswith(("a/", "b/")),
                "patch must use explicit source-relative paths",
            )
            touched.append(name[2:])
    _require(
        bool(touched) and set(touched) == paths and len(touched) == len(paths) * 2,
        "patch touches unbound source/witness paths",
    )


def apply_patches(
    records: list[dict], assets: Path, source: Path, env: dict[str, str]
) -> list[dict]:
    validate_patch_plan(records, assets)
    proof = []
    for record in records:
        for witness in record["witnesses"]:
            path = source / witness["path"]
            _require(
                path.is_file()
                and not path.is_symlink()
                and path.resolve().is_relative_to(source.resolve())
                and sha256(path) == witness["before_sha256"],
                "patch before-source witness mismatch",
            )
        status = run(
            [
                "/usr/bin/patch",
                "--batch",
                "--fuzz=0",
                "-p1",
                "--input",
                str((assets / record["file"]).absolute()),
            ],
            cwd=source,
            env=env,
        )
        actual = []
        for witness in record["witnesses"]:
            path = source / witness["path"]
            digest = sha256(path)
            _require(
                digest == witness["after_sha256"], "patch after-source witness mismatch"
            )
            actual.append({**witness, "actual_after_sha256": digest})
        proof.append({**record, "witnesses": actual, "apply_output": status})
    return proof


def validate_rpm_header(text: str, expected: dict) -> dict:
    fields = text.strip().split("\t")
    _require(len(fields) == 5, "RPM header identity malformed")
    item = dict(zip(["name", "version", "arch", "sourcerpm", "sourcepackage"], fields))
    _require(
        all(item[k] == expected[k] for k in ["name", "version", "arch", "sourcerpm"])
        and item["sourcepackage"] == "(none)",
        "RPM identity/architecture mismatch",
    )
    return item


def _inventory(text: str) -> list[dict]:
    keys = ["name", "epoch", "version", "release", "arch", "sourcerpm", "license"]
    result = []
    for line in text.splitlines():
        fields = line.split("\t")
        _require(len(fields) == len(keys), "installed RPM inventory malformed")
        result.append(dict(zip(keys, fields)))
    return sorted(
        result, key=lambda p: (p["name"], p["arch"], p["version"], p["release"])
    )


def _rpm_spec(item: dict) -> str:
    version = item["version"] + ("-" + item["release"] if "release" in item else "")
    epoch = item.get("epoch", "0")
    return (
        f"{item['name']}.{item['arch']}={'' if epoch == '0' else epoch + ':'}{version}"
    )


def parse_ldd(text: str) -> list[str]:
    _require("not found" not in text, "unresolved runtime shared dependency")
    return [
        match.group(1)
        for line in text.splitlines()
        if (match := re.search(r"(?:=>\s+)?(/[^\s]+)", line))
    ]


def verify_elf(path: Path, arch: str, env: dict[str, str]) -> str:
    with path.open("rb") as stream:
        _require(stream.read(4) == b"\x7fELF", "owned output is not an ELF artifact")
    header = run(["/usr/bin/readelf", "-h", str(path)], cwd=path.parent, env=env)
    _require(
        "ELF64" in header and ARCHES[arch][1] in header and "little endian" in header,
        "native ELF architecture mismatch",
    )
    return header


def verify_program(path: Path, name: str, arch: str, env: dict[str, str]) -> dict:
    header = verify_elf(path, arch, env)
    text = run(
        [str(path), "--version" if name == "gosu" else "-version"],
        cwd=path.parent,
        env=env,
    )
    pattern = (
        r"^1\.19(?:\s|$)" if name == "gosu" else rf"^{name} version 8\.1\.3(?:\s|$)"
    )
    version_lines = [
        line
        for line in text.splitlines()
        if (
            bool(re.match(r"^\d+\.\d+(?:\s|$)", line))
            if name == "gosu"
            else line.startswith(name + " version ")
        )
    ]
    _require(
        len(version_lines) == 1 and bool(re.match(pattern, version_lines[0])),
        "native program version mismatch",
    )
    return {
        "version_output": text,
        "readelf": header,
        "architecture": arch,
        "sha256": sha256(path),
    }


def validate_configuration(text: str, expected: list[str]) -> None:
    # Accept only the actual tool header, blank lines and optional normal footer.
    # Do not filter away unknown arguments, unrecognized text or duplicate data.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    _require(
        bool(lines) and lines[0] == "configuration:",
        "FFmpeg configuration header mismatch",
    )
    flags = []
    for index, line in enumerate(lines[1:], 1):
        if line == "Exiting with exit code 0":
            _require(
                index == len(lines) - 1, "FFmpeg configuration footer is not terminal"
            )
            continue
        _require(line.startswith("--"), "unrecognized FFmpeg configuration data")
        try:
            flags.extend(shlex.split(line))
        except ValueError as exc:
            raise BuildError("invalid FFmpeg configuration quoting") from exc
    _require(flags == expected, "produced FFmpeg configuration mismatch")


def validate_go_version(text: str, arch: str) -> None:
    _require(
        text.strip() == f"go version go1.26.10 linux/{arch}",
        "Go compiler version/architecture mismatch",
    )


def owned_artifacts(root: Path) -> list[dict]:
    result = []
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            target = os.readlink(path)
            _require(
                not Path(target).is_absolute()
                and path.resolve().is_relative_to(root.resolve())
                and path.resolve().is_file(),
                "owned artifact symlink escapes or is unresolved",
            )
            result.append(
                {
                    "path": relative,
                    "type": "symlink",
                    "target": target,
                    "target_sha256": sha256(path.resolve()),
                }
            )
        elif path.is_file():
            result.append(
                {
                    "path": relative,
                    "type": "file",
                    "sha256": sha256(path),
                    "size": path.stat().st_size,
                    "mode": path.stat().st_mode & 0o777,
                }
            )
    return result


def verify_artifact_map(root: Path, expected: list[dict]) -> None:
    actual = owned_artifacts(root)
    _require(actual == expected, "owned artifact map mismatch")
    _require(
        {f"usr/local/bin/{name}" for name in ["ffmpeg", "ffprobe", "gosu"]}.issubset(
            {r["path"] for r in actual}
        ),
        "required owned artifacts missing",
    )


def publish_record(output: Path, record: dict) -> None:
    """The only PASS publisher checks the final staged bytes before writing."""
    verify_artifact_map(output / "root", record["owned_artifacts"])
    temporary = output / "build-output.json.tmp"
    temporary.write_text(json.dumps({**record, "status": "PASS"}, indent=2) + "\n")
    temporary.replace(output / "build-output.json")


def _install_builder(recipe: dict, work: Path, env: dict[str, str]) -> None:
    release = Path("/etc/os-release").read_text()
    _require(
        'ID="opensuse-tumbleweed"' in release or "ID=opensuse-tumbleweed\n" in release,
        "run only inside the reviewed Tumbleweed builder stage",
    )
    for path in Path("/etc/zypp/repos.d").glob("*.repo"):
        path.unlink()
    run(
        [
            "/usr/bin/zypper",
            "--non-interactive",
            "addrepo",
            "--check",
            "--gpgcheck",
            "--refresh",
            recipe["official_repository"],
            "oss",
        ],
        cwd=work,
        env=env,
    )
    run(["/usr/bin/zypper", "--non-interactive", "refresh"], cwd=work, env=env)
    run(
        ["/usr/bin/zypper", "--non-interactive", "update", "--no-recommends"],
        cwd=work,
        env=env,
    )
    run(
        [
            "/usr/bin/zypper",
            "--non-interactive",
            "install",
            "--no-recommends",
            *[_rpm_spec(p) for p in recipe["builder_rpms"]],
        ],
        cwd=work,
        env=env,
    )
    installed = _inventory(
        run(["/usr/bin/rpm", "-qa", "--qf", RPM_FORMAT], cwd=work, env=env)
    )
    for expected in recipe["builder_rpms"]:
        _require(
            any(
                p["name"] == expected["name"]
                and p["arch"] == expected["arch"]
                and p["epoch"] == expected["epoch"]
                and p["version"] + "-" + p["release"] == expected["version"]
                and p["sourcerpm"] == expected["sourcerpm"]
                for p in installed
            ),
            "installed builder RPM identity mismatch",
        )


def _verify_key(key: Path, fingerprint: str, work: Path, env: dict[str, str]) -> None:
    text = run(
        ["/usr/bin/gpg", "--batch", "--show-keys", "--with-colons", str(key)],
        cwd=work,
        env=env,
    )
    fingerprints = re.findall(r"^fpr:[^\n]*", text, re.MULTILINE)
    _require(
        bool(fingerprints) and fingerprints[0].split(":")[9] == fingerprint,
        "signing key fingerprint mismatch",
    )


def _codec_inputs(
    recipe: dict,
    assets: Path,
    work: Path,
    sources: Path,
    env: dict[str, str],
    cache: Path | None,
) -> list[Path]:
    key = assets / "packman.asc"
    _verify_key(key, PACKMAN_FPR, work, env)
    trust = work / "rpm-trust"
    trust.mkdir()
    run(["/usr/bin/rpm", "--dbpath", str(trust), "--initdb"], cwd=work, env=env)
    run(
        ["/usr/bin/rpm", "--dbpath", str(trust), "--import", str(key)],
        cwd=work,
        env=env,
    )
    binaries = []
    for item in [*recipe["codec_rpms"], *recipe["source_rpms"]]:
        source = "filename" in item
        destination = (sources if source else work) / item["url"].rsplit("/", 1)[1]
        fetch_input(item, destination, cache)
        status = run(
            [
                "/usr/bin/rpm",
                "--dbpath",
                str(trust),
                "--checksig",
                "--verbose",
                str(destination),
            ],
            cwd=work,
            env=env,
        )
        _require(
            "signatures OK" in status or ("Signature" in status and ": OK" in status),
            "codec RPM signature not verified",
        )
        _require(
            not any(word in status for word in ["NOKEY", "NOT OK", "BAD"]),
            "codec RPM signature failed",
        )
        query = (
            "%{NAME}\t%{VERSION}-%{RELEASE}\t%{ARCH}\t%{SOURCERPM}\t%{SOURCEPACKAGE}\n"
        )
        header = run(
            [
                "/usr/bin/rpm",
                "--dbpath",
                str(trust),
                "-qp",
                "--qf",
                query,
                str(destination),
            ],
            cwd=work,
            env=env,
        )
        if source:
            fields = header.strip().split("\t")
            _require(
                len(fields) == 5
                and fields[4] == "1"
                and f"{fields[0]}-{fields[1]}.src.rpm" == item["filename"],
                "source RPM header identity mismatch",
            )
        else:
            validate_rpm_header(header, item)
            binaries.append(destination)
    run(["/usr/bin/rpm", "--import", str(key)], cwd=work, env=env)
    run(
        [
            "/usr/bin/zypper",
            "--non-interactive",
            "install",
            "--no-recommends",
            *map(str, binaries),
        ],
        cwd=work,
        env=env,
    )
    return binaries


def _gosu(
    recipe: dict,
    work: Path,
    sources: Path,
    root: Path,
    env: dict[str, str],
    cache: Path | None,
    arch: str,
    toolchain: dict | None = None,
) -> dict:
    go = "/usr/lib64/go/1.26/bin/go"
    origin = None
    if arch == "arm64":
        go, origin = prepare_arm_go(toolchain, work, sources, cache)
        env = {**env, "GOROOT": str(Path(go).parent.parent)}
    version = run([go, "version"], cwd=work, env=env)
    validate_go_version(version, arch)
    archive = sources / "gosu.tar.gz"
    fetch_input(recipe, archive, cache)
    source = work / "gosu"
    safe_extract(archive, source)
    run(
        [go, "get", *[name + "@" + value for name, value in MODULES.items()]],
        cwd=source,
        env=env,
    )
    module_text = run([go, "list", "-m", "-json", "all"], cwd=source, env=env)
    modules = []
    decoder = json.JSONDecoder()
    while module_text.strip():
        module, offset = decoder.raw_decode(module_text.lstrip())
        modules.append(module)
        module_text = module_text.lstrip()[offset:]
    _require(
        {m["Path"]: m["Version"] for m in modules if not m.get("Main")} == MODULES,
        "resolved Go module identity mismatch",
    )
    graph = run([go, "mod", "graph"], cwd=source, env=env)
    run([go, "mod", "vendor"], cwd=source, env=env)
    binary = root / "usr/local/bin/gosu"
    binary.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            go,
            "build",
            "-mod=vendor",
            "-trimpath",
            "-ldflags=-s -w",
            "-o",
            str(binary),
            ".",
        ],
        cwd=source,
        env=env,
    )
    proof = verify_program(binary, "gosu", arch, env)
    dynamic = run(["/usr/bin/readelf", "-d", str(binary)], cwd=work, env=env)
    _require(
        "NEEDED" not in dynamic, "gosu must be the CGO-disabled owned static product"
    )
    proof.update(
        {
            "compiler_version": version,
            "modules": modules,
            "module_graph": graph,
            "go_version_m": run(
                [go, "version", "-m", str(binary)], cwd=source, env=env
            ),
            "dynamic_section": dynamic,
        }
    )
    if origin is not None:
        proof["toolchain_origin"] = origin
    retained = sources / "gosu"
    shutil.copytree(source, retained, symlinks=True)
    notices = root / "usr/local/share/licenses/gosu"
    notices.mkdir(parents=True)
    shutil.copy2(source / "LICENSE", notices / "LICENSE")
    for path in (source / "vendor").rglob("*"):
        if path.is_file() and path.name.startswith(
            ("LICENSE", "COPYING", "NOTICE", "PATENTS")
        ):
            target = notices / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    goroot = Path(run([go, "env", "GOROOT"], cwd=source, env=env).strip())
    package_notices = (
        run(
            ["/usr/bin/rpm", "-q", "--licensefiles", "go1.26"], cwd=work, env=env
        ).splitlines()
        if arch == "amd64"
        else []
    )
    go_notices = {
        p
        for p in [goroot / "LICENSE", goroot / "PATENTS", *map(Path, package_notices)]
        if p.is_file()
    }
    _require(
        any(p.name.startswith(("LICENSE", "COPYING")) for p in go_notices),
        "Go BSD/stdlib notices missing",
    )
    proof["stdlib_notices"] = []
    for index, path in enumerate(sorted(go_notices)):
        name = f"Go-{index}-{path.name}"
        shutil.copy2(path, notices / name)
        shutil.copy2(path, sources / name)
        proof["stdlib_notices"].append(
            {"original_path": str(path), "sha256": sha256(path), "retained_file": name}
        )
    return proof


def _ffmpeg(
    recipe: dict,
    assets: Path,
    work: Path,
    sources: Path,
    root: Path,
    env: dict[str, str],
    cache: Path | None,
    arch: str,
) -> dict:
    archive, signature = (
        sources / "ffmpeg-8.1.3.tar.xz",
        sources / "ffmpeg-8.1.3.tar.xz.asc",
    )
    fetch_input(recipe["ffmpeg"], archive, cache)
    fetch_input(recipe["ffmpeg"]["signature"], signature, cache)
    key = assets / "ffmpeg-release.asc"
    _verify_key(key, FFMPEG_FPR, work, env)
    keyring = work / "ffmpeg-keyring"
    keyring.mkdir(mode=0o700)
    run(
        ["/usr/bin/gpg", "--batch", "--homedir", str(keyring), "--import", str(key)],
        cwd=work,
        env=env,
    )
    status = run(
        [
            "/usr/bin/gpg",
            "--batch",
            "--homedir",
            str(keyring),
            "--status-fd=1",
            "--verify",
            str(signature),
            str(archive),
        ],
        cwd=work,
        env=env,
    )
    validate_signature_status(status, FFMPEG_FPR)
    source = work / "ffmpeg"
    safe_extract(archive, source)
    patch_proof = apply_patches(recipe["ffmpeg_patches"], assets, source, env)
    source_files = [
        {"path": str(path.relative_to(source)), "sha256": sha256(path)}
        for path in sorted(source.rglob("*"))
        if path.is_file() and not path.is_symlink()
    ]
    tree_index = sources / "ffmpeg-patched-source-files.json"
    tree_index.write_text(json.dumps(source_files, indent=2) + "\n")
    arguments = (assets / "ffmpeg.args").read_text().splitlines()
    run(["./configure", *arguments], cwd=source, env=env)
    run(["/usr/bin/make", "-j4"], cwd=source, env=env)
    stage = work / "ffmpeg-install"
    run(["/usr/bin/make", "install", "DESTDIR=" + str(stage)], cwd=source, env=env)
    libdir = root / "usr/local/lib64"
    libdir.mkdir(parents=True)
    for family in LIBRARIES:
        matches = list((stage / "usr/local/lib64").glob(family + ".so*"))
        _require(bool(matches), f"owned FFmpeg library missing: {family}")
        for path in matches:
            shutil.copy2(path, libdir / path.name, follow_symlinks=False)
    for name in ["ffmpeg", "ffprobe"]:
        shutil.copy2(stage / "usr/local/bin" / name, root / "usr/local/bin" / name)
    runtime_env = {**env, "LD_LIBRARY_PATH": str(libdir)}
    programs = {
        name: verify_program(root / "usr/local/bin" / name, name, arch, runtime_env)
        for name in ["ffmpeg", "ffprobe"]
    }
    configuration = run(
        [str(root / "usr/local/bin/ffmpeg"), "-hide_banner", "-buildconf"],
        cwd=work,
        env=runtime_env,
    )
    validate_configuration(configuration, arguments)
    libraries = []
    for path in sorted(libdir.glob("*.so.*")):
        if path.is_symlink():
            continue
        header = verify_elf(path, arch, runtime_env)
        symbol = LIBRARIES[path.name.split(".so", 1)[0]]
        symbols = run(["/usr/bin/nm", "-D", str(path)], cwd=work, env=runtime_env)
        _require(
            bool(re.search(r"\b[TW] " + symbol + r"(?:@\S+)?$", symbols, re.MULTILINE)),
            "owned FFmpeg version symbol missing",
        )
        libraries.append(
            {
                "path": str(path.relative_to(root)),
                "sha256": sha256(path),
                "readelf": header,
                "version_symbol": symbol,
            }
        )
    notices = root / "usr/local/share/licenses/ffmpeg"
    notices.mkdir(parents=True)
    for path in [*source.glob("COPYING.*"), source / "LICENSE.md"]:
        shutil.copy2(path, notices / path.name)
    shutil.copy2(source / "ffbuild/config.log", sources / "ffmpeg-config.log")
    return {
        "programs": programs,
        "libraries": libraries,
        "configuration": configuration,
        "configure_args": arguments,
        "signature_status": status,
        "patches": patch_proof,
        "patched_source_files_sha256": sha256(tree_index),
    }


def _providers(
    root: Path, work: Path, env: dict[str, str]
) -> tuple[list[dict], list[dict]]:
    env = {**env, "LD_LIBRARY_PATH": str(root / "usr/local/lib64")}
    paths = [
        root / "usr/local/bin/ffmpeg",
        root / "usr/local/bin/ffprobe",
        *[p for p in (root / "usr/local/lib64").glob("*.so.*") if not p.is_symlink()],
    ]
    providers, edges = {}, []
    for binary in paths:
        text = run(["/usr/bin/ldd", str(binary)], cwd=work, env=env)
        for name in parse_ldd(text):
            path = Path(name).resolve()
            owned = path.is_relative_to(root.resolve())
            if not owned:
                rows = _inventory(
                    run(
                        ["/usr/bin/rpm", "-qf", "--qf", RPM_FORMAT, str(path)],
                        cwd=work,
                        env=env,
                    )
                )
                _require(
                    len(rows) == 1, "runtime shared dependency RPM provider ambiguous"
                )
                provider = rows[0]
                _require(
                    provider["name"] not in ["libcjson1", "libsndfile1"],
                    "unrequested cJSON/libsndfile runtime dependency",
                )
                providers[_rpm_spec(provider)] = provider
            edges.append(
                {
                    "elf": str(binary.relative_to(root)),
                    "library": str(path.relative_to(root)) if owned else str(path),
                    "owned": owned,
                    "rpm_provider": None if owned else _rpm_spec(provider),
                }
            )
    _require(bool(providers), "runtime dependency provider inventory is empty")
    return [providers[k] for k in sorted(providers)], edges


def build(
    manifest: Path,
    work_dir: Path,
    output_dir: Path,
    arch: str,
    cache_dir: Path | None = None,
) -> dict:
    recipe = load_recipe(manifest, arch)
    validate_patch_plan(recipe.get("ffmpeg_patches", []), manifest.parent)
    _require(
        platform.machine() == ARCHES[arch][0],
        "builder host architecture differs from manifest target; use native CI runners",
    )
    work, output = work_dir.absolute(), output_dir.absolute()
    _require(
        not work.exists()
        and not output.exists()
        and work != output
        and not work.is_relative_to(output)
        and not output.is_relative_to(work),
        "build locations must be new, distinct and non-nested",
    )
    work.mkdir(parents=True)
    output.mkdir(parents=True)
    root, sources = output / "root", output / "sources"
    root.mkdir()
    sources.mkdir()
    env = build_environment(work, arch)
    (work / "home").mkdir(mode=0o700)
    selected = recipe["architectures"][arch]
    _install_builder(selected, work, env)
    _codec_inputs(selected, manifest.parent, work, sources, env, cache_dir)
    gosu = _gosu(
        recipe["gosu"],
        work,
        sources,
        root,
        env,
        cache_dir,
        arch,
        selected.get("go_toolchain"),
    )
    ffmpeg = _ffmpeg(recipe, manifest.parent, work, sources, root, env, cache_dir, arch)
    providers, edges = _providers(root, work, env)
    builder_rpms = _inventory(
        run(["/usr/bin/rpm", "-qa", "--qf", RPM_FORMAT], cwd=work, env=env)
    )
    (output / "runtime-packages.txt").write_text(
        "\n".join(_rpm_spec(p) for p in providers) + "\n"
    )
    codecs = output / "runtime-rpms"
    codecs.mkdir()
    runtime_names = {p["name"] for p in providers}
    for item in selected["codec_rpms"]:
        if item["name"] in runtime_names:
            shutil.copy2(
                work / item["url"].rsplit("/", 1)[1],
                codecs / item["url"].rsplit("/", 1)[1],
            )
    loader = root / "etc/ld.so.conf.d/ecm-native.conf"
    loader.parent.mkdir(parents=True)
    loader.write_text("/usr/local/lib64\n")
    recipe_copy = sources / "recipe"
    shutil.copytree(manifest.parent, recipe_copy)
    shutil.copy2(Path(__file__), recipe_copy / "build_native_runtime.py")
    source_files = [
        {
            "path": str(p.relative_to(output)),
            "sha256": sha256(p),
            "size": p.stat().st_size,
        }
        for p in sorted(sources.rglob("*"))
        if p.is_file() and not p.is_symlink()
    ]
    (output / "source-files.json").write_text(json.dumps(source_files, indent=2) + "\n")
    record = {
        "schema_version": 1,
        "architecture": arch,
        "rpm_architecture": selected["rpm_arch"],
        "manifest_sha256": sha256(manifest),
        "helper_sha256": sha256(Path(__file__)),
        "base_image": recipe["base_image"],
        "source_identity": {"ffmpeg": recipe["ffmpeg"], "gosu": recipe["gosu"]},
        "codec_inputs": selected["codec_rpms"],
        "codec_source_rpms": selected["source_rpms"],
        "official_source_repository": selected["official_source_repository"],
        "ffmpeg": ffmpeg,
        "gosu": gosu,
        "builder_rpms": builder_rpms,
        "runtime_providers": providers,
        "elf_dependency_graph": edges,
        "owned_artifacts": owned_artifacts(root),
        "source_files_sha256": sha256(output / "source-files.json"),
        "source_closure": "NOT CLAIMED: separate corresponding-source collector and policy gates required",
    }
    publish_record(output, record)
    record["status"] = "PASS"
    print(
        f"Native build PASS: {arch}; {len(record['owned_artifacts'])} bound products; {len(providers)} exact RPM providers",
        flush=True,
    )
    return record


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--arch", required=True, choices=sorted(ARCHES))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        build(args.manifest, args.work_dir, args.output_dir, args.arch, args.cache_dir)
    except (BuildError, OSError, tarfile.TarError) as exc:
        raise SystemExit(f"native build failed: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
