"""Decode official stable tracker states, never infer source/advisory clearance.

Fixed-only secdb/Trivy omissions do not clear unfixed or unscored advisories.
The tracker embeds other branches and repeats identical ORM rows. Exact branch
selection and conflict-aware deduplication are distinct from JSON-key rejection.
Owned patched sources remain unresolved here, not compared as pseudo-versions
or exempted by source name. Reachability/primary-fix review is still required.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from urllib.parse import urlsplit

from alpine_source_inputs import InputError, require
from alpine_source_inventory import InventoryError, source_plan

HOST = "https://security.alpinelinux.org"
CONTEXT = HOST + "/static/context.jsonld"
REPOSITORIES = {"3.24-main", "3.24-community"}


def package_version(url: str) -> tuple[str, str]:
    require(isinstance(url, str), "tracker package/version URL required")
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise InputError("invalid tracker package URL") from exc
    require(
        parsed.scheme == "https"
        and parsed.netloc == "security.alpinelinux.org"
        and not parsed.query
        and not parsed.fragment,
        "official canonical tracker package URL required",
    )
    parts = parsed.path.split("/")
    require(
        len(parts) == 4
        and parts[:2] == ["", "srcpkg"]
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_.-]*", parts[2]) is not None
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_.~:-]*", parts[3]) is not None,
        "safe exact tracker source/version path required",
    )
    return parts[2], parts[3]


def severity(cvss: dict) -> str:
    require(
        isinstance(cvss, dict) and set(cvss) == {"score", "vector"},
        "complete tracker CVSS object required",
    )
    score, vector = cvss["score"], cvss["vector"]
    # The official field is named cvss3 even for twelve recorded CVSS 4.0 rows.
    require(
        type(score) in {int, float}
        and math.isfinite(score)
        and 0 <= score <= 10
        and (
            vector is None
            or isinstance(vector, str)
            and vector.startswith(("CVSS:3.0/", "CVSS:3.1/", "CVSS:4.0/"))
        ),
        "invalid tracker CVSS score/vector",
    )
    if vector is None or score == 0:
        return "UNKNOWN"
    return (
        "CRITICAL"
        if score >= 9
        else "HIGH"
        if score >= 7
        else "MEDIUM"
        if score >= 4
        else "LOW"
    )


def decode_tracker(document: dict, repository: str) -> dict:
    """Normalize all exact-branch states while retaining raw/unique row counts."""
    require(repository in REPOSITORIES, "reviewed stable tracker repository required")
    require(
        isinstance(document, dict)
        and set(document) == {"@context", "id", "items", "type"}
        and document["@context"] == CONTEXT
        and document["id"] == HOST + "/branch/" + repository
        and document["type"] == "Collection"
        and isinstance(document["items"], list)
        and bool(document["items"]),
        "complete exact stable tracker collection required",
    )
    documents, records, state_identities = {}, {}, {}
    seen = 0
    for item in document["items"]:
        require(
            isinstance(item, dict)
            and set(item)
            == {
                "@context",
                "cpeMatch",
                "cvss3",
                "description",
                "id",
                "ref",
                "state",
                "type",
            }
            and item["@context"] == CONTEXT
            and item["type"] == "Vulnerability"
            and isinstance(item["id"], str)
            and re.fullmatch(
                re.escape(HOST) + r"/vuln/CVE-[0-9]{4}-[0-9]{4,}", item["id"]
            )
            is not None
            and isinstance(item["description"], str)
            and isinstance(item["state"], list)
            and isinstance(item["cpeMatch"], list)
            and isinstance(item["ref"], list),
            "complete tracker vulnerability object required",
        )
        classification = severity(item["cvss3"])
        digest = hashlib.sha256(
            json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        require(
            item["id"] not in documents or documents[item["id"]] == digest,
            "conflicting repeated tracker vulnerability",
        )
        documents[item["id"]] = digest
        for state in item["state"]:
            require(
                isinstance(state, dict)
                and set(state)
                == {"@context", "fixed", "id", "packageVersion", "repo", "type", "vuln"}
                and state["@context"] == CONTEXT
                and state["type"] == "VulnerabilityState"
                and type(state["fixed"]) is bool
                and isinstance(state["repo"], str)
                and state["vuln"] == item["id"]
                and isinstance(state["id"], str)
                and re.fullmatch(re.escape(item["id"]) + r"#state/[0-9]+", state["id"])
                is not None,
                "complete unambiguous tracker state required",
            )
            source, version = package_version(state["packageVersion"])
            require(
                state["id"] not in state_identities
                or state_identities[state["id"]] == state,
                "conflicting repeated tracker state identifier",
            )
            state_identities[state["id"]] = state
            if state["repo"] != repository:
                continue
            seen += 1
            key = (source, version, item["id"])
            row = {
                "repository": repository,
                "source": source,
                "version": version,
                "cve": item["id"].rsplit("/", 1)[1],
                "vulnerability_url": item["id"],
                "fixed": state["fixed"],
                "severity": classification,
                "cvss3": item["cvss3"],
            }
            if key in records:
                previous = records[key]
                require(
                    {k: v for k, v in previous.items() if k != "state_ids"} == row,
                    "conflicting tracker source/version vulnerability states",
                )
                previous["state_ids"].add(state["id"])
            else:
                records[key] = {**row, "state_ids": {state["id"]}}
    return {
        "status": "OFFICIAL_STABLE_TRACKER_STATES_ONLY",
        "repository": repository,
        "items_seen": len(document["items"]),
        "unique_vulnerabilities": len(documents),
        "states_seen": seen,
        "states": [
            {**row, "state_ids": sorted(row["state_ids"])}
            for _, row in sorted(records.items())
        ],
        "full_runtime_advisory_acceptance": False,
    }


def match_installed(trackers: list[dict], capture: dict, machine: str) -> dict:
    """Exact official source/version matches only, with unresolved subjects held.

    This is a conservative input diagnostic, not complete freshness/CPE/owned
    backport/advisory enforcement or a reachability assessment. No zero-score,
    absent-fix or source-name exemption is permitted.
    """
    try:
        plan = source_plan(capture, machine)
    except (InventoryError, ValueError, KeyError, TypeError) as exc:
        raise InputError("invalid advisory installed subject: " + str(exc)) from exc
    require(
        len(trackers) == 2
        and {row.get("repository") for row in trackers} == REPOSITORIES,
        "both complete stable tracker repositories required",
    )
    subjects = {}
    for row in plan["packages"]:
        origin = row["fields"].get("o")
        if origin:
            subjects.setdefault((origin, row["version"]), []).append(row["name"])
    matches = {}
    for tracker in trackers:
        require(
            tracker["status"] == "OFFICIAL_STABLE_TRACKER_STATES_ONLY"
            and tracker["repository"] in REPOSITORIES
            and tracker["full_runtime_advisory_acceptance"] is False,
            "decoded stable tracker input required",
        )
        for row in tracker["states"]:
            key = row["source"], row["version"]
            if key in subjects and row["fixed"] is False:
                identity = row["repository"], row["source"], row["version"], row["cve"]
                value = {**row, "installed_packages": sorted(subjects[key])}
                require(
                    identity not in matches or matches[identity] == value,
                    "conflicting installed advisory matches",
                )
                matches[identity] = value
    unresolved = plan["unresolved_packages"]
    return {
        "status": "BLOCKED_PUBLISHED_UNFIXED_OR_UNRESOLVED_SUBJECTS"
        if matches or unresolved
        else "NO_EXACT_PUBLISHED_UNFIXED_MATCHES_ONLY",
        "unfixed_matches": [row for _, row in sorted(matches.items())],
        "unresolved_packages": unresolved,
        "source_plan": plan,
        "full_runtime_advisory_acceptance": False,
        "scope": "Exact complete query/database source/version matches to official stable tracker fixed:false states, including unknown scores. No suppression, primary-source reachability decision, source-name/owned-version exemption or full advisory clearance. Owned backports, missing/current-version CPE coverage, snapshot authenticity/freshness, fixed-only secdb reconciliation and independent whole-image scans remain required.",
    }
