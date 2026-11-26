#!/usr/bin/env python3
"""Build the existing owned gosu/Go contract in its pinned native Alpine stage."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
from pathlib import Path

from alpine_repository_policy import validate_repositories
from build_alpine_codecs import create_locations, validate_platform
from build_native_runtime import (
    ARCHES,
    BuildError,
    _gosu,
    _require,
    fetch_input,
    owned_artifacts,
    run,
    sha256,
    validate_go_version,
)

RECIPE_SHA256 = "a7c8145518dc921cca773823709a016556d1663e884cd6eb52db7d04548acd37"


def load_recipe(path: Path) -> dict:
    _require(
        path.is_file() and not path.is_symlink() and sha256(path) == RECIPE_SHA256,
        "reviewed Alpine gosu recipe identity mismatch",
    )
    return json.loads(path.read_text())


def build(
    manifest: Path, arch: str, work: Path, output: Path, cache: Path | None
) -> dict:
    manifest, work, output = manifest.absolute(), work.absolute(), output.absolute()
    recipe = load_recipe(manifest)
    release = Path("/etc/alpine-release").read_text()
    validate_platform(release, platform.machine(), arch)
    validate_repositories(release, Path("/etc/apk/repositories").read_text())
    create_locations(work, output)
    (work / "tmp").mkdir()
    env = {
        "PATH": "/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(work),
        "LC_ALL": "C",
        "TZ": "UTC",
        "CGO_ENABLED": "0",
        "GOOS": "linux",
        "GOARCH": arch,
        "GOTOOLCHAIN": "local",
        "GOFLAGS": "-p=2",
        "GOPATH": str(work / "go"),
        "GOCACHE": str(work / "go-cache"),
        "GOMODCACHE": str(work / "modules"),
        "GOTMPDIR": str(work / "tmp"),
        "GOPROXY": "https://proxy.golang.org,direct",
        "GOSUMDB": "sum.golang.org",
    }
    go = Path("/usr/local/go/bin/go")
    version = run([str(go), "version"], cwd=work, env=env)
    validate_go_version(version, arch)
    sources, root = output / "sources", output / "root"
    sources.mkdir()
    root.mkdir()
    fetch_input(recipe["go_source"], sources / "go1.26.10.src.tar.gz", cache)
    proof = _gosu(recipe["gosu"], work, sources, root, env, cache, arch, go_binary=go)
    proof["toolchain_origin"] = {
        "kind": "official-Alpine-Go-builder",
        "builder_image": recipe["builder_image"],
        "source": recipe["go_source"],
        "compiler_version": version,
    }
    for path in [
        manifest,
        manifest.parent / "Dockerfile.gosu",
        Path(__file__),
        Path(__file__).with_name("build_native_runtime.py"),
        Path(__file__).with_name("build_alpine_codecs.py"),
        Path(__file__).with_name("alpine_repository_policy.py"),
    ]:
        shutil.copy2(path, sources / path.name)
    record = {
        "schema_version": 1,
        "status": "BUILT_GOSU_ONLY",
        "architecture": arch,
        "recipe_sha256": RECIPE_SHA256,
        "gosu": proof,
        "owned_artifacts": owned_artifacts(root),
        "source_files": owned_artifacts(sources),
        "scope": "Owned static gosu source/toolchain only; real shipped-image privilege drop/bootstrap still required.",
    }
    (output / "build-output.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--arch", choices=sorted(ARCHES), required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path)
    args = parser.parse_args()
    try:
        build(args.manifest, args.arch, args.work_dir, args.output_dir, args.cache_dir)
    except (BuildError, OSError, ValueError) as exc:
        print(f"Alpine gosu build failed: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
