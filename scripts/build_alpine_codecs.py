#!/usr/bin/env python3
"""Build only Codec2/TwoLAME/Shine in a disposable native stable-Alpine builder.

This is an incremental migration interface, not a complete native runtime or a
parity/security approval. No package installation or host provisioning occurs.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
from pathlib import Path

from alpine_repository_policy import validate_repositories
from build_native_runtime import (
    ARCHES,
    BuildError,
    _require,
    fetch_input,
    owned_artifacts,
    run,
    safe_extract,
    sha256,
    verify_elf,
)

RECIPE_SHA256 = "56865d627f9507a3d81701898453f44c7a279b5ab756b1e4a6354aed7036f8b6"
SMOKE_SHA256 = "ab0a0b4f68141f69a2ad340683d55e329e2ce0f067cd5e1ace898991917f8186"


def load_recipe(path: Path) -> dict:
    _require(
        path.is_file() and not path.is_symlink() and sha256(path) == RECIPE_SHA256,
        "reviewed codec recipe identity mismatch",
    )
    recipe = json.loads(path.read_text())
    for item in recipe["libraries"]:
        if "exports" in item:
            record = item["exports"]
            asset = path.parent / record["file"]
            _require(
                asset.is_file()
                and not asset.is_symlink()
                and sha256(asset) == record["sha256"],
                "reviewed codec export surface identity mismatch",
            )
        if "numeric" in item:
            for record in [item["numeric"], item["numeric"]["wrapper"]]:
                asset = path.parent / record["file"]
                _require(
                    asset.is_file()
                    and not asset.is_symlink()
                    and sha256(asset) == record["sha256"],
                    "reviewed numeric source identity mismatch",
                )
        for patch in item["patches"]:
            asset = path.parent / patch["file"]
            _require(
                asset.is_file()
                and not asset.is_symlink()
                and sha256(asset) == patch["sha256"],
                "reviewed codec patch identity mismatch",
            )
    smoke = path.parent / "codec-smoke.c"
    _require(
        smoke.is_file() and not smoke.is_symlink() and sha256(smoke) == SMOKE_SHA256,
        "reviewed codec smoke identity mismatch",
    )
    return recipe


def validate_platform(release: str, machine: str, arch: str) -> None:
    _require(
        re.fullmatch(r"3\.24\.\d+", release.strip()) is not None
        and arch in ARCHES
        and machine == ARCHES[arch][0],
        "stable Alpine native platform mismatch",
    )


def create_locations(work: Path, output: Path) -> None:
    paths = [work, output]
    _require(
        all(not p.exists() and not p.is_symlink() for p in paths)
        and work.resolve() != output.resolve()
        and not work.resolve().is_relative_to(output.resolve())
        and not output.resolve().is_relative_to(work.resolve()),
        "build locations must be new and disjoint",
    )
    work.mkdir(parents=True)
    output.mkdir(parents=True)


def apply_patch(record: dict, assets: Path, source: Path, env: dict) -> dict:
    patch = assets / record["file"]
    _require(sha256(patch) == record["sha256"], "codec patch checksum mismatch")
    for witness in record["witnesses"]:
        path = source / witness["path"]
        _require(
            path.is_file()
            and not path.is_symlink()
            and path.resolve().is_relative_to(source.resolve())
            and sha256(path) == witness["before_sha256"],
            "codec patch preimage mismatch",
        )
    status = run(
        ["/usr/bin/patch", "--batch", "--fuzz=0", "-p1", "--input", str(patch)],
        cwd=source,
        env=env,
    )
    for witness in record["witnesses"]:
        _require(
            sha256(source / witness["path"]) == witness["after_sha256"],
            "codec patch postimage mismatch",
        )
    return {**record, "apply_output": status}


def validate_dynamic(text: str, soname: str, arch: str) -> list[str]:
    names = re.findall(r"\(SONAME\).*\[([^\]]+)\]", text)
    needed = re.findall(r"\(NEEDED\).*\[([^\]]+)\]", text)
    allowed = {f"libc.musl-{ARCHES[arch][0]}.so.1"}
    _require(
        names == [soname]
        and bool(needed)
        and set(needed) <= allowed
        and len(needed) == len(set(needed))
        and not re.search(r"\((?:RPATH|RUNPATH)\)", text),
        "codec dynamic ABI/dependency/path mismatch",
    )
    return needed


def stage_numeric(record: dict, assets: Path, source: Path) -> None:
    archive, wrapper = assets / record["file"], assets / record["wrapper"]["file"]
    for path, digest in [
        (archive, record["sha256"]),
        (wrapper, record["wrapper"]["sha256"]),
    ]:
        _require(
            path.is_file() and not path.is_symlink() and sha256(path) == digest,
            "reviewed numeric source identity mismatch",
        )
    numeric = source / "src/numeric"
    safe_extract(archive, numeric)
    _require(
        {p.name for p in numeric.iterdir()} == {p["path"] for p in record["sources"]},
        "numeric source inventory mismatch",
    )
    for item in record["sources"]:
        path = numeric / item["path"]
        _require(
            path.is_file() and not path.is_symlink() and sha256(path) == item["sha256"],
            "numeric source member mismatch",
        )
    shutil.copy2(wrapper, numeric / "codec2_numeric.c")


def validate_exports(text: str, expected: list[str]) -> list[str]:
    names = [line.split()[-1] for line in text.splitlines() if line.strip()]
    _require(
        len(names) == len(set(names))
        and set(expected) <= set(names)
        and set(names) - set(expected) <= {"_init", "_fini"},
        "codec export surface mismatch",
    )
    return sorted(names)


def validate_smoke(text: str) -> dict:
    try:
        result = json.loads(text)
        _require(
            set(result)
            == {"codec2_samples", "codec2_bytes", "twolame_bytes", "shine_bytes"}
            and all(type(v) is int for v in result.values())
            and result["codec2_samples"] == 160
            and result["codec2_bytes"] == 8
            and result["twolame_bytes"] > 4
            and result["shine_bytes"] > 4,
            "codec smoke result mismatch",
        )
        return result
    except (ValueError, TypeError) as exc:
        raise BuildError("invalid codec smoke result") from exc


def validate_smoke_files(directory: Path, result: dict) -> None:
    expected = {
        "codec2.bin": 16 * result["codec2_bytes"],
        "codec2-decoded.raw": 16 * result["codec2_samples"] * 2,
        "twolame.mp2": result["twolame_bytes"],
        "shine.mp3": result["shine_bytes"],
    }
    _require(
        {p.name for p in directory.iterdir()} == set(expected),
        "codec smoke vector set mismatch",
    )
    for name, size in expected.items():
        path = directory / name
        _require(
            path.is_file() and not path.is_symlink() and path.stat().st_size == size,
            "codec smoke vector size/type mismatch",
        )
    for name, layer in [("twolame.mp2", 2), ("shine.mp3", 1)]:
        header = (directory / name).read_bytes()[:2]
        _require(
            header[0] == 0xFF and header[1] & 0xFE == 0xF8 | (layer << 1),
            "codec smoke MPEG frame header mismatch",
        )


def build(
    manifest: Path, arch: str, work: Path, output: Path, cache: Path | None
) -> dict:
    manifest, work, output = manifest.absolute(), work.absolute(), output.absolute()
    recipe = load_recipe(manifest)
    release = Path("/etc/alpine-release").read_text()
    validate_platform(release, platform.machine(), arch)
    # The repository guard is exercised before any source or compiler operation.
    validate_repositories(release, Path("/etc/apk/repositories").read_text())
    create_locations(work, output)
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(work),
        "LC_ALL": "C",
        "TZ": "UTC",
        "CFLAGS": "-O2 -fPIC",
        "CXXFLAGS": "-O2 -fPIC",
        "LDFLAGS": "",
    }
    sources, sdk, root = output / "sources", output / "sdk", output / "root"
    sources.mkdir()
    sdk.mkdir()
    (root / "usr/local/lib").mkdir(parents=True)
    shutil.copytree(manifest.parent, sources / "recipe")
    for helper in [
        Path(__file__),
        Path(__file__).with_name("build_native_runtime.py"),
        Path(__file__).with_name("alpine_repository_policy.py"),
    ]:
        shutil.copy2(helper, sources / "recipe" / helper.name)
    inventory = run(["/sbin/apk", "info", "-v"], cwd=work, env=env)
    compiler = run(["/usr/bin/cc", "--version"], cwd=work, env=env)
    records = []
    for item in recipe["libraries"]:
        archive = sources / item["archive"]["filename"]
        fetch_input(item["archive"], archive, cache)
        source = work / item["name"]
        safe_extract(archive, source)
        patches = [
            apply_patch(p, manifest.parent, source, env) for p in item["patches"]
        ]
        if "numeric" in item:
            stage_numeric(item["numeric"], manifest.parent, source)
        patched_source = owned_artifacts(source)
        if item["name"] == "codec2":
            directory = work / "codec2-build"
            run(
                [
                    "/usr/bin/cmake",
                    "-S",
                    str(source),
                    "-B",
                    str(directory),
                    "-DCMAKE_INSTALL_PREFIX=/usr/local",
                    "-DCMAKE_INSTALL_LIBDIR=lib",
                    *item["configure"],
                ],
                cwd=work,
                env=env,
            )
            run(
                ["/usr/bin/cmake", "--build", str(directory), "--parallel", "2"],
                cwd=work,
                env=env,
            )
            run(
                ["/usr/bin/cmake", "--install", str(directory)],
                cwd=work,
                env={**env, "DESTDIR": str(sdk)},
            )
        else:
            run(
                [str(source / "configure"), "--prefix=/usr/local", *item["configure"]],
                cwd=source,
                env=env,
            )
            run(["/usr/bin/make", "-j2"], cwd=source, env=env)
            run(
                ["/usr/bin/make", "install", "DESTDIR=" + str(sdk)], cwd=source, env=env
            )
        version = run(
            ["/usr/bin/pkgconf", "--modversion", item["name"]],
            cwd=work,
            env={**env, "PKG_CONFIG_LIBDIR": str(sdk / "usr/local/lib/pkgconfig")},
        ).strip()
        _require(
            version == item["version"], "installed codec metadata version mismatch"
        )
        # Only the library graph was installed in the baseline runtime, not the
        # optional standalone codec frontends. Keep devel/tools in sdk/, not root/.
        family = item["soname"].split(".so.")[0] + ".so*"
        selected = sorted((sdk / "usr/local/lib").glob(family))
        _require(bool(selected), "required codec shared library missing")
        for path in selected:
            shutil.copy2(
                path, root / "usr/local/lib" / path.name, follow_symlinks=False
            )
        library = root / "usr/local/lib" / item["soname"]
        header = verify_elf(library, arch, env)
        dynamic = run(["/usr/bin/readelf", "-d", str(library)], cwd=work, env=env)
        needed = validate_dynamic(dynamic, item["soname"], arch)
        exports = None
        if "exports" in item:
            symbols = run(
                ["/usr/bin/nm", "-D", "--defined-only", str(library)], cwd=work, env=env
            )
            exports = validate_exports(
                symbols,
                (manifest.parent / item["exports"]["file"]).read_text().splitlines(),
            )
        notices = root / "usr/share/doc/ecm/alpine-native" / item["name"]
        notices.mkdir(parents=True)
        shutil.copy2(source / "COPYING", notices / "COPYING")
        if "numeric" in item:
            shutil.copy2(
                source / "src/numeric/LICENSE",
                notices / "Arm-optimized-routines-LICENSE",
            )
            (notices / "Arm-license-selection.txt").write_text(
                "ECM selects the MIT option for these pinned Arm scalar sources.\n"
                "Commit: " + item["numeric"]["commit"] + "\n"
                "Codec-local hidden symbols only; USE_GLIBC_ABI=0.\n"
            )
        records.append(
            {
                **item,
                "patches": patches,
                "patched_source_files": patched_source,
                "readelf": header,
                "dynamic": dynamic,
                "needed": needed,
                "verified_exports": exports,
            }
        )
    executable = work / "codec-smoke"
    run(
        [
            "/usr/bin/cc",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(manifest.parent / "codec-smoke.c"),
            "-I" + str(sdk / "usr/local/include"),
            "-L" + str(root / "usr/local/lib"),
            "-lcodec2",
            "-ltwolame",
            "-lshine",
            "-o",
            str(executable),
        ],
        cwd=work,
        env=env,
    )
    smoke_dir = output / "smoke"
    smoke_dir.mkdir()
    result = validate_smoke(
        run(
            [str(executable)],
            cwd=smoke_dir,
            env={**env, "LD_LIBRARY_PATH": str(root / "usr/local/lib")},
        )
    )
    validate_smoke_files(smoke_dir, result)
    # Retain vectors for later paired comparisons; this is only an API smoke.
    record = {
        "schema_version": 1,
        "status": "BUILT_API_SMOKE_ONLY",
        "architecture": arch,
        "recipe_sha256": RECIPE_SHA256,
        "libraries": records,
        "builder_inventory": inventory,
        "compiler": compiler,
        "smoke": result,
        "owned_artifacts": owned_artifacts(root),
        "source_files": owned_artifacts(sources),
        "smoke_files": owned_artifacts(smoke_dir),
        "scope": "Three library builds and bounded API operations only; no full runtime, security, ARM or paired parity approval.",
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
    except (BuildError, OSError, ValueError) as exc:
        print(f"Alpine codec build failed: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
