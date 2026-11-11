#!/usr/bin/env python3
"""Compile the unchanged signed FFmpeg contract against owned Alpine providers.

An intermediate native build, not final runtime package/source/security clearance.
Component contexts are freshly built evidence and are checked before compilation.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
from pathlib import Path

from alpine_repository_policy import validate_repositories
from build_alpine_codecs import RECIPE_SHA256 as CODECS_SHA
from build_alpine_codecs import create_locations, validate_platform
from build_alpine_jxl import RECIPE_SHA256 as JXL_SHA
from build_alpine_security_libraries import RECIPE_SHA256 as SECURITY_SHA
from build_alpine_security_libraries import dynamic_dependencies
from build_native_runtime import (
    ARCHES,
    BuildError,
    _ffmpeg,
    _require,
    owned_artifacts,
    run,
    sha256,
    validate_patch_plan,
)

RECIPE_SHA256 = "eec1b3bbd500d50090a9328373d6825e3d4f51b72ac6ad8650c4aab7339627ec"


def load_recipe(path: Path, assets: Path) -> dict:
    _require(
        path.is_file() and not path.is_symlink() and sha256(path) == RECIPE_SHA256,
        "reviewed Alpine FFmpeg recipe identity mismatch",
    )
    recipe = json.loads(path.read_text())
    for record in [recipe["ffmpeg"]["key"], recipe["configure"]]:
        asset = assets / record["file"]
        _require(
            asset.is_file()
            and not asset.is_symlink()
            and sha256(asset) == record["sha256"],
            "FFmpeg source configuration/signing asset mismatch",
        )
    validate_patch_plan(recipe["ffmpeg_patches"], assets)
    return recipe


def validate_interpreter(text: str, arch: str) -> None:
    _require(
        re.findall(r"\[Requesting program interpreter: ([^\]]+)\]", text)
        == [f"/lib/ld-musl-{ARCHES[arch][0]}.so.1"],
        "native musl program interpreter mismatch",
    )


def validate_component(directory: Path, arch: str, kind: str) -> dict:
    expected_sha, status = {
        "codecs": (CODECS_SHA, "BUILT_API_SMOKE_ONLY"),
        "security": (SECURITY_SHA, "BUILT_SECURITY_API_ONLY"),
        "jxl": (JXL_SHA, "BUILT_JXL_API_ONLY"),
    }[kind]
    record = json.loads((directory / "build-output.json").read_text())
    _require(
        record["architecture"] == arch
        and record["status"] == status
        and record["recipe_sha256"] == expected_sha,
        "component recipe/architecture/status mismatch",
    )
    for key, subdir in [
        ("owned_artifacts", "root"),
        ("source_files", "sources"),
        ("sdk_artifacts", "sdk"),
    ]:
        _require(
            owned_artifacts(directory / subdir) == record[key],
            "component artifact/source inventory mismatch",
        )
    return record


def build_environment(work: Path) -> dict[str, str]:
    # Configure's libc iconv symbol probe can succeed against musl while GNU
    # headers rename actual calls to libiconv_*. Keep the link input explicit.
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(work),
        "LC_ALL": "C",
        "TZ": "UTC",
        "CFLAGS": "-O2 -fPIC -I/usr/include/gnu-libiconv",
        "CXXFLAGS": "-O2 -fPIC -I/usr/include/gnu-libiconv",
        "LDFLAGS": "-L/usr/local/lib -L/usr/lib",
        "extralibs": "-liconv",
        "PKG_CONFIG_PATH": "/usr/local/lib/pkgconfig:/usr/lib/pkgconfig",
    }


def validate_gnu_iconv(libraries: list[dict]) -> None:
    _require(
        any(
            Path(item["path"]).name.startswith("libavformat.so.")
            and "libiconv.so.2" in item["needed"]
            for item in libraries
        ),
        "GNU iconv must be an actual libavformat dependency",
    )


def validate_jxl_provider(libraries: list[dict]) -> None:
    _require(
        any(
            Path(item["path"]).name.startswith("libavcodec.so.")
            and {"libjxl.so.0.12", "libjxl_threads.so.0.12"} <= set(item["needed"])
            and not any(
                "libjxl" in name and ".so.0.11" in name for name in item["needed"]
            )
            for item in libraries
        ),
        "actual owned JXL linkage mismatch",
    )


def build(
    manifest: Path,
    assets: Path,
    arch: str,
    work: Path,
    output: Path,
    codecs: Path,
    security: Path,
    jxl: Path,
    cache: Path | None,
) -> dict:
    manifest, assets, work, output = [
        p.absolute() for p in [manifest, assets, work, output]
    ]
    recipe = load_recipe(manifest, assets)
    release = Path("/etc/alpine-release").read_text()
    validate_platform(release, platform.machine(), arch)
    validate_repositories(release, Path("/etc/apk/repositories").read_text())
    components = {
        kind: validate_component(path, arch, kind)
        for kind, path in [("codecs", codecs), ("security", security), ("jxl", jxl)]
    }
    create_locations(work, output)
    root, sources = output / "root", output / "sources"
    (root / "usr/local/bin").mkdir(parents=True)
    sources.mkdir()
    # GNU iconv is required for the existing charset surface; musl iconv is not
    # substituted. The unchanged configure arguments are still checked verbatim.
    env = build_environment(work)
    _require(
        Path("/usr/include/gnu-libiconv/iconv.h").is_file(),
        "GNU iconv development provider missing",
    )
    proof = _ffmpeg(recipe, assets, work, sources, root, env, cache, arch)
    for name in ["ffmpeg", "ffprobe"]:
        header = run(
            ["/usr/bin/readelf", "-l", str(root / "usr/local/bin" / name)],
            cwd=work,
            env=env,
        )
        validate_interpreter(header, arch)
        proof["programs"][name]["program_headers"] = header
    for item in proof["libraries"]:
        library = root / item["path"]
        text = run(["/usr/bin/readelf", "-d", str(library)], cwd=work, env=env)
        sonames = re.findall(r"\(SONAME\).*\[([^\]]+)\]", text)
        _require(len(sonames) == 1, "FFmpeg library SONAME missing")
        item["needed"] = dynamic_dependencies(text, sonames[0], arch)
        item["dynamic"] = text
    validate_gnu_iconv(proof["libraries"])
    validate_jxl_provider(proof["libraries"])
    shutil.copy2(work / "ffmpeg/ffbuild/config.mak", sources / "ffmpeg-config.mak")
    retained = sources / "recipe"
    retained.mkdir()
    for record in [
        recipe["ffmpeg"]["key"],
        recipe["configure"],
        *recipe["ffmpeg_patches"],
    ]:
        target = retained / record["file"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(assets / record["file"], target)
    for path in [manifest, Path(__file__), manifest.parent / "Dockerfile.ffmpeg"]:
        shutil.copy2(path, retained / path.name)
    for name in [
        "build_native_runtime.py",
        "build_alpine_codecs.py",
        "build_alpine_security_libraries.py",
        "build_alpine_jxl.py",
        "alpine_repository_policy.py",
    ]:
        shutil.copy2(Path(__file__).with_name(name), retained / name)
    record = {
        "schema_version": 1,
        "status": "BUILT_FFMPEG_ONLY",
        "architecture": arch,
        "recipe_sha256": RECIPE_SHA256,
        "ffmpeg": proof,
        "component_receipts": components,
        "owned_artifacts": owned_artifacts(root),
        "source_files": owned_artifacts(sources),
        "builder_inventory": run(["/sbin/apk", "info", "-v"], cwd=work, env=env),
        "scope": "Signed FFmpeg and selected native provider linkage only. No final-image source, advisory, behavior or ARM clearance.",
    }
    (output / "build-output.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in [
        "manifest",
        "assets",
        "work-dir",
        "output-dir",
        "codecs-dir",
        "security-dir",
        "jxl-dir",
    ]:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--arch", required=True, choices=sorted(ARCHES))
    parser.add_argument("--cache-dir", type=Path)
    args = parser.parse_args()
    try:
        build(
            args.manifest,
            args.assets,
            args.arch,
            args.work_dir,
            args.output_dir,
            args.codecs_dir,
            args.security_dir,
            args.jxl_dir,
            args.cache_dir,
        )
    except (BuildError, OSError, ValueError) as exc:
        print(f"Alpine FFmpeg build failed: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
