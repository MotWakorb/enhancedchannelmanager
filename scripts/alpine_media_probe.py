#!/usr/bin/env python3
"""Collect actual old/candidate media output; this probe makes no parity claim."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import subprocess
import tempfile
from pathlib import Path

CAPABILITIES = ("encoders", "decoders", "demuxers", "muxers", "filters", "protocols")
FIXTURES = (
    "synthetic.c2",
    "synthetic.jxl",
    "synthetic.mod",
    "synthetic.svg",
    "synthetic.vgm",
)
FIXTURE_HASHES = {
    "runtime_checks.py": "2c98dee4394f58934960723ce19811a9c398122cb308f0e883e82dde8bd20915",
    "rare_inputs.json": "7be7374b29e2c4ac43871b2fb1dff27eaa599a8ef434c2c914c53ca9981c0071",
}


def collect(fixtures: Path) -> dict:
    for name, digest in FIXTURE_HASHES.items():
        if hashlib.sha256((fixtures / name).read_bytes()).hexdigest() != digest:
            raise ValueError("original native fixture identity changed")
    spec = importlib.util.spec_from_file_location(
        "runtime_checks", fixtures / "runtime_checks.py"
    )
    if spec is None or spec.loader is None:
        raise ValueError("original native fixture loader unavailable")
    checks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checks)
    record = {
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "os_release": Path("/etc/os-release").read_text(),
        "capabilities": {},
        "frames": {},
        "commands": [],
        "input_hashes": {},
        "failures": [],
        "scope": "Actual complete outputs only; not parity, source or security approval.",
    }
    for name in CAPABILITIES:
        command = ["/usr/local/bin/ffmpeg", "-hide_banner", "-" + name]
        record["commands"].append(command)
        try:
            record["capabilities"][name] = checks.command(*command)
        except (AssertionError, OSError, subprocess.TimeoutExpired) as exc:
            record["failures"].append(name + ": " + str(exc))
    try:
        record["existing_gate"] = checks.synthetic_rare_input_decode()
    except (AssertionError, OSError, subprocess.TimeoutExpired) as exc:
        record["failures"].append("existing rare-input gate: " + str(exc))
    inputs = checks.load_rare_inputs()
    if set(inputs) != set(FIXTURES):
        raise ValueError("original native fixture set changed")
    with tempfile.TemporaryDirectory(prefix="ecm-paired-media-") as temporary:
        for name in FIXTURES:
            path = Path(temporary) / name
            path.write_bytes(inputs[name]["data"])
            record["input_hashes"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
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
            record["commands"].append(command)
            try:
                record["frames"][name] = checks.command(*command)
            except (AssertionError, OSError, subprocess.TimeoutExpired) as exc:
                record["failures"].append(name + ": " + str(exc))
    record["status"] = "FAILED" if record["failures"] else "COLLECTED_ONLY"
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures-dir", type=Path, required=True)
    args = parser.parse_args()
    record = collect(args.fixtures_dir)
    print(json.dumps(record, sort_keys=True))
    return int(bool(record["failures"]))


if __name__ == "__main__":
    raise SystemExit(main())
