#!/usr/bin/env python3
"""Native ARM numerical investigation only; never produces an approved package."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import platform
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from run_alpine_codec_matrix import MatrixError, compare

REFERENCE_ZIP_SHA256 = (
    "1ce3dda15bf53dda829ca137089632d6c17deb3d29ebd238f9a7d20ee6e5effd"
)


def variant_source(
    source: Path, target: Path, contract: str, wrappers: str, power: str
) -> None:
    if (
        target.exists()
        or contract not in {"off", "fast"}
        or wrappers not in {"selected", "native"}
        or power not in {"pow", "expf"}
    ):
        raise MatrixError("fresh bounded diagnostic variant required")
    subtree = Path("src") if (source / "src").is_dir() else Path(".")
    cmake = (source / subtree / "CMakeLists.txt").read_text()
    definitions = (source / subtree / "defines.h").read_text()
    for text, witness in [
        (cmake, "-ffp-contract=off"),
        (cmake, "atan2f=codec2_atan2f log10f=codec2_log10f"),
        (definitions, "#define POW10F(x) ((float)pow(10.0, (double)(float)(x)))"),
    ]:
        if text.count(witness) != 1:
            raise MatrixError("diagnostic source witness mismatch")
    shutil.copytree(source, target)
    cmake = re.sub(r"-ffp-contract=(?:off|fast)", "-ffp-contract=" + contract, cmake)
    if wrappers == "native":
        cmake = cmake.replace("atan2f=codec2_atan2f log10f=codec2_log10f", "")
    if power == "expf":
        definitions = definitions.replace(
            "#define POW10F(x) ((float)pow(10.0, (double)(float)(x)))",
            "#define POW10F(x) expf(2.302585092994046f * (x))",
        )
    (target / subtree / "CMakeLists.txt").write_text(cmake)
    (target / subtree / "defines.h").write_text(definitions)


def load_reference(path: Path) -> dict:
    if hashlib.sha256(path.read_bytes()).hexdigest() != REFERENCE_ZIP_SHA256:
        raise MatrixError("original ARM execution artifact identity mismatch")
    with zipfile.ZipFile(path) as archive:
        summary = json.loads(archive.read("comparison/summary.json"))
        if (
            summary["baseline_image"]
            != "sha256:33db988ca62173209458e8df077081380e7d23b7da1188469a0899da803f1f2c"
            or summary["executions"] != 142
        ):
            raise MatrixError("original ARM execution subject mismatch")
        cases = {}
        for name in archive.namelist():
            if name.startswith("comparison/") and name.endswith(
                "-baseline-encode-decode.json"
            ):
                actual = json.loads(archive.read(name))["actual"]
                compare(actual, actual, "arm64")
                cases[actual["mode"], actual["corpus"]] = actual
    if len(cases) != 35:
        raise MatrixError("complete original ARM codec case set required")
    return cases


def collect(source: Path, probe: Path, reference_zip: Path, output: Path) -> None:
    if platform.machine() != "aarch64" or output.exists():
        raise MatrixError("fresh output on real native ARM required")
    expected = load_reference(reference_zip)
    output.mkdir(parents=True)
    all_results = []
    for contract, wrappers, power in itertools.product(
        ["off", "fast"], ["selected", "native"], ["pow", "expf"]
    ):
        label = f"{contract}-{wrappers}-{power}"
        directory = output / label
        directory.mkdir()
        variant_source(source, directory / "source", contract, wrappers, power)
        result = {
            "variant": label,
            "scope": "DIAGNOSTIC_ONLY_NOT_ACCEPTANCE",
            "failures": [],
            "cases": [],
        }
        with (directory / "build.log").open("w") as log:
            for command in [
                [
                    "cmake",
                    "-S",
                    str(directory / "source"),
                    "-B",
                    str(directory / "build"),
                    "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
                    "-DBUILD_SHARED_LIBS=ON",
                    "-DUNITTEST=FALSE",
                    "-DLPCNET=OFF",
                ],
                [
                    "cmake",
                    "--build",
                    str(directory / "build"),
                    "--target",
                    "codec2",
                    "--parallel",
                    "2",
                ],
            ]:
                subprocess.run(
                    command, stdout=log, stderr=subprocess.STDOUT, check=True
                )
        library = directory / "build/src/libcodec2.so.1.2"
        result["library_sha256"] = hashlib.sha256(library.read_bytes()).hexdigest()
        for (mode, corpus), baseline in sorted(expected.items()):
            for kind in ["encode-decode", "decode-only"]:
                command = [
                    "/usr/local/bin/python",
                    str(probe),
                    "--library",
                    str(library),
                    "--mode",
                    str(mode),
                    "--corpus",
                    corpus,
                ]
                if kind == "decode-only":
                    command += ["--decode-hex", baseline["encoded_hex"]]
                process = subprocess.run(
                    command, check=False, capture_output=True, text=True, timeout=30
                )
                row = {
                    "mode": mode,
                    "corpus": corpus,
                    "kind": kind,
                    "exit": process.returncode,
                    "stdout": process.stdout,
                    "stderr": process.stderr,
                }
                try:
                    if process.returncode:
                        raise MatrixError("diagnostic probe failed")
                    row["actual"] = json.loads(process.stdout)
                    compare(row["actual"], baseline, "arm64")
                except (MatrixError, ValueError, KeyError, TypeError) as exc:
                    row["difference"] = str(exc)
                    result["failures"].append(f"{mode}/{corpus}/{kind}: {exc}")
                result["cases"].append(row)
        (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        all_results.append(
            {
                "variant": label,
                "library_sha256": result["library_sha256"],
                "differences": len(result["failures"]),
            }
        )
        (output / "summary.json").write_text(
            json.dumps(
                {"scope": "DIAGNOSTIC_ONLY_NOT_ACCEPTANCE", "variants": all_results},
                indent=2,
            )
            + "\n"
        )
        print(json.dumps(all_results[-1]), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--reference-zip", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    collect(args.source_dir, args.probe, args.reference_zip, args.output_dir)


if __name__ == "__main__":
    main()
