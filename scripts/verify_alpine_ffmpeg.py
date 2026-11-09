#!/usr/bin/env python3
"""Check real installed FFmpeg outputs against the unchanged recorded corpus.

This supplements, never replaces, the existing native-image/application gates.
ARM uses independently executed original ARM outputs, not an AMD64 proxy.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_SHA256 = "1c549087934e4206bc343152c6e9a48d04d7199ec1f75e9da3d519611131d29f"
ARM_REFERENCE_SHA256 = (
    "1f78673968199bf91cbf3ad06ddcea30a9babef19669606bd5e1e5b3d190dda3"
)
FIXTURE_HASHES = {
    "runtime_checks.py": "2c98dee4394f58934960723ce19811a9c398122cb308f0e883e82dde8bd20915",
    "rare_inputs.json": "7be7374b29e2c4ac43871b2fb1dff27eaa599a8ef434c2c914c53ca9981c0071",
}


class VerificationError(RuntimeError):
    """A bounded real-media or capability output differs from its reference."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_reference(arch: str = "amd64") -> dict:
    if arch == "arm64":
        path = (
            ROOT
            / "docs/design/evidence/stable-alpine-native-original-arm64-20261109.json.gz"
        )
        data = path.read_bytes()
        if digest(data) != ARM_REFERENCE_SHA256:
            raise VerificationError("original ARM FFmpeg reference identity mismatch")
        media = json.loads(gzip.decompress(data))["media"]
        return {
            "scope": "Original independently executed ARM64 bounded FFmpeg corpus; not application/source/security approval.",
            "capabilities": {
                name: digest(value.encode())
                for name, value in media["capabilities"].items()
            },
            "frames": {
                name: digest(value.encode()) for name, value in media["frames"].items()
            },
            "metadata_sha256": digest(
                json.dumps(
                    media["existing_gate"], sort_keys=True, separators=(",", ":")
                ).encode()
            ),
        }
    if arch != "amd64":
        raise VerificationError("unsupported native FFmpeg architecture")
    path = ROOT / "docker/alpine-native/ffmpeg-baseline.json"
    data = path.read_bytes()
    if digest(data) != REFERENCE_SHA256:
        raise VerificationError("FFmpeg reference identity mismatch")
    return json.loads(data)


def validate_hashes(actual: dict, expected: dict, label: str) -> None:
    if actual != expected:
        raise VerificationError("exact FFmpeg " + label + " mismatch")


def verify(fixtures: Path, output: Path) -> None:
    if output.exists() or output.is_symlink():
        raise VerificationError("FFmpeg evidence must be fresh")
    arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine())
    reference = load_reference(arch)
    for name, expected in FIXTURE_HASHES.items():
        if digest((fixtures / name).read_bytes()) != expected:
            raise VerificationError("existing FFmpeg fixture/check identity changed")
    for name in ["ffmpeg", "ffprobe"]:
        if shutil.which(name) != "/usr/local/bin/" + name:
            raise VerificationError(
                "actual installed owned FFmpeg executables required"
            )
    spec = importlib.util.spec_from_file_location(
        "runtime_checks", fixtures / "runtime_checks.py"
    )
    if spec is None or spec.loader is None:
        raise VerificationError("existing native checks unavailable")
    checks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checks)
    record = {
        "scope": reference["scope"],
        "reference_sha256": ARM_REFERENCE_SHA256
        if arch == "arm64"
        else REFERENCE_SHA256,
        "machine": platform.machine(),
        "capabilities": {},
        "frames": {},
        "failures": [],
    }
    capabilities, frames = {}, {}
    try:
        for name in reference["capabilities"]:
            stdout = checks.command("/usr/local/bin/ffmpeg", "-hide_banner", "-" + name)
            capabilities[name] = digest(stdout.encode())
            record["capabilities"][name] = {
                "stdout": stdout,
                "sha256": capabilities[name],
            }
        validate_hashes(capabilities, reference["capabilities"], "capability listings")
        metadata = checks.synthetic_rare_input_decode()
        record["existing_gate"] = metadata
        metadata_sha = digest(
            json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
        )
        if metadata_sha != reference["metadata_sha256"]:
            raise VerificationError("existing real FFmpeg metadata/count gate differs")
        with tempfile.TemporaryDirectory(
            prefix="ecm-owned-ffmpeg-fixtures-"
        ) as temporary:
            for name, fixture in checks.load_rare_inputs().items():
                path = Path(temporary) / name
                path.write_bytes(fixture["data"])
                command = [
                    "/usr/local/bin/ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(path),
                    "-t",
                    "0.2",
                    "-f",
                    "framemd5",
                    "-",
                ]
                stdout = checks.command(*command)
                frames[name] = digest(stdout.encode())
                record["frames"][name] = {
                    "command": command,
                    "framemd5": stdout,
                    "sha256": frames[name],
                }
        validate_hashes(frames, reference["frames"], "complete decoded frames")
    except (
        AssertionError,
        VerificationError,
        OSError,
        subprocess.TimeoutExpired,
    ) as exc:
        record["failures"].append(str(exc))
    record["status"] = (
        "FAILED"
        if record["failures"]
        else (
            "MATCHED_RECORDED_ORIGINAL_ARM64_FFMPEG_CORPUS"
            if arch == "arm64"
            else "MATCHED_RECORDED_AMD64_FFMPEG_CORPUS"
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2) + "\n")
    if record["failures"]:
        raise VerificationError("; ".join(record["failures"]))
    print(record["status"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures-dir", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    args = parser.parse_args()
    verify(args.fixtures_dir, args.output_file)


if __name__ == "__main__":
    main()
