#!/usr/bin/env python3
"""Run fresh native Codec2 processes against the unchanged recorded AMD64 corpus.

ARM execution/reference equality is not an independent old-ARM-baseline proof.
No tag resolution, network, new golden, normalization or numerical tolerance.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_SHA256 = "6aca56b6f3d03ca0f092116cb9b4d56a1e496a014654230322bbe36785dfeccf"
FIELDS = (
    "mode",
    "mode_name",
    "corpus",
    "frames",
    "bits_per_frame",
    "samples_per_frame",
    "input_sha256",
    "encoded_sha256",
    "encoded_hex",
    "decoded_sha256",
    "decoded_bytes",
)


class MatrixError(RuntimeError):
    """An exact real-image result or its recorded reference is incomplete."""


def reference() -> dict[tuple[int, str], dict]:
    path = ROOT / "docs/design/evidence/stable-alpine-native-amd64-20261108.json.gz"
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != REFERENCE_SHA256:
        raise MatrixError("recorded codec reference identity mismatch")
    rows = json.loads(gzip.decompress(data))["codec_matrix"]["cases"]
    result = {(r["mode"], r["corpus"]): r["baseline"] for r in rows}
    required = {
        (mode, corpus)
        for mode in [0, 1, 2, 3, 4, 5, 8]
        for corpus in ["silence", "saw", "impulse", "noise", "alternating"]
    }
    if len(rows) != 35 or set(result) != required:
        raise MatrixError("incomplete or duplicated codec reference cases")
    return result


def compare(actual: dict, expected: dict, arch: str) -> None:
    if not isinstance(actual, dict):
        raise MatrixError("native codec result must be an object")
    differences = [
        key
        for key in FIELDS
        if actual.get(key) != expected[key]
        or type(actual.get(key)) is not type(expected[key])
    ]
    if actual.get("machine") != {"amd64": "x86_64", "arm64": "aarch64"}[arch]:
        differences.append("native machine")
    if not str(actual.get("python_version", "")).startswith("3.12."):
        differences.append("Python 3.12 interpreter")
    loaded = actual.get("loaded_codec_hashes")
    digest = actual.get("library_sha256")
    if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
        differences.append("library identity")
    if not isinstance(loaded, dict) or not loaded or set(loaded.values()) != {digest}:
        differences.append("same-process loaded codec identity")
    if differences:
        raise MatrixError("exact codec comparison failed: " + ", ".join(differences))


def collect(image: str, arch: str, output: Path) -> None:
    if not re.fullmatch("sha256:[0-9a-f]{64}", image):
        raise MatrixError("full immutable local image ID required")
    if output.exists() or output.is_symlink():
        raise MatrixError("matrix output must be fresh")
    expected = reference()
    output.mkdir(parents=True)
    records, failures = [], []
    script = ROOT / "scripts/alpine_codec_matrix.py"
    for (mode, corpus), baseline in sorted(expected.items()):
        for kind in ["encode-decode", "decode-only"]:
            command = [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--user",
                "65534:65534",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--mount",
                f"type=bind,src={script},dst=/matrix/probe.py,readonly",
                "--entrypoint",
                "/usr/local/bin/python",
                image,
                "/matrix/probe.py",
                "--library",
                "/proof/output/root/usr/local/lib/libcodec2.so.1.2",
                "--mode",
                str(mode),
                "--corpus",
                corpus,
            ]
            if kind == "decode-only":
                command += ["--decode-hex", baseline["encoded_hex"]]
            record = {
                "mode": mode,
                "corpus": corpus,
                "kind": kind,
                "command": command,
            }
            try:
                result = subprocess.run(
                    command, check=False, capture_output=True, text=True, timeout=60
                )
                record.update(
                    exit=result.returncode, stdout=result.stdout, stderr=result.stderr
                )
                if result.returncode:
                    raise MatrixError(f"native process exited {result.returncode}")
                actual = json.loads(result.stdout)
                record["actual"] = actual
                compare(actual, baseline, arch)
            except subprocess.TimeoutExpired as exc:
                record.update(
                    exit=None,
                    stdout=exc.stdout.decode(errors="replace")
                    if isinstance(exc.stdout, bytes)
                    else exc.stdout or "",
                    stderr=exc.stderr.decode(errors="replace")
                    if isinstance(exc.stderr, bytes)
                    else exc.stderr or "",
                    failure="native codec process timed out",
                )
                failures.append(
                    f"{mode}/{corpus}/{kind}: native codec process timed out"
                )
            except (OSError, ValueError, TypeError, MatrixError) as exc:
                record["failure"] = str(exc)
                failures.append(f"{mode}/{corpus}/{kind}: {exc}")
            records.append(record)
            (output / f"{mode}-{corpus}-{kind}.json").write_text(
                json.dumps(record, indent=2) + "\n"
            )
    summary = {
        "image": image,
        "architecture": arch,
        "reference_sha256": REFERENCE_SHA256,
        "probe_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
        "executions": len(records),
        "failures": failures,
        "status": "FAILED" if failures else "MATCHED_RECORDED_AMD64_CORPUS",
        "scope": "Native 35-case encode/decode and 35-case baseline-stream decoding only. Not independent old-ARM-baseline/full-runtime/security proof.",
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    if failures:
        raise MatrixError("\n".join(failures))
    print(json.dumps(summary, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--arch", choices=["amd64", "arm64"], required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    collect(args.image, args.arch, args.output_dir)


if __name__ == "__main__":
    main()
