"""Compose existing Alpine delivery consumers; never grant advisory clearance.

This replaces RPM-specific delivery checks, not the image's security scans.
Further advisory remediation is deferred by the PO; malformed or incomplete
installed/source evidence still fails, including every owned-package proof.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from alpine_source_inputs import InputError, require
from alpine_source_inventory import InventoryError, source_plan
from verify_alpine_external_sources import unique, verify as verify_external
from verify_alpine_owned_sources import OWNED, verify as verify_owned
from verify_alpine_python_base import verify as verify_python_base
from verify_alpine_rust_sources import verify as verify_rust
from verify_alpine_wheel_sources import verify as verify_wheels

__all__ = ["InputError", "InventoryError", "capture_live", "verify"]


def capture_live(root: Path) -> dict:
    query = subprocess.run(
        [
            "/sbin/apk",
            "--root",
            str(root),
            "query",
            "--from",
            "installed",
            "--format",
            "json",
            "--all-matches",
            "*",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    ).stdout
    require(len(query.encode()) <= 32 * 1024**2, "bounded live APK query required")
    return {
        "packages": json.loads(query, object_pairs_hook=unique),
        "installed_database": (root / "lib/apk/db/installed").read_text(),
    }


def verify(root: Path, capture: dict, architecture: str) -> dict:
    require(
        architecture in {"amd64", "arm64"}, "supported Alpine architecture required"
    )
    machine = {"amd64": "x86_64", "arm64": "aarch64"}[architecture]
    plan = source_plan(capture, machine)
    external = verify_external(root / "usr/share/ecm/sources/apk", capture)
    owned = verify_owned(root, capture, architecture)
    expected = set(OWNED)
    pending = external["unresolved_owned_packages"]
    require(
        len(pending) == len(expected)
        and {row["name"] for row in pending} == expected
        and pending == plan["unresolved_packages"]
        and owned["source_plan"] == plan
        and external["package_set_sha256"] == plan["package_set_sha256"],
        "external and owned source consumers must bind the same complete subject",
    )
    require(
        type(external["external_packages"]) is int
        and external["external_packages"]
        + len(expected)
        + len(plan["virtual_packages"])
        == len(plan["packages"]),
        "complete external/owned/virtual APK source partition required",
    )
    python_base = verify_python_base(
        root / "usr/share/ecm/sources/python-base", architecture, root
    )
    wheels = verify_wheels(root, architecture)
    rust = verify_rust(
        root / "usr/share/ecm/sources/rust",
        root / "usr/share/ecm/sources/apk",
        capture,
        root,
    )
    return {
        "distribution": "alpine",
        "architecture": architecture,
        "installed_packages": len(plan["packages"]),
        "package_set_sha256": plan["package_set_sha256"],
        "owned_packages_verified": sorted(expected),
        "consumers": {
            "external": external,
            "owned": owned,
            "python_base": python_base,
            "wheels": wheels,
            "rust": rust,
        },
        "full_runtime_source_acceptance": False,
        "full_runtime_advisory_acceptance": False,
        "scope": "Mandatory installed query/database, signed APK/owned live-file/control-script, original source, Python base/wheel/GNU and retained Rust-input integrity checks. Not complete compiler/static-feature reconstruction or advisory clearance. Further advisory remediation is separately tracked; existing production security scans remain required.",
    }
