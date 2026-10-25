#!/usr/bin/env python3
"""Offline MCP package/crypto check; stdout is one JSON receipt.

The host must first hash-verify the OCI archive with candidate_image.py and
verify-loaded after conversion. Host identity files bind this runtime evidence
to that subject; they are not independently trusted attestations. No system ssl
version, registry tag, network request, or application configuration is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import struct
import subprocess
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SOURCE = re.compile(r"[0-9a-f]{40}")
_MACHINES = {"x86_64": "amd64", "aarch64": "arm64"}


def validate_subject(identity, loaded, source, expected_image, expected_source, arch):
    """Reject missing/substituted host identity and any non-MCP/root image user."""
    if not isinstance(identity, dict) or arch not in {"amd64", "arm64"}:
        raise ValueError("missing or unsupported image identity")
    for field in ("ManifestDigest", "ConfigDigest"):
        if not isinstance(identity.get(field), str) or not _DIGEST.fullmatch(
            identity[field]
        ):
            raise ValueError(f"missing or malformed {field}")
    if not isinstance(expected_image, str) or not _DIGEST.fullmatch(expected_image):
        raise ValueError("expected image must be an immutable config digest")
    if identity["ConfigDigest"] != expected_image:
        raise ValueError("expected image/config substitution")
    if identity.get("OS") != "linux" or identity.get("Architecture") != arch:
        raise ValueError("image platform substitution")
    layers = identity.get("RootFSDiffIDs")
    if (
        not isinstance(layers, list)
        or not layers
        or any(
            not isinstance(layer, str) or not _DIGEST.fullmatch(layer)
            for layer in layers
        )
    ):
        raise ValueError("missing or malformed rootfs identity")
    if (
        not isinstance(loaded, list)
        or len(loaded) != 1
        or not isinstance(loaded[0], dict)
    ):
        raise ValueError("expected exactly one inspected image")
    expected = {
        "Id": expected_image,
        "Os": "linux",
        "Architecture": arch,
        "RootFS": {"Type": "layers", "Layers": layers},
    }
    if any(loaded[0].get(key) != value for key, value in expected.items()):
        raise ValueError("loaded image/config/platform/rootfs substitution")
    config = loaded[0].get("Config")
    if not isinstance(config, dict) or config.get("User") != "appuser:appgroup":
        raise ValueError("MCP must retain its default non-root appuser")
    if (
        not isinstance(source, str)
        or not _SOURCE.fullmatch(source)
        or not isinstance(expected_source, str)
        or not _SOURCE.fullmatch(expected_source)
        or source != expected_source
    ):
        raise ValueError("missing or substituted source SHA")


def validate_openssl(text: str) -> None:
    """Only reviewed, released OpenSSL 4.0 patches >=3 satisfy these CVE fixes."""
    match = (
        re.fullmatch(
            r"OpenSSL 4\.0\.(0|[1-9][0-9]*)(?: ([0-9]{1,2}) ([A-Z][a-z]{2}) ([0-9]{4}))?",
            text,
        )
        if isinstance(text, str) and len(text) <= 128
        else None
    )
    if match is None or int(match[1]) < 3:
        raise ValueError("missing, unreviewed, or vulnerable bundled OpenSSL version")
    if match[2] is not None:
        months = [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ]
        if match[3] not in months:
            raise ValueError("malformed bundled OpenSSL release date")
        datetime(int(match[4]), months.index(match[3]) + 1, int(match[2]))


def inspect_extension(path: Path, arch: str) -> dict:
    """Hash the loaded extension and check its real ELF architecture, not its name."""
    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:7] != b"\x7fELF\x02\x01\x01":
            raise ValueError(
                "loaded crypto binding is not a little-endian ELF64 library"
            )
        kind, machine = struct.unpack_from("<HH", header, 16)
        if kind != 3 or machine != {"amd64": 62, "arm64": 183}.get(arch):
            raise ValueError("loaded crypto binding ELF architecture mismatch")
        stream.seek(0)
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(path), "sha256": digest, "architecture": arch}


def validate_musl(text: str) -> dict:
    """Parse real apk installed-package output, denying missing/ambiguous versions."""
    packages = {}
    if not isinstance(text, str) or len(text) > 256:
        raise ValueError("missing or malformed musl inventory")
    for line in text.splitlines():
        match = re.fullmatch(
            r"(musl|musl-utils)-((0|[1-9][0-9]*)\."
            r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-r(0|[1-9][0-9]*))",
            line,
        )
        if match is None or match[1] in packages:
            raise ValueError("missing or ambiguous musl package version")
        if tuple(int(part) for part in match.groups()[2:]) < (1, 2, 6, 3):
            raise ValueError("vulnerable musl package version; require >=1.2.6-r3")
        packages[match[1]] = match[2]
    if set(packages) != {"musl", "musl-utils"}:
        raise ValueError("both musl and musl-utils must be installed")
    return packages


def inspect_musl() -> dict:
    """Query installed packages in this image, not a repository or host fixture."""
    result = subprocess.run(
        ["/sbin/apk", "info", "--installed", "--verbose", "musl", "musl-utils"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return validate_musl(result.stdout)


def inspect_installer() -> dict:
    """The retained Python-module installer must match the official 26.2 wheel."""
    import pip

    metadata_version = version("pip")
    if pip.__version__ != "26.2" or metadata_version != "26.2":
        raise ValueError("retained pip installer is not the reviewed 26.2 wheel")
    return {"version": pip.__version__, "metadata_version": metadata_version}


def check_runtime(arch: str) -> dict:
    if os.getuid() == 0 or os.getgid() == 0:
        raise ValueError("crypto check must execute as the default non-root image user")
    machine = platform.machine()
    if platform.system() != "Linux" or _MACHINES.get(machine) != arch:
        raise ValueError("runtime machine does not match the candidate architecture")

    import cryptography
    from cryptography.hazmat.backends.openssl.backend import backend
    from cryptography.hazmat.bindings import _rust
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if cryptography.__version__ != "50.0.2" or version("cryptography") != "50.0.2":
        raise ValueError("loaded cryptography is not the reviewed 50.0.2 wheel")
    openssl = backend.openssl_version_text()
    validate_openssl(openssl)
    native = inspect_extension(Path(_rust.__file__).resolve(strict=True), arch)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    message = b"synthetic offline MCP image crypto check"
    signature = key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    key.public_key().verify(signature, message, padding.PKCS1v15(), hashes.SHA256())
    cipher = AESGCM(AESGCM.generate_key(bit_length=256))
    nonce = b"test-nonce12"
    ciphertext = cipher.encrypt(nonce, message, b"image check")
    if cipher.decrypt(nonce, ciphertext, b"image check") != message:
        raise ValueError("AES-GCM roundtrip failed")
    musl = inspect_musl()
    pip = inspect_installer()
    return {
        "cryptography": cryptography.__version__,
        "openssl": openssl,
        "machine": machine,
        "uid": os.getuid(),
        "gid": os.getgid(),
        "native_extension": native,
        "operations": ["RSA-sign-verify", "AES-GCM-roundtrip"],
        "musl": musl,
        "pip": pip,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--loaded-image", type=Path, required=True)
    parser.add_argument("--source-sha", type=Path, required=True)
    parser.add_argument("--expected-image-id", required=True)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    args = parser.parse_args()
    try:
        identity = json.loads(args.identity.read_text())
        loaded = json.loads(args.loaded_image.read_text())
        source = args.source_sha.read_text().strip()
        validate_subject(
            identity,
            loaded,
            source,
            args.expected_image_id,
            args.expected_source_sha,
            args.architecture,
        )
        runtime = check_runtime(args.architecture)
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "source_sha": source,
                    "identity": identity,
                    "runtime": runtime,
                },
                sort_keys=True,
            )
        )
    except Exception as exc:  # noqa: BLE001 - the CLI denies every unexpected failure
        # Any unexpected library/import/operation failure is also a denial. Do not
        # emit application logs or pretend that an empty receipt is a PASS.
        print(json.dumps({"status": "FAIL", "error": str(exc)}, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
