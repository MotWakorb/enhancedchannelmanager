#!/usr/bin/env python3
"""Package verified native evidence into a build-only signed APK.

No host installation, repository addition, permanent trust change or security
clearance. The matching source and component receipts travel inside the package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import stat
import tempfile
from pathlib import Path, PurePosixPath

from alpine_repository_policy import validate_repositories
from build_alpine_codecs import RECIPE_SHA256 as CODECS_SHA
from build_alpine_codecs import create_locations, validate_platform
from build_alpine_ffmpeg import RECIPE_SHA256 as FFMPEG_SHA
from build_alpine_gosu import RECIPE_SHA256 as GOSU_SHA
from build_alpine_jxl import RECIPE_SHA256 as JXL_SHA
from build_alpine_security_libraries import RECIPE_SHA256 as SECURITY_SHA
from build_native_runtime import (
    ARCHES,
    BuildError,
    _require,
    owned_artifacts,
    run,
    sha256,
    verify_elf,
)

COMPONENTS = {
    "codecs": (CODECS_SHA, "BUILT_API_SMOKE_ONLY"),
    "security": (SECURITY_SHA, "BUILT_SECURITY_API_ONLY"),
    "jxl": (JXL_SHA, "BUILT_JXL_API_ONLY"),
    "ffmpeg": (FFMPEG_SHA, "BUILT_FFMPEG_ONLY"),
    "gosu": (GOSU_SHA, "BUILT_GOSU_ONLY"),
}
PACKAGE_NAME = "ecm-owned-native"
SONAME = re.compile(r"[A-Za-z0-9_+.-]+\.so(?:\.[A-Za-z0-9_+.-]+)*")


def checked_path(value: str) -> Path:
    _require(
        isinstance(value, str)
        and value
        and not any(c.isspace() for c in value)
        and not PurePosixPath(value).is_absolute()
        and ".." not in PurePosixPath(value).parts
        and str(PurePosixPath(value)) == value,
        "unsafe package path",
    )
    return Path(value)


def validate_runtime_path(value: str) -> None:
    path = checked_path(value)
    _require(
        any(
            path.is_relative_to(prefix)
            for prefix in [
                "usr/local/bin",
                "usr/local/lib",
                "usr/local/lib64",
                "usr/lib",
                "usr/share/doc",
                "usr/local/share/licenses",
            ]
        ),
        "unapproved owned runtime namespace",
    )


def copy_inventory(source: Path, inventory: list[dict], destination: Path) -> None:
    _require(
        source.is_dir()
        and not source.is_symlink()
        and owned_artifacts(source) == inventory,
        "source inventory changed before packaging",
    )
    for path in source.rglob("*"):
        mode = path.lstat().st_mode
        _require(not mode & 0o7000, "privileged mode forbidden in owned package")
        _require(
            stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode),
            "special package entry forbidden",
        )
    # Reject all collisions before copying; never overwrite evidence or an input.
    for row in inventory:
        target = destination / checked_path(row["path"])
        _require(
            not target.exists() and not target.is_symlink(),
            "owned package path collision",
        )
    for row in inventory:
        relative = checked_path(row["path"])
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target, follow_symlinks=False)
    for row in inventory:
        path = destination / row["path"]
        if row["type"] == "file":
            _require(
                not path.is_symlink() and sha256(path) == row["sha256"],
                "staged package inventory mismatch",
            )
        else:
            _require(
                path.is_symlink()
                and str(path.readlink()) == row["target"]
                and path.resolve().is_relative_to(destination.resolve())
                and sha256(path) == row["target_sha256"],
                "staged package symlink inventory mismatch",
            )


def validate_inputs(directories: dict[str, Path], arch: str) -> dict[str, dict]:
    _require(set(directories) == set(COMPONENTS), "complete component set required")
    records = {}
    for name, directory in sorted(directories.items()):
        receipt = directory / "build-output.json"
        _require(
            receipt.is_file() and not receipt.is_symlink(),
            "regular component receipt required",
        )
        record = json.loads(receipt.read_text())
        expected_sha, status = COMPONENTS[name]
        _require(
            record["recipe_sha256"] == expected_sha
            and record["status"] == status
            and record["architecture"] == arch,
            "component recipe/status/architecture mismatch",
        )
        for subdir, key in [("root", "owned_artifacts"), ("sources", "source_files")]:
            _require(
                (directory / subdir).is_dir()
                and not (directory / subdir).is_symlink()
                and owned_artifacts(directory / subdir) == record[key],
                "component artifact/source inventory mismatch",
            )
        for row in record["owned_artifacts"]:
            validate_runtime_path(row["path"])
        records[name] = record
    # FFmpeg must bind the same three component builds we are packaging, not
    # different outputs that happen to use the same source recipe.
    for name in ["codecs", "security", "jxl"]:
        _require(
            records["ffmpeg"]["component_receipts"][name] == records[name],
            "FFmpeg composed component receipt mismatch",
        )
    return records


def dependency_metadata(libraries: list[dict]) -> tuple[list[str], list[str]]:
    provides, needed = set(), set()
    for library in libraries:
        soname = library.get("soname")
        if soname:
            _require(
                SONAME.fullmatch(soname) is not None, "invalid ELF provider SONAME"
            )
            provides.add(soname)
        for name in library["needed"]:
            _require(
                SONAME.fullmatch(name) is not None
                and not any(
                    bad in name
                    for bad in [
                        "libc.so.",
                        "ld-linux",
                        "libm.so.",
                        "libpthread.so.",
                        "libdl.so.",
                        "librt.so.",
                    ]
                ),
                "foreign or unsafe ELF provider dependency",
            )
            needed.add(name)
    return (
        [f"so:{name}=0" for name in sorted(provides)],
        [f"so:{name}" for name in sorted(needed - provides)],
    )


def validate_locations(directories: dict[str, Path], work: Path, output: Path) -> None:
    for source in directories.values():
        for destination in [work, output]:
            a, b = source.resolve(), destination.resolve()
            _require(
                not a.is_relative_to(b) and not b.is_relative_to(a),
                "packaging locations must be disjoint from component evidence",
            )


def package_version(components: dict, helper_sha: str) -> str:
    data = json.dumps(
        {"components": components, "packager_sha256": helper_sha}, sort_keys=True
    ).encode()
    return "1.0~" + hashlib.sha256(data).hexdigest() + "-r0"


def package(directories: dict[str, Path], arch: str, work: Path, output: Path) -> dict:
    release = Path("/etc/alpine-release").read_text()
    validate_platform(release, platform.machine(), arch)
    validate_repositories(release, Path("/etc/apk/repositories").read_text())
    validate_locations(directories, work, output)
    records = validate_inputs(directories, arch)
    create_locations(work, output)
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(work),
        "LC_ALL": "C",
        "TZ": "UTC",
    }
    stage = work / "payload"
    stage.mkdir()
    components = {}
    for name, directory in sorted(directories.items()):
        record = records[name]
        copy_inventory(directory / "root", record["owned_artifacts"], stage)
        copy_inventory(
            directory / "sources",
            record["source_files"],
            stage / "usr/share/ecm/sources/owned" / name,
        )
        target = stage / "usr/share/ecm/provenance/owned" / (name + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(directory / "build-output.json", target)
        components[name] = {
            "recipe_sha256": record["recipe_sha256"],
            "receipt_sha256": sha256(target),
        }
    # musl does not process glibc ldconfig caches. Keep the real existing FFmpeg
    # lib64 location without aliases, RPATH, or relying on operator PATH/env.
    loader_path = f"etc/ld-musl-{ARCHES[arch][0]}.path"
    (stage / "etc").mkdir()
    (stage / loader_path).write_text(
        "/usr/local/lib64\n/usr/local/lib\n/lib\n/usr/lib\n"
    )
    libraries = []
    for path in sorted(stage.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                continue
        # Original source archives are opaque, never loaded/extracted here.
        _require(
            not path.is_relative_to(stage / "usr/share/ecm/sources"),
            "unexpected executable source payload",
        )
        verify_elf(path, arch, env)
        dynamic = run(["/usr/bin/readelf", "-d", str(path)], cwd=work, env=env)
        _require(
            not re.search(r"\((?:RPATH|RUNPATH)\)", dynamic),
            "owned ELF runtime search override forbidden",
        )
        sonames = re.findall(r"\(SONAME\).*\[([^\]]+)\]", dynamic)
        _require(len(sonames) <= 1, "ambiguous owned ELF SONAME")
        libraries.append(
            {
                "path": str(path.relative_to(stage)),
                "soname": sonames[0] if sonames else None,
                "needed": re.findall(r"\(NEEDED\).*\[([^\]]+)\]", dynamic),
            }
        )
    provides, depends = dependency_metadata(libraries)
    # Versioned package-name provides conflict with stock providers; no replaces
    # or force-overwrite is allowed. The exact local package is installed first.
    provides += [
        item["name"] + "=" + item["package_version"]
        for item in records["security"]["libraries"]
    ]
    provides += ["libjxl=0.12.0-r0"]
    version = package_version(components, sha256(Path(__file__)))
    archive = output / f"{PACKAGE_NAME}-{version}.apk"
    helper = stage / "usr/share/ecm/sources/owned/package_alpine_native.py"
    shutil.copy2(Path(__file__), helper)
    inventory = owned_artifacts(stage)
    licenses = sorted(
        {
            "GPL-3.0-or-later",
            "BSD-3-Clause",
            "MIT",
            "Apache-2.0",
            *[
                item["license"]
                for name in ["codecs", "security"]
                for item in records[name]["libraries"]
            ],
        }
    )
    with tempfile.TemporaryDirectory(prefix="build-only-key-", dir=work) as temporary:
        keydir = Path(temporary)
        private, public = keydir / "private.pem", output / "ecm-build.pub"
        run(
            [
                "/usr/bin/openssl",
                "genpkey",
                "-algorithm",
                "RSA",
                "-pkeyopt",
                "rsa_keygen_bits:3072",
                "-out",
                str(private),
            ],
            cwd=work,
            env=env,
        )
        private.chmod(0o600)
        run(
            [
                "/usr/bin/openssl",
                "pkey",
                "-in",
                str(private),
                "-pubout",
                "-out",
                str(public),
            ],
            cwd=work,
            env=env,
        )
        command = [
            "/sbin/apk",
            "mkpkg",
            "--sign-key",
            str(private),
            "--files",
            str(stage),
            "--output",
            str(archive),
        ]
        for key, value in {
            "name": PACKAGE_NAME,
            "version": version,
            "arch": ARCHES[arch][0],
            "description": "ECM reviewed native providers and matching source; not vulnerability clearance",
            "license": " AND ".join(licenses),
            "origin": PACKAGE_NAME,
            "provides": " ".join(provides),
            "depends": " ".join(depends),
        }.items():
            command += ["--info", f"{key}:{value}"]
        run(command, cwd=work, env=env)
        run(
            [
                "/sbin/apk",
                "verify",
                "--network=no",
                "--keys-dir",
                str(output),
                str(archive),
            ],
            cwd=work,
            env=env,
        )
    record = {
        "schema_version": 1,
        "status": "SIGNED_OWNED_APK_ONLY",
        "architecture": arch,
        "package": {
            "name": PACKAGE_NAME,
            "version": version,
            "file": archive.name,
            "sha256": sha256(archive),
        },
        "public_key": {"file": public.name, "sha256": sha256(public)},
        "components": components,
        "provides": provides,
        "external_dependencies": depends,
        "elf_providers": libraries,
        "installed_artifacts": inventory,
        "loader_path": loader_path,
        "helper_sha256": sha256(Path(__file__)),
        "scope": "Build-only signed APK, exact native products/source binding. No final installed APK/advisory/full-image approval.",
    }
    (output / "package-output.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=sorted(ARCHES), required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    for name in COMPONENTS:
        parser.add_argument("--" + name + "-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        package(
            {name: getattr(args, name + "_dir").absolute() for name in COMPONENTS},
            args.arch,
            args.work_dir.absolute(),
            args.output_dir.absolute(),
        )
    except (BuildError, OSError, ValueError, KeyError) as exc:
        print(f"Owned Alpine packaging failed: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
