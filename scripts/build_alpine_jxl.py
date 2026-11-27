#!/usr/bin/env python3
"""Build the approved seventh Alpine provider, retaining baseline skcms/JPEG APIs."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import tarfile
from pathlib import Path, PurePosixPath

from alpine_repository_policy import validate_repositories
from build_alpine_codecs import apply_patch, create_locations, validate_platform
from build_alpine_security_libraries import dynamic_dependencies, extract_source
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

RECIPE_SHA256 = "bb8155aa518712f712df123aa864e3502e0c7b56ad1a3418e3703af6f4f9a955"


def load_recipe(path: Path) -> dict:
    _require(
        path.is_file() and not path.is_symlink() and sha256(path) == RECIPE_SHA256,
        "reviewed JXL recipe identity mismatch",
    )
    recipe = json.loads(path.read_text())
    records = [
        recipe["cms"],
        recipe["source_origin"],
        recipe["baseline_spec"],
        recipe["exports"],
        *recipe["smoke_assets"],
        *recipe["patches"],
    ]
    for record in records:
        asset = path.parent / record["file"]
        _require(
            asset.is_file()
            and not asset.is_symlink()
            and sha256(asset) == record["sha256"],
            "reviewed JXL asset identity mismatch",
        )
    return recipe


def extract_cms(archive: Path, directory: Path) -> None:
    _require(
        not directory.is_symlink()
        and (not directory.exists() or not any(directory.iterdir())),
        "CMS destination must be fresh or an empty upstream submodule directory",
    )
    with tarfile.open(archive) as tar:
        names = set()
        for member in tar.getmembers():
            path = PurePosixPath(member.name)
            _require(
                bool(path.parts)
                and not path.is_absolute()
                and ".." not in path.parts
                and str(path) not in names
                and (member.isdir() or member.isreg()),
                "unsafe CMS archive member",
            )
            names.add(str(path))
        directory.mkdir(parents=True, exist_ok=True)
        tar.extractall(directory, filter="data")


def build(
    manifest: Path, arch: str, work: Path, output: Path, cache: Path | None
) -> dict:
    manifest, work, output = [p.absolute() for p in [manifest, work, output]]
    recipe = load_recipe(manifest)
    release = Path("/etc/alpine-release").read_text()
    validate_platform(release, platform.machine(), arch)
    validate_repositories(release, Path("/etc/apk/repositories").read_text())
    create_locations(work, output)
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(work),
        "LC_ALL": "C",
        "TZ": "UTC",
        "CFLAGS": "-O2 -fPIC",
        "CXXFLAGS": "-O2 -fPIC",
    }
    sources, sdk, root = output / "sources", output / "sdk", output / "root"
    sources.mkdir()
    retained = sources / "recipe"
    retained.mkdir()
    for record in [
        recipe["cms"],
        recipe["source_origin"],
        recipe["baseline_spec"],
        recipe["exports"],
        *recipe["smoke_assets"],
        *recipe["patches"],
    ]:
        target = retained / record["file"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(manifest.parent / record["file"], target)
    shutil.copy2(manifest, retained / manifest.name)
    for path in [
        Path(__file__),
        manifest.parent / "Dockerfile.libjxl",
        *[
            Path(__file__).with_name(name)
            for name in [
                "build_native_runtime.py",
                "build_alpine_codecs.py",
                "build_alpine_security_libraries.py",
                "alpine_repository_policy.py",
            ]
        ],
    ]:
        shutil.copy2(path, retained / path.name)
    archive = sources / recipe["archive"]["filename"]
    fetch_input(recipe["archive"], archive, cache)
    source = work / "libjxl"
    extract_source(archive, source)
    extract_cms(manifest.parent / recipe["cms"]["file"], source / "third_party/skcms")
    patches = [
        apply_patch(record, manifest.parent, source, env)
        for record in recipe["patches"]
    ]
    source_inventory = owned_artifacts(source)
    directory = work / "jxl-build"
    run(
        [
            "/usr/bin/cmake",
            "-S",
            str(source),
            "-B",
            str(directory),
            *recipe["configure"],
        ],
        cwd=work,
        env=env,
    )
    configuration = (directory / "CMakeCache.txt").read_text()
    for option, value in [
        ("JPEGXL_ENABLE_SKCMS", "ON"),
        ("JPEGXL_FORCE_SYSTEM_LCMS2", "OFF"),
        ("JPEGXL_ENABLE_TRANSCODE_JPEG", "ON"),
        ("JPEGXL_ENABLE_BOXES", "ON"),
    ]:
        _require(
            f"{option}:BOOL={value}\n" in configuration,
            "JXL baseline color/format configuration mismatch",
        )
    run(
        [
            "/usr/bin/cmake",
            "--build",
            str(directory),
            "--target",
            "jxl",
            "jxl_threads",
            "--parallel",
            "2",
        ],
        cwd=work,
        env=env,
    )
    run(
        ["/usr/bin/cmake", "--install", str(directory)],
        cwd=work,
        env={**env, "DESTDIR": str(sdk)},
    )
    shutil.copy2(directory / "CMakeCache.txt", sources / "jxl-CMakeCache.txt")
    libraries = []
    (root / "usr/local/lib").mkdir(parents=True)
    expected = json.loads((manifest.parent / recipe["exports"]["file"]).read_text())
    for item in recipe["libraries"]:
        for path in sorted((sdk / "usr/local/lib").glob(item["name"] + "*")):
            shutil.copy2(
                path, root / "usr/local/lib" / path.name, follow_symlinks=False
            )
        library = root / "usr/local/lib" / item["soname"]
        header = verify_elf(library, arch, env)
        dynamic = run(["/usr/bin/readelf", "-d", str(library)], cwd=work, env=env)
        needed = dynamic_dependencies(dynamic, item["soname"], arch)
        _require(
            not any("lcms2" in name or "libjxl.so.0.11" in name for name in needed),
            "JXL color engine or provider substituted",
        )
        nm = run(
            ["/usr/bin/nm", "-D", "--defined-only", str(library)], cwd=work, env=env
        )
        symbols = sorted(
            {
                line.split()[-1].split("@")[0]
                for line in nm.splitlines()
                if line.split()[-1].startswith("Jxl")
            }
        )
        _require(
            symbols == expected[item["name"]]["public_symbols"],
            "JXL baseline public API export mismatch",
        )
        libraries.append(
            {
                **item,
                "readelf": header,
                "dynamic": dynamic,
                "needed": needed,
                "public_symbols": symbols,
            }
        )
    smoke_text = run(
        [
            "/usr/local/bin/python",
            str(manifest.parent / "jxl-smoke.py"),
            "--library",
            str(root / "usr/local/lib/libjxl.so.0.12"),
        ],
        cwd=work,
        env={**env, "LD_LIBRARY_PATH": str(root / "usr/local/lib")},
    )
    smoke = json.loads(smoke_text)
    _require(
        smoke.get("versions")
        == {"JxlDecoderVersion": 12000, "JxlEncoderVersion": 12000}
        and smoke.get("rgb_sha256")
        == "a0d99fe349fe48d113c16adf5c3f582b70dbfb3cf5fa8bd0e59e0833b78c2a93"
        and smoke.get("jpeg_reconstruction_exact") is True
        and smoke.get("native_thread_runner") is True,
        "JXL native smoke mismatch",
    )
    notices = root / "usr/share/doc/ecm/alpine-native/libjxl"
    notices.mkdir(parents=True)
    shutil.copy2(source / "LICENSE", notices / "LICENSE")
    shutil.copy2(source / "third_party/skcms/LICENSE", notices / "skcms-LICENSE")
    record = {
        "schema_version": 1,
        "status": "BUILT_JXL_API_ONLY",
        "architecture": arch,
        "recipe_sha256": RECIPE_SHA256,
        "source_identity": recipe,
        "patches": patches,
        "patched_source_files": source_inventory,
        "libraries": libraries,
        "smoke": smoke,
        "builder_inventory": run(["/sbin/apk", "info", "-v"], cwd=work, env=env),
        "owned_artifacts": owned_artifacts(root),
        "sdk_artifacts": owned_artifacts(sdk),
        "source_files": owned_artifacts(sources),
        "scope": "Owned libjxl/skcms and bounded baseline/JPEG/thread API checks only; not final-image/source/advisory/ARM parity approval.",
    }
    (output / "build-output.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["manifest", "work-dir", "output-dir"]:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--arch", choices=sorted(ARCHES), required=True)
    parser.add_argument("--cache-dir", type=Path)
    args = parser.parse_args()
    try:
        build(args.manifest, args.arch, args.work_dir, args.output_dir, args.cache_dir)
    except (BuildError, OSError, ValueError, tarfile.TarError) as exc:
        print(f"Alpine JXL build failed: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
