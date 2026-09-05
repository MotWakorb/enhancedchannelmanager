#!/usr/bin/env python3
"""Build the exact manifest-pinned resdet artifact used by the ECM image."""

from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_sbom import SbomError, _read_json, parse_native_dependencies, sha256_file  # noqa: E402


UPSTREAM_Y4M_LIMIT = "resdet_dims_exceed_limit(*width,*height,4,unsigned char)"
BUILD_Y4M_LIMIT = "resdet_dims_exceed_limit(*width,*height,{multiplier},unsigned char)"
CONFIGURE = (
    "./configure",
    "--disable-everything",
    "--disable-ffmpeg",
    "--omit-pgm-reader",
    "--omit-pfm-reader",
)
NOTICE_PATHS = (
    "COPYING",
    "COPYING.LGPL.txt",
    "COPYING.MIT.txt",
    "lib/kissfft/COPYING",
    "lib/kissfft/LICENSES/BSD-3-Clause",
)
ARTIFACT_ENV = {
    "CFLAGS": "-O2",
    "CPPFLAGS": "",
    "LDFLAGS": "",
    "MAKEFLAGS": "",
    "CC": "cc",
    "AR": "ar",
}
EXPECTED_VERSION_LINES = [
    "resdet version 2.4.3",
    "libresdet version 3.2.0",
    "Built with image readers: Y4M",
]


class BuildError(RuntimeError):
    """The fixed build recipe could not produce its required artifact."""


def _download(url: str, destination: Path) -> None:
    try:
        with urlopen(url) as response, destination.open("wb") as output:
            shutil.copyfileobj(response, output)
    except OSError as exc:
        raise BuildError("resdet source download failed") from exc


def _safe_extract(archive: Path, source: Path) -> None:
    staging = source.parent / ".resdet-extract"
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
            roots = set()
            for member in members:
                path = PurePosixPath(member.name)
                if (
                    path.is_absolute()
                    or not path.parts
                    or any(part in ("", ".", "..") for part in path.parts)
                    or not (member.isdir() or member.isreg())
                ):
                    raise BuildError("resdet source unsafe archive")
                roots.add(path.parts[0])
            if len(roots) != 1:
                raise BuildError("resdet source unsafe archive")
            staging.mkdir(parents=True)
            bundle.extractall(staging, filter="data")
        (staging / roots.pop()).rename(source)
        staging.rmdir()
    except (OSError, tarfile.TarError) as exc:
        raise BuildError("resdet source unsafe archive") from exc


def _patch_y4m_limit(path: Path, multiplier: int) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(UPSTREAM_Y4M_LIMIT) != 1:
        raise BuildError("resdet source must contain exactly one upstream Y4M limit")
    replacement = BUILD_Y4M_LIMIT.format(multiplier=multiplier)
    patched = text.replace(UPSTREAM_Y4M_LIMIT, replacement)
    if UPSTREAM_Y4M_LIMIT in patched or patched.count(replacement) != 1:
        raise BuildError("resdet Y4M limit patch verification failed")
    path.write_text(patched, encoding="utf-8")


def _run(command: list[str], *, cwd: Path, env: dict[str, str]):
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BuildError("resdet build command failed") from exc


def _build_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ARTIFACT_ENV and not key.startswith("PKG_CONFIG_")
    }
    environment.update(ARTIFACT_ENV)
    return environment


def _verify_artifact(binary: Path, source: Path, environment: dict[str, str]) -> None:
    if not binary.is_file():
        raise BuildError("resdet output artifact is missing")
    version = _run([str(binary), "-V"], cwd=source, env=environment).stdout
    if version.splitlines() != EXPECTED_VERSION_LINES:
        raise BuildError("resdet version or reader verification failed")
    symbols = _run(["nm", "-g", str(binary)], cwd=source, env=environment).stdout
    linkage = _run(["ldd", str(binary)], cwd=source, env=environment).stdout
    if "kiss_fft" not in symbols or "kissfft" in linkage.lower():
        raise BuildError("resdet bundled KISS FFT static linkage verification failed")


def build(manifest: Path, work_dir: Path, output_dir: Path) -> None:
    try:
        packages, _relationships = parse_native_dependencies(
            _read_json(manifest), "ecm", str(manifest)
        )
    except SbomError as exc:
        raise BuildError("native dependency manifest is invalid") from exc
    resdet = next(item for item in packages if item["id"] == "resdet")
    build_metadata = resdet["build"]
    if work_dir.exists() or output_dir.exists() or work_dir == output_dir:
        raise BuildError("resdet build locations must be new and distinct")
    work_dir.mkdir(parents=True)
    archive = work_dir / "resdet.tar.gz"
    source = work_dir / "source"

    _download(build_metadata["archiveUrl"], archive)
    if sha256_file(archive) != build_metadata["archiveSha256"]:
        raise BuildError("resdet source checksum mismatch")
    _safe_extract(archive, source)
    _patch_y4m_limit(
        source / "lib" / "image" / "y4m.c", build_metadata["y4mLimitMultiplier"]
    )

    environment = _build_environment()
    configure = [*CONFIGURE, f"--pixel-max={build_metadata['pixelMax']}"]
    _run(configure, cwd=source, env=environment)
    config_path = source / "config.mak"
    config = config_path.read_text(encoding="utf-8")
    if f"PIXEL_MAX={build_metadata['pixelMax']}" not in config:
        raise BuildError("resdet pixel ceiling configuration failed")
    for tuning in ("-march=native", "-mtune=native", "-mcpu=native"):
        config = config.replace(tuning, "")
    config_path.write_text(config, encoding="utf-8")
    if any(tuning in config_path.read_text(encoding="utf-8") for tuning in ("-march=native", "-mtune=native", "-mcpu=native")):
        raise BuildError("resdet native tuning removal failed")
    _run(["make", "resdet"], cwd=source, env=environment)

    binary = source / "resdet"
    _verify_artifact(binary, source, environment)

    output_dir.mkdir(parents=True)
    shutil.copy2(binary, output_dir / "resdet")
    notices = output_dir / "licenses"
    for relative in NOTICE_PATHS:
        destination = notices / relative.replace("lib/kissfft/", "kissfft/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, destination)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        build(args.manifest, args.work_dir, args.output_dir)
    except BuildError as exc:
        raise SystemExit(f"resdet build failed: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
