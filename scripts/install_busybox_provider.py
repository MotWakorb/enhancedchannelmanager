#!/usr/bin/env python3
"""Install only the three approved BusyBox APKs in a disposable Alpine builder."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import platform
import stat
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

from alpine_apk_v2 import inspect_apk, parts
from alpine_repository_policy import validate_repositories
from build_alpine_codecs import validate_platform
from build_busybox_provider import MANIFEST_SHA256, read_policy, validate_payload
from build_native_runtime import (
    ARCHES,
    BuildError,
    _require,
    owned_artifacts,
    run,
    sha256,
)
from install_alpine_native import temporary_trust

__all__ = [
    "MANIFEST_SHA256",
    "BuildError",
    "install",
    "load_packages",
    "validate_contents",
    "verify_payload",
]


def verify_payload(root: Path, rows: list[dict]) -> None:
    _require(
        rows and len({row["path"] for row in rows}) == len(rows),
        "unique complete BusyBox payload required",
    )
    for row in rows:
        relative = PurePosixPath(row["path"])
        _require(
            not relative.is_absolute()
            and ".." not in relative.parts
            and str(relative) == row["path"],
            "unsafe installed BusyBox path",
        )
        path = root / relative
        _require(
            path.parent.resolve().is_relative_to(root.resolve()),
            "BusyBox payload parent escapes runtime root",
        )
        metadata = path.lstat()
        _require(
            metadata.st_uid == row["uid"]
            and metadata.st_gid == row["gid"]
            and stat.S_IMODE(metadata.st_mode) == row["mode"],
            "installed BusyBox metadata mismatch: " + row["path"],
        )
        if row["type"] == "file":
            _require(
                stat.S_ISREG(metadata.st_mode)
                and metadata.st_size == row["size"]
                and sha256(path) == row["sha256"],
                "installed BusyBox bytes mismatch: " + row["path"],
            )
        elif row["type"] == "directory":
            _require(
                stat.S_ISDIR(metadata.st_mode), "installed BusyBox directory mismatch"
            )
        else:
            _require(
                row["type"] == "symlink"
                and stat.S_ISLNK(metadata.st_mode)
                and str(path.readlink()) == row["target"],
                "installed BusyBox symlink mismatch",
            )


def validate_contents(text: str, name: str, version: str, rows: list[dict]) -> None:
    lines = text.splitlines()
    _require(
        lines and lines[0] == name + "-" + version + " contains:",
        "BusyBox APK contents header mismatch",
    )
    actual = [line for line in lines[1:] if line]
    expected = {row["path"] for row in rows if row["type"] != "directory"}
    _require(
        len(actual) == len(expected) and set(actual) == expected,
        "BusyBox APK contents mismatch",
    )


def verify_control_scripts(path: Path, expected: dict[str, dict]) -> list[dict]:
    observed = []
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            if not member.name.startswith(("busybox-", "ssl_client-")):
                continue
            _require(
                member.name in expected and member.isreg(),
                "unexpected installed BusyBox control script",
            )
            data = archive.extractfile(member)
            _require(data is not None, "unreadable installed BusyBox control script")
            row = {
                "path": expected[member.name]["path"],
                "mode": member.mode,
                "uid": member.uid,
                "gid": member.gid,
                "size": member.size,
                "sha256": hashlib.sha256(data.read()).hexdigest(),
            }
            _require(
                row == expected[member.name],
                "installed BusyBox control script mismatch",
            )
            observed.append({"installed_name": member.name, **row})
    _require(
        len(observed) == len(expected)
        and {row["installed_name"] for row in observed} == set(expected),
        "installed BusyBox control script set mismatch",
    )
    return observed


def load_packages(directory: Path, arch: str, manifest: Path) -> dict:
    recipe = read_policy(manifest)
    receipt = directory / "build-output.json"
    _require(
        receipt.is_file() and not receipt.is_symlink(),
        "regular BusyBox build receipt required",
    )
    record = json.loads(receipt.read_text())
    _require(
        record["status"] == "BUILT_SIGNED_BUSYBOX_ONLY"
        and record["architecture"] == arch
        and record["recipe_sha256"] == MANIFEST_SHA256
        and record["package_version"] == recipe["package_version"],
        "approved BusyBox build subject mismatch",
    )
    _require(
        [row["name"] for row in record["runtime_packages"]]
        == recipe["runtime_package_names"],
        "exact approved BusyBox runtime selection required",
    )
    public = record["public_key"]
    _require(
        public["file"] == "ecm-busybox.rsa.pub"
        and (directory / public["file"]).is_file()
        and not (directory / public["file"]).is_symlink()
        and sha256(directory / public["file"]) == public["sha256"],
        "BusyBox public-key evidence mismatch",
    )
    packages = directory / "runtime-packages"
    _require(
        packages.is_dir()
        and not packages.is_symlink()
        and {p.name for p in packages.iterdir()}
        == {row["file"] for row in record["runtime_packages"]},
        "unselected BusyBox runtime archive present",
    )
    group = recipe["architecture_inputs"][ARCHES[arch][0]]
    for row in record["runtime_packages"]:
        _require(
            row["file"] == row["name"] + "-" + recipe["package_version"] + ".apk",
            "BusyBox package basename mismatch",
        )
        path = packages / row["file"]
        _require(
            path.is_file() and not path.is_symlink() and sha256(path) == row["sha256"],
            "exact selected BusyBox APK hash mismatch",
        )
        actual = inspect_apk(path)
        original = next(
            p for p in group["signed_packages"] if p["index_record"]["P"] == row["name"]
        )
        _require(
            actual["fields"]["pkgname"] == [row["name"]]
            and actual["fields"]["pkgver"] == [recipe["package_version"]]
            and actual["fields"]["arch"] == [original["control_architecture"]]
            and actual["fields"]["origin"] == ["busybox"]
            and actual["fields"]["license"] == ["GPL-2.0-only"]
            and actual["payload"] == row["installed_payload"]
            and actual["control_scripts"]
            == row["control_scripts"]
            == original["original_control_scripts"]
            and actual["fields"].get("triggers", []) == original["original_triggers"],
            "BusyBox payload/control identity mismatch",
        )
        validate_payload(group, row["name"], actual["payload"])
    _require(
        record["source_files"] == owned_artifacts(directory / "sources"),
        "BusyBox delivered source inventory mismatch",
    )
    check = json.loads((directory / "check-output.json").read_text())
    busybox = next(p for p in record["runtime_packages"] if p["name"] == "busybox")
    binary = next(
        row for row in busybox["installed_payload"] if row["path"] == "bin/busybox"
    )
    lines = (directory / "check.log").read_bytes().splitlines()
    _require(
        check["status"] == "UPSTREAM_CHECKS_COMPLETED"
        and check["recipe_sha256"] == MANIFEST_SHA256
        and check["log_sha256"] == sha256(directory / "check.log")
        and check["binary_sha256"] == binary["sha256"]
        and check["helper_sha256"]
        == sha256(directory / "sources/build_busybox_provider.py")
        and not any(line.startswith(b"FAIL:") for line in lines)
        and sum(line.startswith(b"PASS:") for line in lines)
        == record["upstream_pass_lines"]
        and check["pass_lines"] == record["upstream_pass_lines"] > 0,
        "BusyBox completed check receipt mismatch",
    )
    return record


def damage_control(source: Path, target: Path) -> None:
    streams = parts(source)
    control = gzip.decompress(streams[1])
    witness = b"pkgname = busybox\n"
    _require(
        control.count(witness) == 1 and not target.exists() and not target.is_symlink(),
        "signed BusyBox negative-control witness mismatch",
    )
    changed = control.replace(witness, b"pkgname = cusybox\n", 1)
    with target.open("xb") as output:
        output.write(streams[0] + gzip.compress(changed, mtime=0) + streams[2])


def validate_tamper_denial(code: int, stdout: str, stderr: str) -> None:
    # APKv2 distinguishes an invalid signature from an unknown key. The
    # original packages must have passed with this same scoped key directory.
    lines = (stdout + stderr).splitlines()
    _require(
        code == 1 and len(lines) == 1 and lines[0].endswith(": BAD signature"),
        "BusyBox authenticated control mutation was not rejected by signature verification: "
        + repr({"exit": code, "stdout": stdout, "stderr": stderr}),
    )


def install(directory: Path, arch: str, manifest: Path, evidence: Path) -> dict:
    release = Path("/etc/alpine-release").read_text()
    validate_platform(release, platform.machine(), arch)
    validate_repositories(release, Path("/etc/apk/repositories").read_text())
    _require(
        not evidence.exists() and not evidence.is_symlink(),
        "BusyBox installation evidence must be fresh",
    )
    record = load_packages(directory, arch, manifest)
    public = directory / record["public_key"]["file"]
    packages = [
        directory / "runtime-packages" / row["file"]
        for row in record["runtime_packages"]
    ]
    trust_paths = [Path("/etc/apk/keys"), Path("/lib/apk/keys")]
    before = {str(path): owned_artifacts(path) for path in trust_paths}
    repositories = Path("/etc/apk/repositories").read_bytes()
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": "/tmp",
        "LC_ALL": "C",
        "TZ": "UTC",
    }
    denials = []
    for path in packages:
        process = subprocess.run(
            ["/sbin/apk", "verify", "--network=no", str(path)],
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=60,
        )
        _require(
            process.returncode != 0
            and "UNTRUSTED signature" in process.stdout + process.stderr,
            "owned BusyBox APK unexpectedly trusted by runtime keys",
        )
        denials.append(process.stdout + process.stderr)
    try:
        with temporary_trust(
            trust_paths, public, Path("/tmp"), key_name="ecm-busybox.rsa.pub"
        ) as trust:
            verification = run(
                [
                    "/sbin/apk",
                    "verify",
                    "--network=no",
                    "--keys-dir",
                    str(trust),
                    *[str(p) for p in packages],
                ],
                cwd=directory,
                env=env,
            )
            damaged = trust / "damaged.apk"
            damage_control(packages[0], damaged)
            negative = subprocess.run(
                [
                    "/sbin/apk",
                    "verify",
                    "--network=no",
                    "--keys-dir",
                    str(trust),
                    str(damaged),
                ],
                capture_output=True,
                text=True,
                check=False,
                env=env,
                timeout=60,
            )
            validate_tamper_denial(
                negative.returncode, negative.stdout, negative.stderr
            )
            installation = run(
                [
                    "/sbin/apk",
                    "add",
                    "--network=no",
                    "--keys-dir",
                    str(trust),
                    *[str(p) for p in packages],
                ],
                cwd=directory,
                env=env,
            )
    finally:
        _require(
            before == {str(path): owned_artifacts(path) for path in trust_paths},
            "BusyBox installation changed runtime trusted keys",
        )
        _require(
            Path("/etc/apk/repositories").read_bytes() == repositories,
            "BusyBox installation changed runtime repositories",
        )
    _require(not trust.exists(), "temporary BusyBox public-key trust retained")
    contents, ownership = {}, {}
    expected_scripts = {}
    for row in record["runtime_packages"]:
        # Alpine's installed script name includes the authenticated compressed
        # control-stream SHA1, not the mutable package version alone.
        control_digest = hashlib.sha1(
            parts(directory / "runtime-packages" / row["file"])[1]
        ).hexdigest()
        for script in row["control_scripts"]:
            expected_scripts[
                row["name"]
                + "-"
                + record["package_version"]
                + ".X1"
                + control_digest
                + script["path"]
            ] = script
        verify_payload(Path("/"), row["installed_payload"])
        text = run(
            ["/sbin/apk", "info", "--contents", row["name"]], cwd=directory, env=env
        )
        validate_contents(
            text, row["name"], record["package_version"], row["installed_payload"]
        )
        contents[row["name"]] = text
        paths = [
            "/" + r["path"]
            for r in row["installed_payload"]
            if r["type"] != "directory"
        ]
        text = run(["/sbin/apk", "info", "--who-owns", *paths], cwd=directory, env=env)
        expected = {
            p + " is owned by " + row["name"] + "-" + record["package_version"]
            for p in paths
        }
        _require(
            len(text.splitlines()) == len(expected)
            and set(text.splitlines()) == expected,
            "installed BusyBox package ownership mismatch",
        )
        ownership[row["name"]] = text
    inventory = json.loads(
        run(
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
            cwd=directory,
            env=env,
        )
    )
    origin = {
        p["name"]: p["version"] for p in inventory if p.get("origin") == "busybox"
    }
    _require(
        origin
        == {r["name"]: record["package_version"] for r in record["runtime_packages"]},
        "unselected or old BusyBox source package remains installed",
    )
    installed_scripts = verify_control_scripts(
        Path("/lib/apk/db/scripts.tar.gz"), expected_scripts
    )
    result = {
        "status": "SIGNED_BUSYBOX_INSTALLED_ONLY",
        "architecture": arch,
        "recipe_sha256": MANIFEST_SHA256,
        "package_receipt_sha256": sha256(directory / "build-output.json"),
        "runtime_packages": record["runtime_packages"],
        "public_key": record["public_key"],
        "default_trust_denials": denials,
        "signature_verification": verification,
        "changed_control_denial": negative.stdout + negative.stderr,
        "installation": installation,
        "contents": contents,
        "ownership": ownership,
        "installed_control_scripts": installed_scripts,
        "installed_packages": inventory,
        "runtime_trusted_keys": before,
        "repositories_sha256": sha256(Path("/etc/apk/repositories")),
        "temporary_trust_removed": True,
        "scope": "Exact three signed BusyBox APKs and installed bytes/modes/UID/GID/links/ownership only. Static utmps/skalibs source, advisory and full-image approval remain separate.",
    }
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages-dir", type=Path, required=True)
    parser.add_argument("--arch", choices=["amd64", "arm64"], required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--evidence-file", type=Path, required=True)
    args = parser.parse_args()
    install(
        args.packages_dir.absolute(),
        args.arch,
        args.manifest.absolute(),
        args.evidence_file.absolute(),
    )


if __name__ == "__main__":
    main()
