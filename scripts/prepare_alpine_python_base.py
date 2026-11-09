"""Build-only acquisition of the pinned official Python base source/OCI graph.

The runtime verifier rederives the graph offline. GPG trust is private to the
builder work directory; no source, build recipe or target executable is run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen

from alpine_source_inputs import InputError, require
from prepare_alpine_external_sources import validate_destinations
from verify_alpine_external_sources import read_json, sha256
from verify_alpine_python_base import (
    BASE_INDEX,
    PUBLIC_KEY_SHA256,
    PYTHON_VERSION,
    RECIPE_SHA256,
    RELEASE_KEY,
    SIGNATURE_SHA256,
    SOURCE_COMMIT,
    SOURCE_SHA256,
    metadata,
    verify,
)

__all__ = ["RELEASE_KEY", "InputError", "fetch", "prepare", "verify_release"]
REGISTRY = "https://registry-1.docker.io/v2/library/python/"
MEDIA = "application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json"


def fetch(
    url: str,
    target: Path,
    expected: str,
    *,
    headers: dict | None = None,
    size: int | None = None,
) -> dict:
    require(
        url.startswith("https://")
        and re.fullmatch(r"[0-9a-f]{64}", expected) is not None,
        "HTTPS and exact original blob hash required",
    )
    require(
        not target.exists() and not target.is_symlink(),
        "fresh original Python input path required",
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    request = Request(
        url,
        headers={"User-Agent": "ECM-pinned-Python-base-source/1.0", **(headers or {})},
    )
    with urlopen(request, timeout=120) as response:
        require(
            response.status == 200, "successful original Python input response required"
        )
        count = 0
        with target.open("xb") as stream:
            while data := response.read(1024 * 1024):
                count += len(data)
                require(count <= 512 * 1024**2, "oversized Python source/OCI input")
                stream.write(data)
    require(
        sha256(target) == expected and (size is None or target.stat().st_size == size),
        "original Python input hash/size mismatch",
    )
    # Do not retain request Authorization or redirected signed-URL credentials.
    return {
        "url": url,
        "sha256": expected,
        "size": target.stat().st_size,
        "status": 200,
    }


def verify_release(source: Path, home: Path) -> dict:
    require(
        not home.exists() and not home.is_symlink(),
        "fresh isolated build-only GPG home required",
    )
    key = source / "python-release-manager.asc"
    signature = source / ("Python-" + PYTHON_VERSION + ".tar.xz.asc")
    require(
        sha256(key) == PUBLIC_KEY_SHA256 and sha256(signature) == SIGNATURE_SHA256,
        "original CPython signing evidence bytes differ",
    )
    home.mkdir(mode=0o700)
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(home),
        "GNUPGHOME": str(home),
        "LC_ALL": "C",
    }
    command = [
        "gpg",
        "--homedir",
        str(home),
        "--batch",
        "--no-autostart",
        "--no-auto-key-retrieve",
    ]
    shown = subprocess.run(
        [*command, "--with-colons", "--show-keys", str(key)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    fingerprints = [
        row.split(":")[9] for row in shown.stdout.splitlines() if row.startswith("fpr:")
    ]
    require(
        shown.returncode == 0
        and sum(row.startswith("pub:") for row in shown.stdout.splitlines()) == 1
        and bool(fingerprints)
        and fingerprints[0] == RELEASE_KEY,
        "pinned original CPython release-manager fingerprint mismatch",
    )
    imported = subprocess.run(
        [*command, "--import", str(key)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    require(
        imported.returncode == 0, "cannot import isolated public CPython release key"
    )
    result = subprocess.run(
        [
            *command,
            "--status-fd",
            "1",
            "--verify",
            str(signature),
            str(source / ("Python-" + PYTHON_VERSION + ".tar.xz")),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=90,
    )
    statuses = [
        row
        for row in result.stdout.splitlines()
        if row.startswith("[GNUPG:] VALIDSIG ")
    ]
    require(
        result.returncode == 0
        and len(statuses) == 1
        and statuses[0].split()[-1] == RELEASE_KEY
        and not re.search(
            r"\b(BADSIG|ERRSIG|EXPSIG|EXPKEYSIG|REVKEYSIG)\b",
            result.stdout + result.stderr,
        ),
        "exact pinned CPython source signature verification failed",
    )
    return {
        "fingerprint": RELEASE_KEY,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit": result.returncode,
        "scope": "Original CPython source archive signature only, using an isolated builder public keyring. No runtime trust-store change.",
    }


def prepare(architecture: str, output: Path, work: Path, receipt: Path) -> dict:
    validate_destinations(output, work, receipt)
    require(
        architecture in {"amd64", "arm64"}, "supported pinned Python platform required"
    )
    output.mkdir(parents=True)
    work.mkdir(parents=True)
    acquisitions = []
    try:
        request = Request(
            "https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/python:pull"
        )
        with urlopen(request, timeout=60) as response:
            require(
                response.status == 200, "public Python registry authorization failed"
            )
            token = json.loads(response.read(1024 * 1024))["token"]
        require(
            isinstance(token, str) and bool(token), "missing public registry pull token"
        )
        headers = {"Authorization": "Bearer " + token, "Accept": MEDIA}
        acquisitions.append(
            fetch(
                REGISTRY + "manifests/sha256:" + BASE_INDEX,
                output / "index.json",
                BASE_INDEX,
                headers=headers,
            )
        )
        index = read_json(output / "index.json")
        descriptors = [
            r
            for r in index["manifests"]
            if r.get("platform", {}).get("architecture") == architecture
            and r["platform"].get("os") == "linux"
        ]
        require(len(descriptors) == 1, "unique original Python base platform required")

        def acquire(kind: str, descriptor: dict) -> dict:
            digest = descriptor["digest"]
            require(
                re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is not None,
                "exact original Python descriptor digest required",
            )
            target = output / "blobs" / digest[7:]
            if target.exists():
                require(
                    sha256(target) == digest[7:]
                    and target.stat().st_size == descriptor["size"],
                    "conflicting original Python blob",
                )
            else:
                acquisitions.append(
                    fetch(
                        REGISTRY + kind + "/" + digest,
                        target,
                        digest[7:],
                        headers=headers,
                        size=descriptor["size"],
                    )
                )
            return read_json(target) if kind == "manifests" else {}

        manifest = acquire("manifests", descriptors[0])
        acquire("blobs", manifest["config"])
        for layer in manifest["layers"]:
            acquire("blobs", layer)
        meta = metadata(output, architecture)
        attest = [
            r
            for r in index["manifests"]
            if r.get("annotations", {}).get("vnd.docker.reference.digest")
            == meta["manifest_digest"]
        ]
        require(len(attest) == 1, "unique original Python OCI attestation required")
        att = acquire("manifests", attest[0])
        acquire("blobs", att["config"])
        for layer in att["layers"]:
            acquire("blobs", layer)
        for name, url, digest in [
            (
                "source/Python-" + PYTHON_VERSION + ".tar.xz",
                "https://www.python.org/ftp/python/"
                + PYTHON_VERSION
                + "/Python-"
                + PYTHON_VERSION
                + ".tar.xz",
                SOURCE_SHA256,
            ),
            (
                "source/Python-" + PYTHON_VERSION + ".tar.xz.asc",
                "https://www.python.org/ftp/python/"
                + PYTHON_VERSION
                + "/Python-"
                + PYTHON_VERSION
                + ".tar.xz.asc",
                SIGNATURE_SHA256,
            ),
            (
                "source/python-release-manager.asc",
                "https://keys.openpgp.org/vks/v1/by-fingerprint/" + RELEASE_KEY,
                PUBLIC_KEY_SHA256,
            ),
            (
                "recipe/Dockerfile",
                "https://raw.githubusercontent.com/docker-library/python/"
                + SOURCE_COMMIT
                + "/3.12/alpine3.24/Dockerfile",
                RECIPE_SHA256,
            ),
            (
                "recipe/LICENSE",
                "https://raw.githubusercontent.com/docker-library/python/"
                + SOURCE_COMMIT
                + "/LICENSE",
                "0a3ffdfc4f368b76ae1140b5fbe2f2f69ee76ee00a3024e8a09ca9b2e265186f",
            ),
        ]:
            acquisitions.append(fetch(url, output / name, digest))
        signature = verify_release(output / "source", work / "gnupg")
        result = verify(output, architecture)
        record = {
            "status": "PREPARED_AND_VERIFIED_PINNED_PYTHON_BASE_SOURCE_ONLY",
            "full_runtime_source_acceptance": False,
            "architecture": architecture,
            "result": result,
            "signature": signature,
            "acquisition": acquisitions,
            "scope": "Original Python base source/recipe/provenance/layer delivery and independent release signature only. Installed-image binding, compiler/language/nested support, complete license/advisory and aggregate source/runtime/MCP gates remain separate.",
        }
    except Exception as exc:
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(
            json.dumps(
                {
                    "status": "PYTHON_BASE_PREPARATION_FAILED",
                    "full_runtime_source_acceptance": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "acquisition": acquisitions,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
        raise
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", choices=["amd64", "arm64"], required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--receipt-file", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(
        args.architecture,
        args.output_dir.absolute(),
        args.work_dir.absolute(),
        args.receipt_file.absolute(),
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "architecture": args.architecture,
                "full_runtime_source_acceptance": False,
            }
        )
    )


if __name__ == "__main__":
    main()
