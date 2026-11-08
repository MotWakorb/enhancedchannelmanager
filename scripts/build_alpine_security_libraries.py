#!/usr/bin/env python3
"""Build reviewed libxml2/Speex/librsvg sources in a disposable Alpine builder.

This installs owned build products into the builder's /usr for dependent builds.
Never invoke on a host or application runtime. Final package/source/scan binding
belongs to the composite runtime gate, not this intermediate build receipt.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import posixpath
import re
import shutil
import tarfile
import tomllib
from pathlib import Path, PurePosixPath

from alpine_repository_policy import validate_repositories
from build_alpine_codecs import apply_patch, create_locations, validate_platform
from build_native_runtime import (
    ARCHES,
    BuildError,
    _require,
    fetch_input,
    owned_artifacts,
    run,
    sha256,
    verify_elf,
)

RECIPE_SHA256 = "059ce48c90dcfb2ed728d3f33b37ccb5f7bc380602bc056d7fcceca639f4451b"
SMOKE_SHA256 = "c30e55b7265151165d04e973e43be391b582dd6c30b2d20ff5b370ea8577b547"


def load_recipe(path: Path) -> dict:
    _require(
        path.is_file() and not path.is_symlink() and sha256(path) == RECIPE_SHA256,
        "reviewed security recipe identity mismatch",
    )
    recipe = json.loads(path.read_text())
    capacity = path.parent / recipe["capacity_smoke"]["file"]
    _require(
        capacity.is_file()
        and not capacity.is_symlink()
        and sha256(capacity) == recipe["capacity_smoke"]["sha256"],
        "reviewed Speex capacity smoke identity mismatch",
    )
    smoke = path.parent / "security-smoke.c"
    _require(
        smoke.is_file() and not smoke.is_symlink() and sha256(smoke) == SMOKE_SHA256,
        "reviewed security smoke identity mismatch",
    )
    for item in recipe["libraries"]:
        if "source_equivalence" in item:
            proof = item["source_equivalence"]
            equivalence_path = path.parent / proof["file"]
            _require(
                equivalence_path.is_file()
                and not equivalence_path.is_symlink()
                and sha256(equivalence_path) == proof["sha256"],
                "source equivalence identity mismatch",
            )
        for record in [*item["patches"], *item.get("retained_upstream_patches", [])]:
            asset = path.parent / record["file"]
            _require(
                asset.is_file()
                and not asset.is_symlink()
                and sha256(asset) == record["sha256"],
                "security patch identity mismatch",
            )
        if "vendor" in item:
            vendor = item["vendor"]
            for name, digest in [
                (vendor["file"], vendor["sha256"]),
                (vendor["origin_file"], vendor["origin_sha256"]),
            ]:
                asset = path.parent / name
                _require(
                    asset.is_file()
                    and not asset.is_symlink()
                    and sha256(asset) == digest,
                    "retained vendor identity mismatch",
                )
    return recipe


def _extract(archive: Path, directory: Path, *, roots: set[str] | None) -> set[str]:
    _require(
        not directory.exists() and not directory.is_symlink(),
        "source extraction destination exists",
    )
    with tarfile.open(archive) as tar:
        members, names, observed = tar.getmembers(), set(), set()
        for member in members:
            path = PurePosixPath(member.name)
            _require(
                bool(path.parts)
                and not path.is_absolute()
                and ".." not in path.parts
                and str(path) not in names
                and (member.isdir() or member.isreg() or member.issym()),
                "unsafe source archive member",
            )
            names.add(str(path))
            observed.add(path.parts[0])
            if member.issym():
                target = PurePosixPath(member.linkname)
                resolved = PurePosixPath(posixpath.normpath(str(path.parent / target)))
                _require(
                    not target.is_absolute()
                    and bool(resolved.parts)
                    and resolved.parts[0] == path.parts[0],
                    "unsafe source archive symlink",
                )
        _require(
            observed == roots if roots is not None else len(observed) == 1,
            "unsafe source archive roots",
        )
        directory.mkdir()
        tar.extractall(directory, filter="data")
    return observed


def extract_source(archive: Path, source: Path) -> None:
    temporary = source.with_name(source.name + "-extract")
    roots = _extract(archive, temporary, roots=None)
    (temporary / roots.pop()).rename(source)
    temporary.rmdir()


def verify_vendor(source: Path, expected_lock: str) -> list[dict]:
    """Join every registry crate and every retained file to the bound Cargo lock."""
    lock = source / "Cargo.lock"
    _require(sha256(lock) == expected_lock, "reviewed vendor lock mismatch")
    packages = tomllib.loads(lock.read_text())["package"]
    expected = {
        (p["name"], p["version"]): p["checksum"] for p in packages if p.get("source")
    }
    _require(
        all(
            p.get("source")
            in {None, "registry+https://github.com/rust-lang/crates.io-index"}
            for p in packages
        ),
        "unreviewed Cargo source",
    )
    actual = {}
    for crate in sorted((source / "vendor").iterdir()):
        _require(
            crate.is_dir() and not crate.is_symlink(), "unsafe vendor crate directory"
        )
        metadata = tomllib.loads((crate / "Cargo.toml").read_text())["package"]
        identity = metadata["name"], metadata["version"]
        checksum = json.loads((crate / ".cargo-checksum.json").read_text())
        _require(
            identity in expected
            and identity not in actual
            and checksum["package"] == expected[identity],
            "vendor crate/lock identity mismatch",
        )
        observed = {
            str(p.relative_to(crate))
            for p in crate.rglob("*")
            if p.is_file() and p.name != ".cargo-checksum.json"
        }
        _require(observed == set(checksum["files"]), "vendor file inventory mismatch")
        for name, digest in checksum["files"].items():
            path = crate / name
            _require(
                path.resolve().is_relative_to(crate.resolve())
                and not path.is_symlink()
                and sha256(path) == digest,
                "vendor file checksum mismatch",
            )
        actual[identity] = {
            "name": identity[0],
            "version": identity[1],
            "checksum": checksum["package"],
        }
    _require(set(actual) == set(expected), "incomplete vendor dependency closure")
    return list(actual.values())


def dynamic_dependencies(text: str, soname: str, arch: str) -> list[str]:
    names = re.findall(r"\(SONAME\).*\[([^\]]+)\]", text)
    needed = re.findall(r"\(NEEDED\).*\[([^\]]+)\]", text)
    forbidden = {
        "libc.so.6",
        "libm.so.6",
        "libpthread.so.0",
        "libdl.so.2",
        "ld-linux-x86-64.so.2",
        "ld-linux-aarch64.so.1",
    }
    _require(
        names == [soname]
        and needed
        and len(needed) == len(set(needed))
        and not set(needed) & forbidden
        and f"libc.musl-{ARCHES[arch][0]}.so.1" in needed
        and not any(
            "libc.musl-" in name and name != f"libc.musl-{ARCHES[arch][0]}.so.1"
            for name in needed
        )
        and not re.search(r"\((?:RPATH|RUNPATH)\)", text),
        "security library dynamic ABI/path mismatch",
    )
    return needed


def configure_cargo(source: Path, home: Path, evidence: Path) -> None:
    """Meson runs Cargo from the build tree, so source-local config is insufficient."""
    _require(not home.exists() and not home.is_symlink(), "Cargo home must be fresh")
    vendor = (source / "vendor").resolve()
    _require(vendor.is_dir(), "verified vendor directory missing")
    home.mkdir()
    # JSON quoted strings are also TOML basic strings, including escaped paths.
    config = (
        "[net]\noffline = true\n\n[source.crates-io]\n"
        'replace-with = "vendored-sources"\n\n[source.vendored-sources]\n'
        f"directory = {json.dumps(str(vendor))}\n"
    )
    (home / "config.toml").write_text(config)
    (evidence / "cargo-config.toml").write_text(config)


def validate_smoke(text: str) -> dict:
    try:
        result = json.loads(text)
        _require(
            set(result)
            == {
                "xml_nonet_text",
                "xml_nonet_xml",
                "xml_tree_nonet_text",
                "xml_tree_nonet_xml",
                "speex_external_buffer_canary",
            }
            and all(value is True for value in result.values()),
            "security smoke result mismatch",
        )
        return result
    except (TypeError, ValueError) as exc:
        raise BuildError("invalid security smoke result") from exc


def _install_builder_sdk(sdk: Path) -> None:
    # Only run after native Alpine/platform/repository checks in a disposable
    # builder. Do not copy the SDK or old builder providers into a runtime.
    for relative in ["usr/include", "usr/lib/pkgconfig"]:
        path = sdk / relative
        if path.is_dir():
            shutil.copytree(
                path, Path("/") / relative, dirs_exist_ok=True, symlinks=True
            )
    for path in (sdk / "usr/lib").glob("*.so*"):
        target = Path("/usr/lib") / path.name
        if path.is_symlink() or target.is_symlink():
            target.unlink(missing_ok=True)
        shutil.copy2(path, target, follow_symlinks=False)


def build(
    manifest: Path, arch: str, work: Path, output: Path, cache: Path | None
) -> dict:
    manifest, work, output = manifest.absolute(), work.absolute(), output.absolute()
    recipe = load_recipe(manifest)
    release = Path("/etc/alpine-release").read_text()
    validate_platform(release, platform.machine(), arch)
    validate_repositories(release, Path("/etc/apk/repositories").read_text())
    _require(os.geteuid() == 0, "security builder requires disposable container root")
    create_locations(work, output)
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(work),
        "LC_ALL": "C",
        "TZ": "UTC",
        "CFLAGS": "-O2 -fPIC",
        "CXXFLAGS": "-O2 -fPIC",
        "CARGO_NET_OFFLINE": "true",
        "CARGO_BUILD_JOBS": "2",
        "CARGO_HOME": str(work / "cargo-home"),
        "CARGO_TARGET_DIR": "/proof/cargo-target",
    }
    sources, sdk, root = output / "sources", output / "sdk", output / "root"
    sources.mkdir()
    sdk.mkdir()
    (root / "usr/lib").mkdir(parents=True)
    # Retained archives are indexed separately, not recursively duplicated.
    (sources / "recipe").mkdir()
    shutil.copy2(manifest, sources / "recipe/security-libraries.json")
    shutil.copy2(
        manifest.parent / "security-smoke.c", sources / "recipe/security-smoke.c"
    )
    shutil.copy2(
        manifest.parent / recipe["capacity_smoke"]["file"],
        sources / "recipe" / recipe["capacity_smoke"]["file"],
    )
    shutil.copy2(
        manifest.parent / "Dockerfile.security-libraries",
        sources / "recipe/Dockerfile.security-libraries",
    )
    shutil.copytree(
        manifest.parent / "patches/security", sources / "recipe/patches/security"
    )
    for helper in [
        Path(__file__),
        Path(__file__).with_name("build_alpine_codecs.py"),
        Path(__file__).with_name("build_native_runtime.py"),
        Path(__file__).with_name("alpine_repository_policy.py"),
    ]:
        shutil.copy2(helper, sources / "recipe" / helper.name)
    inventory = run(["/sbin/apk", "info", "-v"], cwd=work, env=env)
    records = []
    for item in recipe["libraries"]:
        archive = sources / item["archive"]["filename"]
        fetch_input(item["archive"], archive, cache)
        source = work / item["name"]
        extract_source(archive, source)
        patches = [
            apply_patch(patch, manifest.parent, source, env)
            for patch in item["patches"]
        ]
        vendor_record = None
        if item["name"] == "librsvg":
            vendor = item["vendor"]
            vendor_archive = sources / "recipe" / vendor["file"]
            vendor_archive.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(manifest.parent / vendor["file"], vendor_archive)
            shutil.copy2(
                manifest.parent / vendor["origin_file"],
                sources / "recipe" / vendor["origin_file"],
            )
            equivalence = item["source_equivalence"]["file"]
            shutil.copy2(
                manifest.parent / equivalence, sources / "recipe" / equivalence
            )
            unpacked = work / "vendor-extract"
            _extract(vendor_archive, unpacked, roots={".cargo", "Cargo.lock", "vendor"})
            before_lock = sha256(source / "Cargo.lock")
            shutil.copytree(unpacked / ".cargo", source / ".cargo", dirs_exist_ok=True)
            shutil.copytree(unpacked / "vendor", source / "vendor")
            shutil.copy2(unpacked / "Cargo.lock", source / "Cargo.lock")
            vendor_record = {
                "upstream_lock_sha256": before_lock,
                "baseline_vendor_lock_sha256": vendor["lock_sha256"],
                "crates": verify_vendor(source, vendor["lock_sha256"]),
            }
            configure_cargo(source, Path(env["CARGO_HOME"]), sources / "recipe")
        patched_source = owned_artifacts(source)
        if item["name"] == "librsvg":
            directory = work / "rsvg-build"
            run(
                [
                    "/usr/bin/meson",
                    "setup",
                    str(directory),
                    str(source),
                    *item["configure"],
                ],
                cwd=work,
                env=env,
            )
            run(
                ["/usr/bin/meson", "compile", "-C", str(directory), "-j2"],
                cwd=work,
                env=env,
            )
            run(
                [
                    "/usr/bin/meson",
                    "install",
                    "--no-rebuild",
                    "-C",
                    str(directory),
                    "--destdir",
                    str(sdk),
                ],
                cwd=work,
                env=env,
            )
        else:
            run([str(source / "configure"), *item["configure"]], cwd=source, env=env)
            run(["/usr/bin/make", "-j2"], cwd=source, env=env)
            run(["/usr/bin/make", "-j2", "check"], cwd=source, env=env)
            run(
                ["/usr/bin/make", "install", "DESTDIR=" + str(sdk)], cwd=source, env=env
            )
        _install_builder_sdk(sdk)
        capacity_proof = None
        if item["name"] == "speex":
            fixture = manifest.parent / recipe["capacity_smoke"]["file"]
            capacity_proof = {}
            for kind in ["native-library", "realloc-failure-sanitized-source"]:
                executable = work / ("speex-capacity-" + kind)
                command = [
                    "/usr/bin/cc",
                    "-O1",
                    "-g",
                    str(fixture),
                    "-I" + str(sdk / "usr/include"),
                    "-o",
                    str(executable),
                ]
                if kind == "native-library":
                    command += [
                        "-Wall",
                        "-Wextra",
                        "-Werror",
                        "-L" + str(sdk / "usr/lib"),
                        "-lspeex",
                    ]
                else:
                    command += [
                        "-fsanitize=address,undefined",
                        "-fno-sanitize-recover=all",
                        "-DHAVE_CONFIG_H",
                        "-DECM_SPEEX_REALLOC_FAILURE",
                        "-I" + str(source),
                        "-I" + str(source / "libspeex"),
                    ]
                run(command, cwd=work, env=env)
                result = run(
                    [str(executable)],
                    cwd=work,
                    env={**env, "LD_LIBRARY_PATH": str(sdk / "usr/lib")},
                )
                _require(
                    result.strip() == "SPEEX_CAPACITY_BOUNDARIES_PASS",
                    "Speex capacity behavior mismatch",
                )
                capacity_proof[kind] = {
                    "command": command,
                    "result": result,
                    "executable_sha256": sha256(executable),
                }
        family = item["soname"].split(".so.")[0] + ".so*"
        selected = sorted((sdk / "usr/lib").glob(family))
        _require(bool(selected), "required owned security library missing")
        for path in selected:
            shutil.copy2(path, root / "usr/lib" / path.name, follow_symlinks=False)
        library = root / "usr/lib" / item["soname"]
        header = verify_elf(library, arch, env)
        dynamic = run(["/usr/bin/readelf", "-d", str(library)], cwd=work, env=env)
        needed = dynamic_dependencies(dynamic, item["soname"], arch)
        notice = (
            source
            / {"libxml2": "Copyright", "speex": "COPYING", "librsvg": "COPYING.LIB"}[
                item["name"]
            ]
        )
        notices = root / "usr/share/doc/ecm/alpine-native" / item["name"]
        notices.mkdir(parents=True)
        shutil.copy2(notice, notices / notice.name)
        records.append(
            {
                **item,
                "patches": patches,
                "patched_source_files": patched_source,
                "readelf": header,
                "dynamic": dynamic,
                "needed": needed,
                "vendor_proof": vendor_record,
                "capacity_proof": capacity_proof,
            }
        )
    smoke = work / "security-smoke"
    run(
        [
            "/usr/bin/cc",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(manifest.parent / "security-smoke.c"),
            "-I" + str(sdk / "usr/include/libxml2"),
            "-I" + str(sdk / "usr/include"),
            "-L" + str(root / "usr/lib"),
            "-lxml2",
            "-lspeex",
            "-o",
            str(smoke),
        ],
        cwd=work,
        env=env,
    )
    smoke_result = validate_smoke(
        run(
            [str(smoke)],
            cwd=work,
            env={**env, "LD_LIBRARY_PATH": str(root / "usr/lib")},
        )
    )
    rsvg = sdk / "usr/bin/rsvg-convert"
    svg = work / "fixture.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="16"><rect width="32" height="16" fill="red"/></svg>'
    )
    png = output / "fixture.png"
    run(
        [str(rsvg), "--output", str(png), str(svg)],
        cwd=work,
        env={**env, "LD_LIBRARY_PATH": str(root / "usr/lib")},
    )
    data = png.read_bytes()
    _require(
        data[:8] == b"\x89PNG\r\n\x1a\n"
        and int.from_bytes(data[16:20], "big") == 32
        and int.from_bytes(data[20:24], "big") == 16,
        "librsvg native render mismatch",
    )
    record = {
        "schema_version": 1,
        "status": "BUILT_SECURITY_API_ONLY",
        "architecture": arch,
        "recipe_sha256": RECIPE_SHA256,
        "libraries": records,
        "builder_inventory": inventory,
        "smoke": smoke_result,
        "render_sha256": sha256(png),
        "owned_artifacts": owned_artifacts(root),
        "source_files": owned_artifacts(sources),
        "scope": "Three owned security providers only; final packages/source/inventory/scans and both native whole-image gates remain separate.",
    }
    (output / "build-output.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--manifest", type=Path, required=True)
    result.add_argument("--arch", choices=sorted(ARCHES), required=True)
    result.add_argument("--work-dir", type=Path, required=True)
    result.add_argument("--output-dir", type=Path, required=True)
    result.add_argument("--cache-dir", type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        build(args.manifest, args.arch, args.work_dir, args.output_dir, args.cache_dir)
    except (BuildError, OSError, ValueError, tarfile.TarError) as exc:
        print(f"Alpine security-library build failed: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
