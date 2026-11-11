#!/usr/bin/env python3
"""Independently deny Alpine advisory gaps; not a source-fix clearance engine.

Compose AFTER the existing runtime/source checks, not instead of them. A trusted
producer binds four full official responses to complete immutable-image scan and
live APK query/database evidence. The consumer requires the producer's canonical
binding digest through an independent trusted channel. A digest copied from an
untrusted binding alongside its inputs is NOT authentication.

No discharge format is accepted yet: reviewed exact source/recipe/feature/live
proofs and paired corrected-image tests are still outstanding. In particular,
version changes, missing tools, empty fixed-only findings and operator workflow
claims cannot remove the persistent holds. The current correct verdict is BLOCKED.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from alpine_advisories import match_installed
from alpine_advisory_snapshot import MAXIMUM_AGE_SECONDS, read_snapshot
from alpine_language_inventory import check_catalogue, validate_capture
from alpine_source_inputs import InputError, require
from alpine_source_inventory import InventoryError, canonical_sha256, source_plan
from check_native_image_scan import DIGEST, check_report
from verify_alpine_external_sources import unique

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
FILES = {"report", "identity", "scanner", "runtime_inventory"}
IDENTITY_FIELDS = {
    "ManifestDigest",
    "ConfigDigest",
    "OS",
    "Architecture",
    "RootFSDiffIDs",
}
# Historical 19-record assessment, not a version exclusion list. These remain
# pending even if the publisher omits them or an installed version/name changes.
# test_nonempty_resealed_publisher_truncation_cannot_drop_known19_or_owned_holds
# verifies the retention invariant against the original complete responses.
KNOWN_ADVISORIES = {
    "cairo": ("1.18.4-r1", ("CVE-2025-50422",)),
    "coreutils": ("9.11-r0", ("CVE-2016-2781", "CVE-2026-56391", "CVE-2026-56392")),
    "glib": ("2.88.1-r1", ("CVE-2026-58016",)),
    "sqlite": (
        "3.53.4-r0",
        (
            "CVE-2026-119371",
            "CVE-2026-119372",
            "CVE-2026-119373",
            "CVE-2026-119374",
            "CVE-2026-119375",
            "CVE-2026-119376",
            "CVE-2026-119377",
            "CVE-2026-119378",
            "CVE-2026-119379",
            "CVE-2026-119586",
            "CVE-2026-119591",
        ),
    ),
    "tar": ("1.35-r5", ("CVE-2025-45582", "CVE-2026-18477", "CVE-2026-18508")),
}
OWNED_SUBJECTS = ("busybox", "busybox-binsh", "ecm-owned-native", "ssl_client")


def raw_sha256(path: Path) -> str:
    require(
        path.is_file()
        and not path.is_symlink()
        and path.absolute() == path.resolve()
        and 0 < path.stat().st_size <= 64 * 1024**2,
        "canonical bounded regular evidence file required",
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict:
    require(
        path.is_file()
        and not path.is_symlink()
        and path.absolute() == path.resolve()
        and 0 < path.stat().st_size <= 64 * 1024**2,
        "canonical bounded regular evidence file required",
    )
    try:
        result = json.loads(path.read_bytes(), object_pairs_hook=unique)
        require(isinstance(result, dict), "evidence object required")
        return result
    except (ValueError, RecursionError) as exc:
        raise InputError("invalid or ambiguous evidence JSON: " + str(exc)) from exc


def helper_digests() -> dict:
    # Deliberate superset of the import closure: bind ALL local Python modules,
    # including future imports. Trusted read-only checkout, NEVER input-directory
    # execution. This seal does not make modifiable consumer code trustworthy.
    root = Path(__file__).resolve().parent
    return {
        path.relative_to(root).as_posix(): raw_sha256(path)
        for path in sorted(root.rglob("*.py"))
    }


def subject(files: dict[str, Path], architecture: str) -> tuple[dict, dict]:
    require(set(files) == FILES, "all four original image evidence files required")
    require(architecture in {"amd64", "arm64"}, "native architecture required")
    documents = {name: read_json(path) for name, path in files.items()}
    identity, inventory = documents["identity"], documents["runtime_inventory"]
    require(
        set(identity) == IDENTITY_FIELDS
        and set(inventory) == IDENTITY_FIELDS | {"capture"}
        and {key: inventory[key] for key in IDENTITY_FIELDS} == identity
        and identity["Architecture"] == architecture
        and identity["OS"] == "linux",
        "complete immutable manifest/config/platform/rootfs inventory identity differs",
    )
    try:
        plan = source_plan(
            inventory["capture"], {"amd64": "x86_64", "arm64": "aarch64"}[architecture]
        )
    except (InventoryError, KeyError, ValueError, TypeError) as exc:
        raise InputError("invalid complete live APK inventory: " + str(exc)) from exc
    # Bind structured identities directly; do not classify human-readable errors
    # from the independent scanner validator as a machine interface.
    diff_ids = identity["RootFSDiffIDs"]
    metadata = documents["report"].get("Metadata")
    require(
        all(
            isinstance(identity[k], str) and DIGEST.fullmatch(identity[k])
            for k in ["ManifestDigest", "ConfigDigest"]
        )
        and isinstance(diff_ids, list)
        and bool(diff_ids)
        and all(isinstance(d, str) and DIGEST.fullmatch(d) for d in diff_ids)
        and isinstance(metadata, dict)
        and metadata.get("ImageID") == identity["ConfigDigest"]
        and metadata.get("DiffIDs") == diff_ids
        and isinstance(metadata.get("ImageConfig"), dict)
        and metadata["ImageConfig"].get("os") == "linux"
        and metadata["ImageConfig"].get("architecture") == architecture
        and metadata["ImageConfig"].get("rootfs")
        == {"type": "layers", "diff_ids": diff_ids},
        "scan/image config/platform/rootfs identity binding differs",
    )
    return documents, plan


def bind_evidence(
    files: dict[str, Path],
    snapshot: Path,
    architecture: str,
    language_capture: Path | None = None,
    language_capture_sha256: str | None = None,
) -> dict:
    """Trusted acquisition-side byte seal, not a signed attestation or clearance."""
    documents, plan = subject(files, architecture)
    read_snapshot(snapshot)
    language = language_evidence(documents, language_capture, language_capture_sha256)
    return {
        "schema_version": 1,
        "status": "BOUND_ADVISORY_DENIAL_INPUTS_ONLY",
        "captured_at": datetime.now(UTC).isoformat(),
        "identity": documents["identity"],
        "files": {name: raw_sha256(path) for name, path in files.items()},
        "snapshot_index_sha256": raw_sha256(snapshot / "index.json"),
        "helpers": helper_digests(),
        "database_sha256": plan["database_sha256"],
        "package_set_sha256": plan["package_set_sha256"],
        "language_capture": language,
        "full_runtime_advisory_acceptance": False,
    }


def evaluate(
    files: dict[str, Path],
    snapshot: Path,
    binding: dict,
    expected_binding_sha256: str,
    architecture: str,
    language_capture: Path | None = None,
    language_capture_sha256: str | None = None,
) -> dict:
    """Consume exact bound inputs and retain known/new/owned pending findings."""
    require(
        isinstance(expected_binding_sha256, str)
        and _SHA256_RE.fullmatch(expected_binding_sha256) is not None
        and canonical_sha256(binding) == expected_binding_sha256,
        "trusted independent binding digest differs",
    )
    require(
        set(binding)
        == {
            "schema_version",
            "status",
            "captured_at",
            "identity",
            "files",
            "snapshot_index_sha256",
            "helpers",
            "database_sha256",
            "package_set_sha256",
            "language_capture",
            "full_runtime_advisory_acceptance",
        }
        and type(binding["schema_version"]) is int
        and binding["schema_version"] == 1
        and binding["status"] == "BOUND_ADVISORY_DENIAL_INPUTS_ONLY"
        and binding["full_runtime_advisory_acceptance"] is False
        and binding["helpers"] == helper_digests(),
        "exact trusted pending advisory binding schema/helpers required",
    )
    try:
        stamp = datetime.fromisoformat(binding["captured_at"])
        require(
            stamp.tzinfo is not None
            and stamp.utcoffset().total_seconds() == 0
            and 0 <= (datetime.now(UTC) - stamp).total_seconds() <= MAXIMUM_AGE_SECONDS,
            "stale or future advisory/image binding",
        )
    except (TypeError, ValueError) as exc:
        raise InputError("invalid advisory/image binding time: " + str(exc)) from exc
    require(
        set(files) == FILES
        and binding["files"] == {name: raw_sha256(path) for name, path in files.items()}
        and binding["snapshot_index_sha256"] == raw_sha256(snapshot / "index.json"),
        "bound original scan/scanner/live inventory/snapshot bytes differ",
    )
    documents, plan = subject(files, architecture)
    language = language_evidence(documents, language_capture, language_capture_sha256)
    require(
        binding["language_capture"] == language,
        "bound physical language capture differs",
    )
    require(
        binding["identity"] == documents["identity"]
        and binding["database_sha256"] == plan["database_sha256"]
        and binding["package_set_sha256"] == plan["package_set_sha256"],
        "bound complete query/database or immutable image subject differs",
    )
    official = read_snapshot(snapshot)
    matched = match_installed(
        list(official["trackers"].values()),
        documents["runtime_inventory"]["capture"],
        {"amd64": "x86_64", "arm64": "aarch64"}[architecture],
    )
    scan_failures = check_report(
        documents["report"],
        documents["identity"],
        documents["scanner"],
        architecture,
        documents["runtime_inventory"],
    )
    holds = []
    for origin, (version, cves) in KNOWN_ADVISORIES.items():
        installed = [
            {"name": p["name"], "version": p["version"]}
            for p in plan["packages"]
            if p["fields"].get("o") == origin
        ]
        for cve in cves:
            holds.append(
                {
                    "repository": "3.24-main",
                    "source": origin,
                    "recorded_version": version,
                    "cve": cve,
                    "recorded_severity": "MEDIUM"
                    if cve == "CVE-2025-45582"
                    else "UNKNOWN",
                    "recorded_cvss3": {
                        "score": 4.1,
                        "vector": "CVSS:3.1/AV:L/AC:H/PR:N/UI:R/S:C/C:N/I:L/A:L",
                    }
                    if cve == "CVE-2025-45582"
                    else {"score": 0.0, "vector": None},
                    "installed_packages": installed,
                    "status": "PENDING_EXACT_SOURCE_FEATURE_LIVE_RECONCILIATION",
                }
            )
    owned = [
        {
            "name": name,
            "status": "PENDING_COMPONENT_SOURCE_RECIPE_FEATURE_SIGNED_LIVE_BYTE_PROOF",
        }
        for name in OWNED_SUBJECTS
    ]
    installed_sources = {p["fields"].get("o") for p in plan["packages"]}
    fixed_records = [
        {"repository": part, **row}
        for part, data in official["secdb"].items()
        for row in data["fix_records"]
        if row["source"] in installed_sources
    ]
    return {
        "schema_version": 1,
        "status": "BLOCKED",
        "identity": documents["identity"],
        "binding_sha256": expected_binding_sha256,
        "snapshot_index_sha256": binding["snapshot_index_sha256"],
        "installed_packages": len(plan["packages"]),
        "package_set_sha256": plan["package_set_sha256"],
        "scan_failures": scan_failures,
        "language_inventory": {
            "status": "SCOPED_PHYSICAL_CATALOGUE_JOINED"
            if language
            else "MINIMUM_COVERAGE_ONLY",
            "complete_image_catalogue": False,
            "capture": language,
        },
        "unfixed_matches": matched["unfixed_matches"],
        "known_advisory_holds": holds,
        "owned_subject_holds": owned,
        "additional_unresolved_packages": [
            r for r in matched["unresolved_packages"] if r["name"] not in OWNED_SUBJECTS
        ],
        "fixed_secdb_records_not_clearance": fixed_records,
        "remaining_acceptance": [
            "Whole-image language discovery beyond reviewed Python roots and gosu; paired native physical capture",
            "Exact native APK version/fixed-secdb and CPE applicability reconciliation",
            "Reviewed per-advisory source/patch/recipe/feature/live-byte proof for owned components",
            "Explicit bounded nonapplicability controls, not blanket operator workflow waivers",
            "Actual paired corrected final images and independent source/runtime/security checks",
        ],
        "full_runtime_advisory_acceptance": False,
        "scope": "Independent denial only. All exact fixed:false matches block regardless of score or scanner label. Historical nineteen holds and four owned subjects persist across publisher omission, package renaming/removal and synthetic version changes. Fixed-only omissions, opaque aliases/version-zero metadata and build receipts are not fix proof. No discharge, waiver, suppression, publication or deployment authorization is accepted.",
    }


def language_evidence(
    documents: dict, path: Path | None, anchor: str | None
) -> dict | None:
    require(
        (path is None) == (anchor is None),
        "physical language capture and independent digest required together",
    )
    if path is None:
        return None
    capture = validate_capture(read_json(path), anchor, documents["identity"])
    check_catalogue(documents["report"], capture)
    return {"sha256": raw_sha256(path), "trusted_capture_sha256": anchor}


def write_fresh(path: Path, value: dict) -> None:
    require(
        not path.exists() and not path.is_symlink(), "fresh evidence output required"
    )
    with path.open("x") as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["bind", "verify"])
    for name in sorted(FILES):
        parser.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--architecture", choices=["amd64", "arm64"], required=True)
    parser.add_argument("--binding", type=Path)
    parser.add_argument("--binding-sha256")
    parser.add_argument("--language-capture", type=Path)
    parser.add_argument("--language-capture-sha256")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        files = {name: getattr(args, name) for name in FILES}
        if args.command == "bind":
            require(
                args.binding is None and args.binding_sha256 is None,
                "bind does not consume an untrusted prior binding",
            )
            result = bind_evidence(
                files,
                args.snapshot,
                args.architecture,
                args.language_capture,
                args.language_capture_sha256,
            )
            write_fresh(args.output, result)
            print(canonical_sha256(result))
            return 0
        require(
            args.binding is not None, "independently anchored producer binding required"
        )
        result = evaluate(
            files,
            args.snapshot,
            read_json(args.binding),
            args.binding_sha256,
            args.architecture,
            args.language_capture,
            args.language_capture_sha256,
        )
        write_fresh(args.output, result)
        print(
            "ADVISORY BLOCKED: unreconciled source/provider findings; see "
            + str(args.output),
            file=sys.stderr,
        )
        return 1
    except (
        InputError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RecursionError,
    ) as exc:
        print("ADVISORY DENIED: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
