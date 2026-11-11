#!/usr/bin/env python3
"""Verify delivered owned APK evidence against a live image, not full clearance.

Run trusted checkout code read-only inside the immutable candidate. Never execute
delivered recipes, import their Python, install packages or change runtime trust.
Compiler/static/language/advisory and complete runtime acceptance remain separate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import stat
import subprocess
from pathlib import Path

from alpine_apk_v2 import parts
from alpine_owned_apk_v3 import verify_owned_apk
from alpine_owned_busybox import bind_installed_record, replay_original_sources
from alpine_owned_native import (
    reviewed_package_basename,
    COMPONENTS,
    PROVENANCE,
    bind_components,
    bind_live_paths,
    verify_sources,
)
from alpine_source_inputs import InputError, require
from alpine_source_inventory import InventoryError, payload_record, source_plan
from build_native_runtime import BuildError, owned_artifacts
from install_busybox_provider import load_packages, verify_control_scripts
from verify_alpine_external_sources import read_json, sha256, unique, verify_signature

NAMESPACE = "usr/share/ecm/provenance/owned-signatures"
OWNED = {
    "busybox": "busybox",
    "busybox-binsh": "busybox",
    "ssl_client": "busybox",
    "ecm-owned-native": "ecm-owned-native",
}


def regular(path: Path, maximum: int = 512 * 1024**2) -> None:
    require(
        path.absolute() == path.resolve() and path.is_file() and not path.is_symlink(),
        "canonical regular owned evidence file required",
    )
    require(0 < path.stat().st_size <= maximum, "bounded owned evidence file required")


def read_namespace(root: Path, architecture: str) -> dict:
    """Closed transport integrity only; signatures are checked by verify()."""
    try:
        require(
            architecture in {"amd64", "arm64"}, "reviewed owned architecture required"
        )
        proof = root / NAMESPACE
        require(
            proof.is_dir() and proof.absolute() == proof.resolve(),
            "canonical owned evidence directory required",
        )
        regular(proof / "index.json", 32 * 1024**2)
        index = read_json(proof / "index.json")
        require(
            isinstance(index, dict)
            and set(index)
            == {
                "schema_version",
                "status",
                "architecture",
                "selected_packages",
                "files",
                "helper_sha256",
                "signature_verification_required",
                "full_runtime_source_acceptance",
                "scope",
            },
            "exact owned evidence index fields required",
        )
        require(
            type(index["schema_version"]) is int
            and index["schema_version"] == 1
            and type(index["selected_packages"]) is int
            and index["selected_packages"] == 4,
            "owned evidence schema/package count differs",
        )
        require(
            index["architecture"] == architecture
            and index["status"] == "SELECTED_ORIGINAL_OWNED_APK_EVIDENCE_COPIED_ONLY"
            and index["signature_verification_required"] is True
            and index["full_runtime_source_acceptance"] is False
            and isinstance(index["scope"], str)
            and bool(index["scope"]),
            "owned evidence scope/architecture differs",
        )
        records = {}
        for name in [
            "native/package-output.json",
            "busybox/build-output.json",
            "busybox/check-output.json",
        ]:
            regular(proof / name, 32 * 1024**2)
            records[name] = read_json(proof / name)
            require(isinstance(records[name], dict), "owned receipt object required")
        native, busy = (
            records["native/package-output.json"],
            records["busybox/build-output.json"],
        )
        require(
            native["architecture"] == busy["architecture"] == architecture,
            "owned receipt architecture differs",
        )
        require(
            native["public_key"]["file"] == "ecm-build.pub"
            and busy["public_key"]["file"] == "ecm-busybox.rsa.pub",
            "original public key basenames required",
        )
        reviewed_package_basename(native)
        require(
            [row["name"] for row in busy["runtime_packages"]]
            == ["busybox", "busybox-binsh", "ssl_client"],
            "all three original BusyBox packages required",
        )
        expected = {
            "index.json",
            "native/ecm-build.pub",
            "native/package-output.json",
            "native/" + native["package"]["file"],
            "busybox/ecm-busybox.rsa.pub",
            "busybox/build-output.json",
            "busybox/check-output.json",
            "busybox/check.log",
            "recipe/retain_alpine_owned_evidence.py",
        }
        bound = [
            ("native/" + native["package"]["file"], native["package"]["sha256"]),
            ("native/ecm-build.pub", native["public_key"]["sha256"]),
            ("busybox/ecm-busybox.rsa.pub", busy["public_key"]["sha256"]),
        ]
        for row in busy["runtime_packages"]:
            require(
                row["file"] == row["name"] + "-" + busy["package_version"] + ".apk"
                and Path(row["file"]).name == row["file"],
                "safe original BusyBox package basename required",
            )
            name = "busybox/runtime-packages/" + row["file"]
            expected.add(name)
            bound.append((name, row["sha256"]))
        directories = {"native", "busybox", "busybox/runtime-packages", "recipe"}
        actual_files, actual_dirs = set(), set()
        for path in proof.rglob("*"):
            require(not path.is_symlink(), "owned evidence aliases forbidden")
            name = path.relative_to(proof).as_posix()
            if path.is_dir():
                actual_dirs.add(name)
            else:
                regular(path)
                require(
                    stat.S_IMODE(path.stat().st_mode) == 0o644,
                    "owned evidence file mode differs",
                )
                actual_files.add(name)
        require(
            actual_files == expected and actual_dirs == directories,
            "closed owned evidence layout differs",
        )
        require(
            isinstance(index["files"], list)
            and len(index["files"]) == 11
            and [row for row in owned_artifacts(proof) if row["path"] != "index.json"]
            == index["files"],
            "complete owned evidence inventory differs",
        )
        helper = Path(__file__).with_name("retain_alpine_owned_evidence.py")
        require(
            index["helper_sha256"]
            == sha256(helper)
            == sha256(proof / "recipe/retain_alpine_owned_evidence.py"),
            "trusted copier identity differs",
        )
        for name, digest in bound:
            require(
                sha256(proof / name) == digest, "owned receipt file binding differs"
            )
        for name in ["native/ecm-build.pub", "busybox/ecm-busybox.rsa.pub"]:
            regular(proof / name, 16 * 1024)
            require(
                re.fullmatch(
                    rb"-----BEGIN PUBLIC KEY-----\n(?:[A-Za-z0-9+/=]+\n)+-----END PUBLIC KEY-----\n",
                    (proof / name).read_bytes(),
                )
                is not None,
                "only public PEM envelopes allowed in owned evidence",
            )
        return {"index": index, "native": native, "busybox": busy}
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RecursionError,
        BuildError,
    ) as exc:
        raise InputError("invalid owned evidence namespace: " + str(exc)) from exc


def owned_records(capture: dict, machine: str) -> dict:
    """Join the COMPLETE query/database first; preserve unresolved partition."""
    try:
        plan = source_plan(capture, machine)
        packages = {row["name"]: row for row in plan["packages"]}
        require(set(OWNED) <= packages.keys(), "missing owned installed package")
        cohort = {
            name
            for name, row in packages.items()
            if row["fields"].get("o") in set(OWNED.values())
        }
        require(cohort == set(OWNED), "unexpected owned origin cohort")
        for name, origin in OWNED.items():
            require(packages[name]["fields"].get("o") == origin, "owned origin differs")
        blocks = {}
        for block in capture["installed_database"].strip("\n").split("\n\n"):
            name = next(
                line[2:] for line in block.splitlines() if line.startswith("P:")
            )
            if name in OWNED:
                blocks[name] = block
        require(
            set(blocks) == set(OWNED), "complete raw owned installed records required"
        )
        return {"blocks": blocks, "plan": plan}
    except (InventoryError, KeyError, TypeError, ValueError, StopIteration) as exc:
        raise InputError("invalid owned installed cohort: " + str(exc)) from exc


def busybox_live_artifacts(receipt: dict) -> dict:
    """Only call after authenticating all three APK payloads/receipt inventories."""
    regulars = {
        row["path"]: row
        for package in receipt["runtime_packages"]
        for row in package["installed_payload"]
        if row["type"] == "file"
    }
    result = {}
    for package in receipt["runtime_packages"]:
        artifacts = []
        for original in package["installed_payload"]:
            if original["type"] == "directory":
                continue
            row = dict(original)
            if row["type"] == "symlink":
                require(
                    row["path"] == "bin/sh"
                    and row["target"] == "/bin/busybox"
                    and "bin/busybox" in regulars,
                    "reviewed separately signed BusyBox link referent required",
                )
                row["target_sha256"] = regulars["bin/busybox"]["sha256"]
            artifacts.append(row)
        result[package["name"]] = artifacts
    return result


def verify(root: Path, capture: dict, architecture: str) -> dict:
    """Compose all four original APK signatures/DB/live/control/source joins."""
    namespace = read_namespace(root, architecture)
    machine = {"amd64": "x86_64", "arm64": "aarch64"}[architecture]
    cohort = owned_records(capture, machine)
    blocks = cohort["blocks"]
    proof = root / NAMESPACE
    trust_paths = [root / "etc/apk/keys", root / "lib/apk/keys"]
    trust = {str(path): owned_artifacts(path) for path in trust_paths}
    repositories = (root / "etc/apk/repositories").read_bytes()
    native_record = namespace["native"]
    parsed = payload_record(blocks["ecm-owned-native"], machine)
    native_package = proof / "native" / native_record["package"]["file"]
    native = verify_owned_apk(
        native_package,
        proof / "native/ecm-build.pub",
        native_record,
        parsed["record"]["fields"],
        architecture,
        database_record=blocks["ecm-owned-native"],
    )
    native_live = bind_live_paths(
        root, native["installed_artifacts"], parsed["directories"]
    )
    raw_receipts = {}
    for name in COMPONENTS:
        path = root / PROVENANCE / (name + ".json")
        regular(path, 32 * 1024**2)
        raw_receipts[name] = path.read_bytes()
    components = bind_components(native_record, raw_receipts, architecture)
    native_sources = verify_sources(root, components["components"])
    source = root / "usr/share/ecm/sources/busybox"
    busy = load_packages(
        proof / "busybox",
        architecture,
        source / "owned-recipe/manifest.json",
        source_root=source,
    )
    require(
        busy == namespace["busybox"],
        "original BusyBox receipt changed during validation",
    )
    corresponding_sources = replay_original_sources(
        source,
        corresponding_plan=root
        / "usr/share/ecm/provenance/busybox-source-replay/source-replay.json",
    )
    packages, all_packages, expected_scripts = [], [native_package], {}
    # Authenticate EVERY payload before deriving cross-package link referents.
    for expected in busy["runtime_packages"]:
        package = proof / "busybox/runtime-packages" / expected["file"]
        verify_signature(package, proof / "busybox")
        bound = bind_installed_record(
            package, blocks[expected["name"]], expected, architecture
        )
        all_packages.append(package)
        packages.append(
            {"name": expected["name"], "signature_verified": True, "database": bound}
        )
        control_digest = hashlib.sha1(parts(package)[1]).hexdigest()
        for script in expected["control_scripts"]:
            name = (
                expected["name"]
                + "-"
                + busy["package_version"]
                + ".X1"
                + control_digest
                + script["path"]
            )
            require(name not in expected_scripts, "duplicate BusyBox control script")
            expected_scripts[name] = script
    live_artifacts = busybox_live_artifacts(busy)
    for package in packages:
        parsed = payload_record(blocks[package["name"]], machine)
        package["live"] = bind_live_paths(
            root, live_artifacts[package["name"]], parsed["directories"]
        )
    scripts_path = root / "lib/apk/db/scripts.tar.gz"
    regular(scripts_path, 32 * 1024**2)
    scripts = verify_control_scripts(scripts_path, expected_scripts)
    denials = []
    for package in all_packages:
        result = subprocess.run(
            ["/sbin/apk", "verify", "--network=no", str(package)],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        require(
            result.returncode != 0
            and "UNTRUSTED signature" in result.stdout + result.stderr,
            "owned APK unexpectedly trusted by default runtime keys",
        )
        denials.append(
            {
                "package": package.name,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
    require(
        trust == {str(path): owned_artifacts(path) for path in trust_paths}
        and (root / "etc/apk/repositories").read_bytes() == repositories,
        "owned verification changed runtime trust/repositories",
    )
    # Recheck transport after subprocesses, not just before authentication.
    require(
        read_namespace(root, architecture) == namespace,
        "owned public evidence changed during verification",
    )
    return {
        "status": "OWNED_NATIVE_SIGNATURE_DATABASE_LIVE_CONTROL_AND_ORIGINAL_SOURCE_JOINS_ONLY",
        "architecture": architecture,
        "namespace": namespace["index"],
        "native": native,
        "native_live": native_live,
        "native_component_sources": native_sources,
        "busybox_packages": packages,
        "busybox_corresponding_sources": corresponding_sources,
        "busybox_original_check_pass_lines": busy["upstream_pass_lines"],
        "installed_busybox_control_scripts": scripts,
        "default_runtime_trust_denials": denials,
        "runtime_keys_and_repositories_unchanged": True,
        "source_plan": cohort["plan"],
        "full_runtime_source_acceptance": False,
        "historical_compiler_linkage_established": False,
        "scope": "Four original delivered APKs independently verified with separate public evidence and full actual installed records, live bytes/raw links/identities/modes, six native component sources, original BusyBox sources/static/check records and installed control scripts. The separately sealed source-only corresponding patch plan replays full patched source bytes in memory; it is not a builder patch substitution or proof of compiled features. No package installation or delivered code execution; historical logs are not reproduced tests. Compiler/language/static features, GNU wheels, frontend/MCP and independent unsuppressed advisories/aggregate acceptance remain separate. The complete source plan and unresolved partition are preserved, not waived.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=("amd64", "arm64"), required=True)
    options = parser.parse_args()
    try:
        require(
            platform.machine() == {"amd64": "x86_64", "arm64": "aarch64"}[options.arch],
            "native owned verifier architecture differs",
        )
        result = subprocess.run(
            [
                "/sbin/apk",
                "query",
                "--from",
                "installed",
                "--format",
                "json",
                "--all-matches",
                "*",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        require(
            len(result.stdout.encode()) <= 32 * 1024**2,
            "bounded installed query required",
        )
        capture = {
            "packages": json.loads(result.stdout, object_pairs_hook=unique),
            "installed_database": Path("/lib/apk/db/installed").read_text(),
        }
        report = verify(Path("/"), capture, options.arch)
    except (
        InputError,
        BuildError,
        InventoryError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as exc:
        parser.exit(1, "owned source verification failed: " + str(exc) + "\n")
    print(json.dumps(report, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
