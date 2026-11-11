#!/usr/bin/env python3
"""Compare actual original and Alpine images on the same native architecture.

Collect independently executed old-ARM results instead of treating an AMD64
recording as an ARM baseline. Original recordings remain unchanged extra gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

from alpine_media_probe import CAPABILITIES, FIXTURES
from run_alpine_codec_matrix import MatrixError, compare, reference

ROOT = Path(__file__).resolve().parents[1]
BASELINES_SHA256 = "a2c5e9971c28afb31c9df3ec1e4ea187e4b67279dbc9d84d660d19849d65476c"


def load_baselines() -> dict:
    path = ROOT / "docker/alpine-native/original-baselines.json"
    data = path.read_bytes()
    if path.is_symlink() or hashlib.sha256(data).hexdigest() != BASELINES_SHA256:
        raise MatrixError("reviewed original baseline descriptor identity mismatch")
    return json.loads(data)


def compare_media(actual: dict, expected: dict, arch: str) -> None:
    for side in [actual, expected]:
        if not isinstance(side, dict) or side.get("status") != "COLLECTED_ONLY":
            raise MatrixError("actual collected media result required")
        if side.get("machine") != {"amd64": "x86_64", "arm64": "aarch64"}[arch]:
            raise MatrixError("native media architecture mismatch")
        if not str(side.get("python_version", "")).startswith("3.12."):
            raise MatrixError("media Python 3.12 interpreter required")
        if set(side.get("capabilities", {})) != set(CAPABILITIES) or set(
            side.get("frames", {})
        ) != set(FIXTURES):
            raise MatrixError("complete media outputs required")
        if side.get("failures"):
            raise MatrixError("media probe failed")
        if not all(
            isinstance(value, str) and value
            for group in ["frames", "capabilities"]
            for value in side[group].values()
        ):
            raise MatrixError("empty media output cannot establish parity")
        inputs = side.get("input_hashes", {})
        if set(inputs) != set(FIXTURES) or not all(
            isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value)
            for value in inputs.values()
        ):
            raise MatrixError("complete media input identities required")
        if set(
            side.get("existing_gate", {}).get("synthetic_rare_input_decode", {})
        ) != set(FIXTURES):
            raise MatrixError("complete existing media metadata gate required")
    for name in ["capabilities", "frames", "existing_gate", "input_hashes"]:
        if actual.get(name) != expected.get(name):
            raise MatrixError("exact original/native candidate media mismatch: " + name)


def command(image: str, side: str, script: Path) -> list[str]:
    return [
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
        "--tmpfs",
        "/tmp:rw,mode=1777",
        "--mount",
        f"type=bind,src={script},dst=/matrix/probe.py,readonly",
        "--entrypoint",
        "/opt/venv/bin/python" if side == "baseline" else "/usr/local/bin/python",
        image,
        "/matrix/probe.py",
    ]


def collect(baseline: str, candidate: str, arch: str, output: Path) -> None:
    if baseline == candidate:
        raise MatrixError("original and candidate must be distinct image subjects")
    if baseline != load_baselines()["architectures"][arch]["identity"]["ConfigDigest"]:
        raise MatrixError("original baseline identity mismatch")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", candidate):
        raise MatrixError("full immutable candidate image ID required")
    if output.exists() or output.is_symlink():
        raise MatrixError("paired output must be fresh")
    cases = reference()
    output.mkdir(parents=True)
    records, failures = [], []

    def invoke(args: list[str], label: str) -> dict | None:
        record = {"command": args, "label": label}
        try:
            result = subprocess.run(
                args, check=False, capture_output=True, text=True, timeout=90
            )
            record.update(
                exit=result.returncode, stdout=result.stdout, stderr=result.stderr
            )
            if result.returncode:
                raise MatrixError(f"process exited {result.returncode}")
            actual = json.loads(result.stdout)
            if not isinstance(actual, dict):
                raise MatrixError("native probe result must be an object")
            record["actual"] = actual
        except subprocess.TimeoutExpired as exc:
            record.update(
                exit=None,
                stdout=exc.stdout.decode(errors="replace")
                if isinstance(exc.stdout, bytes)
                else exc.stdout or "",
                stderr=exc.stderr.decode(errors="replace")
                if isinstance(exc.stderr, bytes)
                else exc.stderr or "",
                failure="native process timed out",
            )
            failures.append(label + ": native process timed out")
        except (OSError, ValueError, MatrixError) as exc:
            record["failure"] = str(exc)
            failures.append(label + ": " + str(exc))
        records.append(record)
        (output / (label + ".json")).write_text(json.dumps(record, indent=2) + "\n")
        return record.get("actual")

    images = {"baseline": baseline, "candidate": candidate}
    libraries = {
        "baseline": "/usr/lib64/libcodec2.so.1.2",
        "candidate": "/usr/local/lib/libcodec2.so.1.2",
    }
    for mode, corpus in sorted(cases):
        results = {}
        for side, image in images.items():
            args = command(image, side, ROOT / "scripts/alpine_codec_matrix.py")
            args += [
                "--library",
                libraries[side],
                "--mode",
                str(mode),
                "--corpus",
                corpus,
            ]
            results[side] = invoke(args, f"{mode}-{corpus}-{side}-encode-decode")
        old = results["baseline"]
        if old is None:
            continue
        try:
            compare(old, old, arch)
            compare(results["candidate"], old, arch)
        except (MatrixError, KeyError, TypeError) as exc:
            failures.append(f"{mode}/{corpus}/encode-decode: {exc}")
        if not isinstance(old.get("encoded_hex"), str):
            failures.append(f"{mode}/{corpus}: original encoded stream unavailable")
            continue
        decoded = {}
        for side, image in images.items():
            args = command(image, side, ROOT / "scripts/alpine_codec_matrix.py")
            args += [
                "--library",
                libraries[side],
                "--mode",
                str(mode),
                "--corpus",
                corpus,
                "--decode-hex",
                old["encoded_hex"],
            ]
            decoded[side] = invoke(args, f"{mode}-{corpus}-{side}-decode-only")
        try:
            compare(decoded["baseline"], old, arch)
            compare(decoded["candidate"], decoded["baseline"], arch)
        except (MatrixError, KeyError, TypeError) as exc:
            failures.append(f"{mode}/{corpus}/original-stream-decode: {exc}")
    media = {}
    for side, image in images.items():
        args = command(image, side, ROOT / "scripts/alpine_media_probe.py")
        # Insert the extra read-only fixture mount before the container entrypoint.
        index = args.index("--entrypoint")
        args[index:index] = [
            "--mount",
            f"type=bind,src={ROOT}/tests/native_image,dst=/matrix/fixtures,readonly",
        ]
        media[side] = invoke(
            args + ["--fixtures-dir", "/matrix/fixtures"], side + "-media"
        )
    try:
        if not isinstance(media["baseline"], dict) or not isinstance(
            media["candidate"], dict
        ):
            raise MatrixError("both actual media results required")
        compare_media(media["candidate"], media["baseline"], arch)
        if "ID=alpine\n" not in media["candidate"]["os_release"]:
            raise MatrixError("candidate must be actual Alpine")
        if (
            'ID="opensuse-tumbleweed"' not in media["baseline"]["os_release"]
            and "ID=opensuse-tumbleweed\n" not in media["baseline"]["os_release"]
        ):
            raise MatrixError("original image platform mismatch")
    except (MatrixError, KeyError, TypeError) as exc:
        failures.append(str(exc))
    summary = {
        "status": "FAILED" if failures else "EXACT_NATIVE_ORIGINAL_BASELINE_MATCH",
        "architecture": arch,
        "baseline_image": baseline,
        "candidate_image": candidate,
        "baseline_descriptor_sha256": BASELINES_SHA256,
        "executions": len(records),
        "codec_case_count": len(cases),
        "failures": failures,
        "probe_sha256": {
            name: hashlib.sha256((ROOT / "scripts" / name).read_bytes()).hexdigest()
            for name in ["alpine_codec_matrix.py", "alpine_media_probe.py"]
        },
        "scope": "Actual same-native-architecture old-image/candidate bounded codec and complete media comparison. Not full application/source/security approval.",
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    if failures:
        raise MatrixError("\n".join(failures))
    print(json.dumps(summary, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-image", required=True)
    parser.add_argument("--candidate-image", required=True)
    parser.add_argument("--arch", required=True, choices=["amd64", "arm64"])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    collect(args.baseline_image, args.candidate_image, args.arch, args.output_dir)


if __name__ == "__main__":
    main()
