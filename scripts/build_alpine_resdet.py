#!/usr/bin/env python3
"""Build the unchanged resdet recipe for stable Alpine with retained source proof."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
from pathlib import Path

from alpine_repository_policy import validate_repositories
from build_alpine_codecs import create_locations, validate_platform
from build_native_runtime import (
    BuildError,
    _require,
    owned_artifacts,
    run,
    sha256,
    verify_elf,
)
from build_resdet import build as build_resdet
from generate_sbom import _read_json, parse_native_dependencies

__all__ = ["BuildError", "build", "source_input"]
RECIPE_SHA256 = "530f7498262e5c38541513e01ca12b4f6964b61b80273f75ec8956ff5e17a82a"


def source_input(manifest: Path) -> dict:
    _require(
        manifest.is_file()
        and not manifest.is_symlink()
        and sha256(manifest) == RECIPE_SHA256,
        "original resdet recipe identity mismatch",
    )
    packages, _ = parse_native_dependencies(_read_json(manifest), "ecm", str(manifest))
    recipe = next(item for item in packages if item["id"] == "resdet")["build"]
    return {
        "url": recipe["archiveUrl"],
        "sha256": recipe["archiveSha256"],
        "size": 6685870,
        "filename": "resdet.tar.gz",
    }


def build(manifest: Path, arch: str, work: Path, output: Path, cache: Path) -> dict:
    manifest, work, output = manifest.absolute(), work.absolute(), output.absolute()
    source_input(manifest)
    release = Path("/etc/alpine-release").read_text()
    validate_platform(release, platform.machine(), arch)
    validate_repositories(release, Path("/etc/apk/repositories").read_text())
    create_locations(work, output)
    build_resdet(manifest, work / "build", work / "product", cache)
    root, sources = output / "root", output / "sources"
    binary = root / "usr/local/bin/resdet"
    binary.parent.mkdir(parents=True)
    shutil.copy2(work / "product/resdet", binary)
    shutil.copytree(work / "product/licenses", root / "usr/share/doc/resdet")
    sources.mkdir()
    shutil.copy2(work / "build/resdet.tar.gz", sources / "resdet.tar.gz")
    shutil.copy2(manifest, sources / "native-dependencies.json")
    for name in ["build_alpine_resdet.py", "build_resdet.py", "generate_sbom.py"]:
        shutil.copy2(Path(__file__).parent / name, sources / name)
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(work),
        "LC_ALL": "C",
        "TZ": "UTC",
    }
    verify_elf(binary, arch, env)
    record = {
        "status": "BUILT_RESDET_ONLY",
        "architecture": arch,
        "recipe_sha256": RECIPE_SHA256,
        "owned_artifacts": owned_artifacts(root),
        "source_files": owned_artifacts(sources),
        "version_output": run([str(binary), "-V"], cwd=work, env=env),
        "configure": (work / "build/source/config.mak").read_text(),
        "compiler": run(["/usr/bin/cc", "--version"], cwd=work, env=env),
        "build_packages": run(["/sbin/apk", "info", "-v"], cwd=work, env=env),
        "scope": "Original resdet/KISS FFT source recipe, Y4M-only native build and version/link verification. Not full application/advisory approval.",
    }
    (output / "build-output.json").write_text(
        json.dumps(record, sort_keys=True, indent=2) + "\n"
    )
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--arch", choices=["amd64", "arm64"], required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    args = parser.parse_args()
    build(args.manifest, args.arch, args.work_dir, args.output_dir, args.cache_dir)


if __name__ == "__main__":
    main()
