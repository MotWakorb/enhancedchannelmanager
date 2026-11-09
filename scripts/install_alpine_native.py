#!/usr/bin/env python3
"""Offline installation of the exact owned APK using disposable public trust.

Run only in a disposable stable-Alpine image builder, never on an operator host.
Stable signed APK dependencies must already be installed. No solver bypasses.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import stat
import subprocess
import tempfile
import zlib
from contextlib import contextmanager
from pathlib import Path

from alpine_repository_policy import validate_repositories
from build_alpine_codecs import validate_platform
from build_native_runtime import (
    ARCHES,
    BuildError,
    _require,
    owned_artifacts,
    run,
    sha256,
)
from package_alpine_native import PACKAGE_NAME, checked_path


def load_package(directory: Path, arch: str) -> dict:
    receipt = directory / "package-output.json"
    _require(
        receipt.is_file() and not receipt.is_symlink(),
        "regular package receipt required",
    )
    record = json.loads(receipt.read_text())
    _require(
        record["schema_version"] == 1
        and record["status"] == "SIGNED_OWNED_APK_ONLY"
        and record["architecture"] == arch
        and record["package"]["name"] == PACKAGE_NAME,
        "owned package status/architecture mismatch",
    )
    for item in [record["package"], record["public_key"]]:
        relative = checked_path(item["file"])
        _require(len(relative.parts) == 1, "package/key filename must be a basename")
        path = directory / relative
        _require(
            path.is_file() and not path.is_symlink() and sha256(path) == item["sha256"],
            "owned package/key identity mismatch",
        )
    return record


@contextmanager
def temporary_trust(system: list[Path], public: Path, parent: Path):
    with tempfile.TemporaryDirectory(
        prefix="ecm-install-trust-", dir=parent
    ) as temporary:
        directory = Path(temporary)
        for source in system:
            if not source.exists():
                continue
            _require(
                source.is_dir() and not source.is_symlink(),
                "regular system trust directory required",
            )
            for key in source.iterdir():
                _require(
                    key.is_file() and not key.is_symlink() and key.suffix == ".pub",
                    "unexpected system APK trust entry",
                )
                target = directory / key.name
                _require(
                    not target.exists() or target.read_bytes() == key.read_bytes(),
                    "conflicting vendor APK key",
                )
                shutil.copy2(key, target)
        target = directory / "ecm-build.pub"
        _require(not target.exists(), "owned APK key collides with vendor trust")
        shutil.copy2(public, target)
        yield directory


def verify_installed(root: Path, expected: list[dict]) -> None:
    _require(
        expected and len({row["path"] for row in expected}) == len(expected),
        "installed artifact inventory incomplete",
    )
    for row in expected:
        path = root / checked_path(row["path"])
        _require(
            path.resolve().is_relative_to(root.resolve()),
            "installed artifact escapes runtime root",
        )
        if row["type"] == "file":
            _require(
                path.is_file()
                and not path.is_symlink()
                and sha256(path) == row["sha256"]
                and stat.S_IMODE(path.stat().st_mode) == row["mode"],
                "installed artifact bytes/mode mismatch",
            )
        else:
            _require(
                row["type"] == "symlink"
                and path.is_symlink()
                and str(path.readlink()) == row["target"]
                and sha256(path) == row["target_sha256"],
                "installed artifact link mismatch",
            )


def validate_ownership(text: str, inventory: list[dict], version: str) -> None:
    expected = {("/" + row["path"], PACKAGE_NAME + "-" + version) for row in inventory}
    lines = text.splitlines()
    actual = {tuple(line.split(" is owned by ", 1)) for line in lines}
    _require(
        len(lines) == len(expected) and actual == expected,
        "installed APK ownership mismatch",
    )


def validate_contents(
    text: str, inventory: list[dict], name: str, version: str
) -> None:
    lines = text.splitlines()
    _require(
        lines and lines[0] == name + "-" + version + " contains:",
        "unexpected APK contents header",
    )
    paths = [line for line in lines[1:] if line]
    _require(
        len(paths) == len(inventory)
        and set(paths) == {row["path"] for row in inventory},
        "installed APK contents mismatch",
    )


def damage_signed_metadata(source: Path, target: Path) -> None:
    """Change a signed ADB package-name byte, not ignored compression padding."""
    _require(
        not target.exists() and not target.is_symlink(),
        "negative-control output must be fresh",
    )
    decoder, encoder = zlib.decompressobj(-15), zlib.compressobj(wbits=-15)
    with source.open("rb") as stream, target.open("xb") as output:
        _require(
            stream.read(4) == b"ADBd",
            "owned signed APK must use reviewed ADB deflate format",
        )
        output.write(b"ADBd")
        first = True
        while chunk := stream.read(1024 * 1024):
            data = decoder.decompress(chunk)
            if first:
                _require(
                    data.startswith(b"ADB.pckg")
                    and data[:512].count(b"ecm-owned-native") == 1,
                    "owned signed APK metadata witness mismatch",
                )
                offset = data.index(b"ecm-owned-native")
                _require(
                    offset > 8 and data[offset - 1] == len(b"ecm-owned-native"),
                    "owned APK package-name field witness mismatch",
                )
                data = data[:offset] + b"f" + data[offset + 1 :]
                first = False
            output.write(encoder.compress(data))
        _require(
            not first and decoder.eof and not decoder.unused_data,
            "incomplete owned APK compressed stream",
        )
        output.write(encoder.compress(decoder.flush()))
        output.write(encoder.flush())


def install(directory: Path, arch: str, evidence: Path) -> dict:
    release = Path("/etc/alpine-release").read_text()
    validate_platform(release, platform.machine(), arch)
    validate_repositories(release, Path("/etc/apk/repositories").read_text())
    _require(
        not evidence.exists() and not evidence.is_symlink(),
        "installation evidence must be fresh",
    )
    record = load_package(directory, arch)
    package = directory / record["package"]["file"]
    public = directory / record["public_key"]["file"]
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": "/tmp",
        "LC_ALL": "C",
        "TZ": "UTC",
    }
    trust_paths = [Path("/etc/apk/keys"), Path("/lib/apk/keys")]
    before = {str(path): owned_artifacts(path) for path in trust_paths}
    repositories = Path("/etc/apk/repositories").read_bytes()
    # A matching untrusted key must never be sufficient without the explicitly
    # scoped temporary trust directory. Confirm the real default denial first.
    denial = subprocess.run(
        ["/sbin/apk", "verify", "--network=no", str(package)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    _require(
        denial.returncode != 0
        and "UNTRUSTED signature" in denial.stdout + denial.stderr,
        "owned APK unexpectedly trusted by the runtime default keys",
    )
    try:
        with temporary_trust(trust_paths, public, Path("/tmp")) as trust:
            verification = run(
                [
                    "/sbin/apk",
                    "verify",
                    "--network=no",
                    "--keys-dir",
                    str(trust),
                    str(package),
                ],
                cwd=directory,
                env=env,
            )
            damaged = trust / ".damaged-owned.apk"
            damage_signed_metadata(package, damaged)
            tamper = subprocess.run(
                [
                    "/sbin/apk",
                    "verify",
                    "--network=no",
                    "--keys-dir",
                    str(trust),
                    str(damaged),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            _require(
                tamper.returncode != 0
                and "UNTRUSTED signature" in tamper.stdout + tamper.stderr,
                "changed signed APK metadata was not rejected by signature verification",
            )
            installation = run(
                [
                    "/sbin/apk",
                    "add",
                    "--network=no",
                    "--keys-dir",
                    str(trust),
                    str(package),
                ],
                cwd=directory,
                env=env,
            )
    finally:
        _require(
            before == {str(path): owned_artifacts(path) for path in trust_paths},
            "runtime APK trusted keys changed",
        )
        _require(
            Path("/etc/apk/repositories").read_bytes() == repositories,
            "runtime APK repositories changed",
        )
    verify_installed(Path("/"), record["installed_artifacts"])
    contents = run(
        ["/sbin/apk", "info", "--contents", PACKAGE_NAME], cwd=directory, env=env
    )
    validate_contents(
        contents,
        record["installed_artifacts"],
        PACKAGE_NAME,
        record["package"]["version"],
    )
    ownership = run(
        [
            "/sbin/apk",
            "info",
            "--who-owns",
            *["/" + row["path"] for row in record["installed_artifacts"]],
        ],
        cwd=directory,
        env=env,
    )
    validate_ownership(
        ownership, record["installed_artifacts"], record["package"]["version"]
    )
    result = {
        "schema_version": 1,
        "status": "SIGNED_APK_INSTALLED_ONLY",
        "architecture": arch,
        "package_manifest_sha256": sha256(directory / "package-output.json"),
        "package": record["package"],
        "public_key": record["public_key"],
        "signature_verification": verification,
        "default_trust_denial": denial.stdout + denial.stderr,
        "damaged_package_denial": tamper.stdout + tamper.stderr,
        "damaged_package_control": "one authenticated ADB package-name byte changed; compression padding is not the test subject",
        "installation": installation,
        "ownership": ownership,
        "contents": contents,
        "installed_inventory": run(["/sbin/apk", "info", "-v"], cwd=directory, env=env),
        "runtime_trusted_keys": before,
        "repositories_sha256": sha256(Path("/etc/apk/repositories")),
        "helper_sha256": sha256(Path(__file__)),
        "scope": "Exact signed owned package and source ownership/bytes, temporary public-key trust removed. Not external APK/source/advisory/application/security approval.",
    }
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages-dir", type=Path, required=True)
    parser.add_argument("--arch", choices=sorted(ARCHES), required=True)
    parser.add_argument("--evidence-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        install(args.packages_dir.absolute(), args.arch, args.evidence_file.absolute())
    except (
        BuildError,
        OSError,
        ValueError,
        KeyError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(f"Owned Alpine installation failed: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
