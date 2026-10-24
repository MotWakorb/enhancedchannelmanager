#!/usr/bin/env python3
"""Offline compatibility gate for an already-built, full sha256 Docker image ID.

No builds, tag resolution, pulls, published ports, provider data, or host config.
This is a behavioral gate, NOT a vulnerability scan or multi-architecture proof.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "tests/native_image"
_IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}")
_CHECK_NAME_RE = re.compile(r"[a-z0-9_]+")


class GateError(RuntimeError):
    """A required real-image check did not succeed."""


def decode_json(text: str):
    try:
        return json.loads(text)
    except (ValueError, TypeError) as exc:
        raise GateError("missing or malformed JSON result") from exc


class DockerGate:
    def __init__(self, image: str, docker: str, startup_timeout: float):
        if _IMAGE_ID_RE.fullmatch(image) is None:
            raise GateError(
                "--image must be a full immutable sha256 image ID, not a tag"
            )
        if not 0 < startup_timeout <= 600:
            raise GateError("--startup-timeout must be between 0 and 600 seconds")
        self.image = image
        self.docker = docker
        self.startup_timeout = startup_timeout
        self.prefix = f"ecm-native-gate-{uuid.uuid4().hex}"
        self.containers: list[str] = []
        self.volumes: list[str] = []
        self.runtime_report: dict | None = None
        self.healthcheck = ""

    def command(self, *arguments: str, timeout: float = 60) -> str:
        try:
            result = subprocess.run(
                [self.docker, *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GateError(f"Docker {arguments[0]} unavailable or timed out") from exc
        if "/native-image/runtime_checks.py" in arguments:
            try:
                report = json.loads(result.stdout)
                if isinstance(report, dict):
                    # A decoder's PASS is not trusted until exit status and the
                    # complete check contract have both been verified.
                    self.runtime_report = {**report, "status": "FAIL"}
            except ValueError:
                pass
        if result.returncode:
            # Never print app logs, config, exec output, or generated credentials.
            # Runtime diagnostics contain only our synthetic fixture failures.
            detail = ""
            if "/native-image/runtime_checks.py" in arguments:
                try:
                    failures = json.loads(result.stdout).get("failures", {})
                    names = [
                        name for name in failures if _CHECK_NAME_RE.fullmatch(name)
                    ]
                    detail = ": " + ", ".join(names) if names else ""
                except (ValueError, AttributeError, TypeError):
                    pass
            raise GateError(f"Docker {arguments[0]} exited {result.returncode}{detail}")
        return result.stdout

    def inspect_image(self) -> dict:
        data = decode_json(self.command("image", "inspect", self.image))
        if (
            not isinstance(data, list)
            or len(data) != 1
            or not isinstance(data[0], dict)
        ):
            raise GateError("invalid image inspection result")
        image = data[0]
        if image.get("Id") != self.image:
            raise GateError(
                "inspected image identity differs from the requested image ID"
            )
        config = image.get("Config") or {}
        if config.get("Entrypoint") != ["/app/entrypoint.sh"]:
            raise GateError("image does not use the real ECM entrypoint")
        healthcheck = (config.get("Healthcheck") or {}).get("Test", [])
        if (
            len(healthcheck) != 2
            or healthcheck[0] != "CMD-SHELL"
            or not healthcheck[1].startswith("/opt/venv/bin/python ")
        ):
            raise GateError(
                "image healthcheck must invoke the absolute venv interpreter"
            )
        self.healthcheck = healthcheck[1]
        return image

    def run_options(self, name: str) -> list[str]:
        return [
            "--name",
            name,
            "--pull",
            "never",
            "--network",
            "none",
            "--mount",
            f"type=bind,source={FIXTURES},target=/native-image,readonly",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
        ]

    def runtime(self) -> dict:
        name = f"{self.prefix}-runtime"
        self.containers.append(name)
        report = decode_json(
            self.command(
                "run",
                *self.run_options(name),
                "--entrypoint",
                "/opt/venv/bin/python",
                self.image,
                "-I",
                "/native-image/runtime_checks.py",
                timeout=240,
            )
        )
        expected = decode_json((FIXTURES / "checks.json").read_text())
        if (
            not isinstance(report, dict)
            or report.get("status") != "PASS"
            or report.get("checks") != expected
            or not isinstance(report.get("metadata"), dict)
            or report.get("failures") != {}
        ):
            raise GateError("runtime checks did not return a complete PASS result")
        self.runtime_report = report
        return report

    def start(self, name: str, volume: str, uid: int, gid: int) -> None:
        self.containers.append(name)
        self.command(
            "run",
            "--detach",
            *self.run_options(name),
            "--mount",
            f"type=volume,source={volume},target=/config",
            "--env",
            f"PUID={uid}",
            "--env",
            f"PGID={gid}",
            self.image,
        )

    def probe(self, name: str, uid: int, gid: int, phase: str) -> dict:
        deadline = time.monotonic() + self.startup_timeout
        while True:
            container = decode_json(self.command("container", "inspect", name))
            if (
                not isinstance(container, list)
                or len(container) != 1
                or container[0].get("Image") != self.image
                or container[0].get("State", {}).get("Running") is not True
            ):
                raise GateError(
                    "bootstrap container exited or has the wrong image identity"
                )
            try:
                self.command(
                    "exec",
                    "--user",
                    f"{uid}:{gid}",
                    name,
                    "/bin/sh",
                    "-c",
                    self.healthcheck,
                    timeout=15,
                )
                report = decode_json(
                    self.command(
                        "exec",
                        "--user",
                        f"{uid}:{gid}",
                        name,
                        "/opt/venv/bin/python",
                        "-I",
                        "/native-image/bootstrap_checks.py",
                        "--uid",
                        str(uid),
                        "--gid",
                        str(gid),
                        "--phase",
                        phase,
                        "--marker",
                        self.prefix,
                        timeout=15,
                    )
                )
                if (
                    not isinstance(report, dict)
                    or report.get("status") != "PASS"
                    or report.get("uid") != uid
                    or report.get("gid") != gid
                    or report.get("phase") != phase
                ):
                    raise GateError(
                        "bootstrap returned an incomplete result or wrong runtime UID/GID"
                    )
                return report
            except GateError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(min(1, self.startup_timeout))

    def bootstrap(self, uid: int, gid: int) -> dict:
        volume = f"{self.prefix}-{uid}-config"
        # The daemon may create it before the CLI errors or times out.
        self.volumes.append(volume)
        self.command("volume", "create", volume)
        first = f"{self.prefix}-{uid}-first"
        self.start(first, volume, uid, gid)
        report = self.probe(first, uid, gid, "create")
        self.command("restart", "--time", "10", first, timeout=90)
        self.probe(first, uid, gid, "verify")
        self.command("rm", "--force", first)
        self.containers.remove(first)
        second = f"{self.prefix}-{uid}-recreated"
        self.start(second, volume, uid, gid)
        self.probe(second, uid, gid, "verify")
        return {**report, "restart_persistence": True, "recreation_persistence": True}

    def cleanup(self) -> None:
        errors = []
        for name in reversed(self.containers):
            try:
                self.command("rm", "--force", name)
            except GateError as exc:
                errors.append(str(exc))
        for volume in reversed(self.volumes):
            try:
                self.command("volume", "rm", volume)
            except GateError as exc:
                errors.append(str(exc))
        if errors:
            raise GateError("owned-resource cleanup failed: " + "; ".join(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image", required=True, help="already-built full sha256 image ID"
    )
    parser.add_argument("--docker", default="docker", help="Docker CLI executable")
    parser.add_argument("--startup-timeout", type=float, default=120)
    parser.add_argument(
        "--phase", choices=("all", "runtime", "bootstrap"), default="all"
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="write JSON evidence, including runtime diagnostics on failure",
    )
    args = parser.parse_args(argv)
    gate = None
    started = time.monotonic()
    report = {"status": "FAIL", "phase": args.phase, "image_id": args.image}
    try:
        gate = DockerGate(args.image, args.docker, args.startup_timeout)
        image = gate.inspect_image()
        report.update(
            {
                "architecture": image.get("Architecture"),
                "os": image.get("Os"),
                "security_scope": "Compatibility only; requires a separate whole-image vulnerability scan.",
            }
        )
        try:
            if args.phase in {"all", "bootstrap"}:
                report["bootstrap"] = [
                    gate.bootstrap(uid, gid)
                    for uid, gid in ((1000, 1000), (1571, 1572))
                ]
            if args.phase in {"all", "runtime"}:
                report["runtime"] = gate.runtime()
        finally:
            gate.cleanup()
        report["status"] = "PASS"
        report["duration_seconds"] = round(time.monotonic() - started, 3)
        if args.report:
            args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
        return 0
    except (GateError, OSError) as exc:
        if args.report:
            failed = {
                **report,
                "status": "FAIL",
                "error": str(exc),
                "runtime": gate.runtime_report if gate else None,
                "duration_seconds": round(time.monotonic() - started, 3),
            }
            try:
                args.report.write_text(json.dumps(failed, indent=2) + "\n")
            except OSError:
                print(
                    "native image gate failed: unable to write evidence report",
                    file=sys.stderr,
                )
        print(f"native image gate failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
