#!/usr/bin/env python3
"""Acquire/recheck bounded fresh official stable advisory data, not clearance.

No cache fallback, external JSON-LD context execution, package installation or
runtime trust changes. Transport is official HTTPS; snapshots are not signed
security databases. Their exact delivery still needs immutable-image binding.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import stat
import zlib
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

from alpine_advisories import HOST, decode_tracker
from alpine_source_inputs import InputError, require
from verify_alpine_external_sources import sha256, unique

MAXIMUM_RAW_BYTES = 128 * 1024**2
MAXIMUM_AGE_SECONDS = 6 * 60 * 60
SOURCES = {
    **{
        "tracker-" + part: {
            "url": HOST + "/branch/3.24-" + part,
            "accept": "application/ld+json",
            "kind": "tracker",
            "repository": "3.24-" + part,
        }
        for part in ["main", "community"]
    },
    **{
        "secdb-" + part: {
            "url": "https://secdb.alpinelinux.org/v3.24/" + part + ".json",
            "accept": "application/json",
            "kind": "secdb",
            "repository": part,
        }
        for part in ["main", "community"]
    },
}


def decode_secdb(document: dict, repository: str) -> dict:
    require(
        repository in {"main", "community"}
        and isinstance(document, dict)
        and set(document)
        == {"apkurl", "archs", "reponame", "urlprefix", "distroversion", "packages"}
        and document["distroversion"] == "v3.24"
        and document["reponame"] == repository
        and document["urlprefix"] == "https://dl-cdn.alpinelinux.org/alpine"
        and document["apkurl"]
        == "{{urlprefix}}/{{distroversion}}/{{reponame}}/{{arch}}/{{pkg.name}}-{{pkg.ver}}.apk"
        and isinstance(document["archs"], list)
        and all(isinstance(arch, str) and bool(arch) for arch in document["archs"])
        and len(set(document["archs"])) == len(document["archs"])
        and {"x86_64", "aarch64"} <= set(document["archs"])
        and isinstance(document["packages"], list)
        and bool(document["packages"]),
        "complete exact stable fixed-secdb document required",
    )
    names, records = set(), []
    for outer in document["packages"]:
        require(
            isinstance(outer, dict) and set(outer) == {"pkg"},
            "fixed-secdb package wrapper required",
        )
        package = outer["pkg"]
        require(
            isinstance(package, dict)
            and set(package) == {"name", "secfixes"}
            and isinstance(package["name"], str)
            and bool(package["name"])
            and package["name"] not in names
            and isinstance(package["secfixes"], dict),
            "unique fixed-secdb package/fix mapping required",
        )
        names.add(package["name"])
        for version, advisories in package["secfixes"].items():
            require(
                isinstance(version, str)
                and bool(version)
                and isinstance(advisories, list)
                and bool(advisories)
                and all(
                    isinstance(value, str)
                    and value.strip()
                    and all(ord(c) >= 32 for c in value)
                    for value in advisories
                ),
                "complete raw fixed-secdb advisory entries required",
            )
            # Preserve composite, GHSA-only, ALPINE and other raw primary aliases.
            # Version zero is publisher metadata, not proof that runtime is fixed.
            records.append(
                {
                    "source": package["name"],
                    "fixed_version": version,
                    "advisories": advisories,
                }
            )
    return {
        "status": "OFFICIAL_FIXED_SECDB_RECORDS_ONLY",
        "repository": repository,
        "package_count": len(names),
        "fix_records": records,
        "full_runtime_advisory_acceptance": False,
    }


def validate_document(name: str, raw: bytes) -> dict:
    require(
        name in SOURCES
        and isinstance(raw, bytes)
        and 0 < len(raw) <= MAXIMUM_RAW_BYTES,
        "bounded reviewed advisory document required",
    )
    try:
        document = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, RecursionError) as exc:
        raise InputError("invalid or ambiguous advisory JSON") from exc
    descriptor = SOURCES[name]
    return (
        decode_tracker(document, descriptor["repository"])
        if descriptor["kind"] == "tracker"
        else decode_secdb(document, descriptor["repository"])
    )


def fetch_document(name: str, descriptor: dict) -> tuple[bytes, dict]:
    require(
        SOURCES.get(name) == descriptor, "reviewed exact advisory endpoint required"
    )
    with urlopen(
        Request(descriptor["url"], headers={"Accept": descriptor["accept"]}), timeout=90
    ) as response:
        content_type = (
            response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        )
        require(
            response.url == descriptor["url"]
            and response.status == 200
            and content_type in {"application/json", "application/ld+json"},
            "official HTTPS advisory response identity/type differs",
        )
        raw = response.read(MAXIMUM_RAW_BYTES + 1)
        require(
            0 < len(raw) <= MAXIMUM_RAW_BYTES,
            "official advisory body exceeds explicit bound",
        )
        return raw, {
            "url": descriptor["url"],
            "response_url": response.url,
            "http_status": response.status,
            "content_type": content_type,
            "response_headers": dict(response.headers),
        }


def canonical_directory(path: Path) -> None:
    require(
        path.is_dir() and not path.is_symlink() and path.absolute() == path.resolve(),
        "canonical advisory snapshot directory required",
    )


def regular(path: Path, maximum: int) -> None:
    require(
        path.is_file()
        and not path.is_symlink()
        and path.absolute() == path.resolve()
        and 0 < path.stat().st_size <= maximum
        and stat.S_IMODE(path.stat().st_mode) == 0o644,
        "canonical bounded regular advisory snapshot file required",
    )


def prepare(output: Path, *, evidence: Path | None = None) -> dict:
    canonical_directory(output.parent)
    require(
        not output.exists() and not output.is_symlink(),
        "advisory snapshot output must be fresh",
    )
    if evidence is not None:
        canonical_directory(evidence.parent)
        require(
            not evidence.exists()
            and not evidence.is_symlink()
            and not evidence.resolve().is_relative_to(output.resolve())
            and not output.resolve().is_relative_to(evidence.resolve()),
            "fresh disjoint advisory acquisition evidence required",
        )
        evidence.mkdir(mode=0o755)
    captured_at = datetime.now(UTC).isoformat()
    prepared = []
    for name, descriptor in SOURCES.items():
        raw, response = fetch_document(name, descriptor)
        if evidence is not None:
            require(
                isinstance(raw, bytes) and 0 < len(raw) <= MAXIMUM_RAW_BYTES,
                "bounded advisory acquisition evidence required",
            )
            with (evidence / (name + ".json.gz")).open("xb") as stream:
                stream.write(gzip.compress(raw, mtime=0))
            audit = {
                "status": "RAW_RESPONSE_RECEIVED_NOT_VALIDATED",
                "response": response,
                "raw_size": len(raw),
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
            }
            with (evidence / (name + "-response.json")).open("x") as stream:
                stream.write(json.dumps(audit, sort_keys=True, indent=2) + "\n")
        validate_document(name, raw)
        require(
            response["url"] == response["response_url"] == descriptor["url"]
            and response["http_status"] == 200
            and response["content_type"] in {"application/json", "application/ld+json"},
            "acquired advisory response identity differs",
        )
        blob = gzip.compress(raw, mtime=0)
        row = {
            "name": name,
            "file": name + ".json.gz",
            **response,
            "accept": descriptor["accept"],
            "raw_size": len(raw),
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest(),
        }
        prepared.append((row, blob))
    # Every response passes before publishing anything. Failed acquisition never
    # overwrites an operator output or publishes a partial snapshot/index.
    output.mkdir(mode=0o755)
    for row, blob in prepared:
        path = output / row["file"]
        with path.open("xb") as stream:
            stream.write(blob)
        path.chmod(0o644)
        require(
            sha256(path) == row["sha256"], "copied advisory document digest differs"
        )
    helper = output / "recipe/alpine_advisory_snapshot.py"
    helper.parent.mkdir(mode=0o755)
    shutil.copyfile(Path(__file__).resolve(), helper)
    helper.chmod(0o644)
    require(
        sha256(helper) == sha256(Path(__file__)),
        "copied advisory helper digest differs",
    )
    index = {
        "schema_version": 1,
        "status": "FRESH_OFFICIAL_STABLE_ADVISORY_DATA_ONLY",
        "captured_at": captured_at,
        "records": [row for row, _ in prepared],
        "helper_sha256": sha256(Path(__file__)),
        "full_runtime_advisory_acceptance": False,
        "scope": "Four complete official stable tracker/fixed-secdb responses over ordinary validated HTTPS, bounded and hash-sealed with raw aliases/state metadata intact. Capture age starts before the first request. Not signed databases, owned backport/CPE/fix reconciliation, complete freshness of publisher internals, whole-image advisory/source clearance or publication authorization.",
    }
    with (output / "index.json").open("x") as stream:
        stream.write(json.dumps(index, sort_keys=True, indent=2) + "\n")
    (output / "index.json").chmod(0o644)
    return index


def read_snapshot(directory: Path, *, now: datetime | None = None) -> dict:
    try:
        canonical_directory(directory)
        regular(directory / "index.json", 1024**2)
        index = json.loads(
            (directory / "index.json").read_text(), object_pairs_hook=unique
        )
        require(
            isinstance(index, dict)
            and set(index)
            == {
                "schema_version",
                "status",
                "captured_at",
                "records",
                "helper_sha256",
                "full_runtime_advisory_acceptance",
                "scope",
            }
            and type(index["schema_version"]) is int
            and index["schema_version"] == 1
            and index["status"] == "FRESH_OFFICIAL_STABLE_ADVISORY_DATA_ONLY"
            and index["full_runtime_advisory_acceptance"] is False,
            "exact pending advisory snapshot schema required",
        )
        stamp = datetime.fromisoformat(index["captured_at"])
        current = datetime.now(UTC) if now is None else now
        require(
            stamp.tzinfo is not None
            and stamp.utcoffset().total_seconds() == 0
            and current.tzinfo is not None,
            "UTC advisory capture time required",
        )
        require(
            0 <= (current - stamp).total_seconds() <= MAXIMUM_AGE_SECONDS,
            "stale or future advisory snapshot",
        )
        helper = directory / "recipe/alpine_advisory_snapshot.py"
        regular(helper, 1024**2)
        require(
            index["helper_sha256"] == sha256(Path(__file__)) == sha256(helper),
            "trusted advisory copier bytes differ",
        )
        require(
            isinstance(index["records"], list)
            and len(index["records"]) == len(SOURCES)
            and {row["name"] for row in index["records"]} == set(SOURCES),
            "all four unique advisory documents required",
        )
        expected = {"index.json", "recipe", "recipe/alpine_advisory_snapshot.py"} | {
            name + ".json.gz" for name in SOURCES
        }
        require(
            {path.relative_to(directory).as_posix() for path in directory.rglob("*")}
            == expected
            and all(not path.is_symlink() for path in directory.rglob("*")),
            "closed advisory snapshot layout required",
        )
        decoded = {"trackers": {}, "secdb": {}}
        for row in index["records"]:
            name = row["name"]
            descriptor = SOURCES[name]
            require(
                set(row)
                == {
                    "name",
                    "file",
                    "url",
                    "accept",
                    "response_url",
                    "http_status",
                    "content_type",
                    "response_headers",
                    "raw_size",
                    "raw_sha256",
                    "size",
                    "sha256",
                }
                and row["file"] == name + ".json.gz"
                and row["url"] == row["response_url"] == descriptor["url"]
                and row["accept"] == descriptor["accept"]
                and type(row["http_status"]) is int
                and row["http_status"] == 200
                and row["content_type"] in {"application/json", "application/ld+json"}
                and isinstance(row["response_headers"], dict)
                and type(row["raw_size"]) is int
                and 0 < row["raw_size"] <= MAXIMUM_RAW_BYTES
                and type(row["size"]) is int,
                "exact advisory response/bound identity required",
            )
            path = directory / row["file"]
            regular(path, MAXIMUM_RAW_BYTES)
            require(
                path.stat().st_size == row["size"] and sha256(path) == row["sha256"],
                "compressed advisory input bytes differ",
            )
            decoder = zlib.decompressobj(31)
            raw = decoder.decompress(path.read_bytes(), row["raw_size"] + 1)
            require(
                decoder.eof
                and not decoder.unused_data
                and not decoder.unconsumed_tail
                and len(raw) == row["raw_size"]
                and hashlib.sha256(raw).hexdigest() == row["raw_sha256"],
                "complete bounded raw advisory input differs",
            )
            record = validate_document(name, raw)
            decoded["trackers" if descriptor["kind"] == "tracker" else "secdb"][
                descriptor["repository"]
            ] = record
        return {"index": index, **decoded}
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RecursionError,
        zlib.error,
    ) as exc:
        raise InputError("invalid advisory snapshot: " + str(exc)) from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        help="Fresh disjoint directory retaining raw responses before validation; defaults beside output",
    )
    args = parser.parse_args()
    try:
        evidence = (
            args.evidence_dir
            if args.evidence_dir is not None
            else args.output_dir.with_name(args.output_dir.name + ".acquisition")
        )
        result = prepare(args.output_dir, evidence=evidence)
        read_snapshot(args.output_dir)
    except (InputError, OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, "advisory snapshot preparation failed: " + str(exc) + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
