#!/usr/bin/env python3
"""Verify retained original image and native package artifacts without execution."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import tarfile
import zipfile
from pathlib import Path

from candidate_image import runtime_config
from compare_alpine_baseline import MatrixError, load_baselines


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def checked_zip(path: Path, expected: str) -> zipfile.ZipFile:
    if path.is_symlink() or sha256(path) != expected:
        raise MatrixError("immutable baseline/native artifact identity mismatch")
    archive = zipfile.ZipFile(path)
    if len(archive.namelist()) != len(set(archive.namelist())):
        archive.close()
        raise MatrixError("duplicate artifact archive member")
    return archive


def extract_image(path: Path, expected: str, filename: str, output: Path) -> None:
    with checked_zip(path, expected) as archive:
        if archive.namelist() != [filename]:
            raise MatrixError("unexpected original image archive member")
        if output.exists() or output.is_symlink():
            raise MatrixError("original image output must be fresh")
        with archive.open(filename) as source, output.open("xb") as target:
            shutil.copyfileobj(source, target)


def package_members(tar: tarfile.TarFile) -> dict:
    result = {}
    for member in tar:
        name = member.name.removeprefix("./")
        if (
            name == "source-commit.txt"
            or name.startswith("packages/")
            and not member.isdir()
        ):
            if name in result or not member.isreg() or ".." in Path(name).parts:
                raise MatrixError("unsafe/duplicate native package archive member")
            result[name] = member
    return result


def prepare(
    arch: str, baseline_zip: Path, native_zip: Path | None, output: Path
) -> dict:
    if output.exists() or output.is_symlink():
        raise MatrixError("paired baseline output must be fresh")
    if any(
        path.resolve().is_relative_to(output.resolve())
        or output.resolve().is_relative_to(path.resolve())
        for path in [baseline_zip, native_zip]
        if path is not None
    ):
        raise MatrixError("paired input/output locations must be disjoint")
    descriptor = load_baselines()
    row = descriptor["architectures"][arch]
    output.mkdir(parents=True)
    original = row["image_artifact"]
    image = output / "original.oci.tar"
    extract_image(baseline_zip, original["sha256"], original["filename"], image)
    identity = runtime_config(image, row["identity"]["ManifestDigest"], arch)
    if identity != row["identity"]:
        raise MatrixError(
            "original OCI runtime identity differs from native baseline receipt"
        )
    if native_zip is None:
        result = {
            "status": "VERIFIED_ORIGINAL_IMAGE_ONLY",
            "architecture": arch,
            "original_identity": identity,
            "original_artifact_sha256": original["sha256"],
            "scope": "Verified original image only. No historical native package substituted for the current build.",
        }
        (output / "original-identity.json").write_text(
            json.dumps(identity, indent=2) + "\n"
        )
        (output / "input-receipt.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    package_descriptor = row["native_package_artifact"]
    packages = output / "packages"
    packages.mkdir()
    with checked_zip(native_zip, package_descriptor["sha256"]) as archive:
        if archive.namelist() != [package_descriptor["filename"]]:
            raise MatrixError("unexpected native evidence archive member")
        with tarfile.open(
            fileobj=io.BytesIO(archive.read(package_descriptor["filename"])),
            mode="r:gz",
        ) as tar:
            members = package_members(tar)
            source = tar.extractfile(members["source-commit.txt"])
            if (
                source is None
                or source.read().decode().strip() != package_descriptor["source_commit"]
            ):
                raise MatrixError("native package source commit mismatch")
            source = tar.extractfile(members["packages/package-output.json"])
            if source is None:
                raise MatrixError("native package receipt missing")
            manifest = json.load(source)
            if (
                manifest["architecture"] != arch
                or manifest["status"] != "SIGNED_OWNED_APK_ONLY"
            ):
                raise MatrixError("native signed package status/architecture mismatch")
            names = {
                manifest["package"]["file"],
                manifest["public_key"]["file"],
                "package-output.json",
                "install-output.json",
                "ffmpeg-behavior.json",
            }
            if {
                name.removeprefix("packages/")
                for name in members
                if name.startswith("packages/")
            } != names:
                raise MatrixError("native package artifact inventory mismatch")
            for name in sorted(names):
                if Path(name).name != name:
                    raise MatrixError("native package filename must be a basename")
                source = tar.extractfile(members["packages/" + name])
                if source is None:
                    raise MatrixError("native package member unreadable")
                with (packages / name).open("xb") as target:
                    shutil.copyfileobj(source, target)
    for item in [manifest["package"], manifest["public_key"]]:
        if sha256(packages / item["file"]) != item["sha256"]:
            raise MatrixError("native package/key identity mismatch")
    installation = json.loads((packages / "install-output.json").read_text())
    media = json.loads((packages / "ffmpeg-behavior.json").read_text())
    if (
        installation["status"] != "SIGNED_APK_INSTALLED_ONLY"
        or media["status"] != "MATCHED_RECORDED_AMD64_FFMPEG_CORPUS"
    ):
        raise MatrixError("native package prior verification is incomplete")
    (output / "original-identity.json").write_text(
        json.dumps(identity, indent=2) + "\n"
    )
    result = {
        "status": "VERIFIED_IMMUTABLE_INPUTS_ONLY",
        "architecture": arch,
        "original_identity": identity,
        "original_artifact_sha256": original["sha256"],
        "native_artifact_sha256": package_descriptor["sha256"],
        "source_equivalence": {
            "application_source": descriptor["application_source"],
            "image_source": descriptor["image_source"],
            "paths": descriptor["source_equivalence_paths"],
        },
        "package": manifest["package"],
        "scope": "Artifact/OCI identity preparation only; no native execution or paired equivalence claim.",
    }
    (output / "input-receipt.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", required=True, choices=["amd64", "arm64"])
    parser.add_argument("--baseline-zip", type=Path, required=True)
    parser.add_argument(
        "--native-zip",
        type=Path,
        help="Optional historical-package diagnostic; omit when testing the current native build",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare(
            args.arch,
            args.baseline_zip.absolute(),
            args.native_zip.absolute() if args.native_zip is not None else None,
            args.output_dir.absolute(),
        )
    except (
        MatrixError,
        OSError,
        ValueError,
        KeyError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as exc:
        print("Baseline input preparation failed: " + str(exc), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
