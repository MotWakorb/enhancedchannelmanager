#!/usr/bin/env python3
"""Build the approved BusyBox backport without changing applets or test selection."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from alpine_apk_v2 import inspect_apk
from alpine_repository_policy import validate_repositories
from build_alpine_codecs import validate_platform
from build_native_runtime import (
    ARCHES,
    BuildError,
    _require,
    owned_artifacts,
    safe_extract,
    sha256,
)
from verify_musllinux_gnu import apk_payload

__all__ = [
    "BuildError",
    "config_settings",
    "derive_apkbuild",
    "load_manifest",
    "safe_extract",
]
MANIFEST_SHA256 = "fc2e13d620c9c8c339b4474ec72d27dc6943d5173986372dc53e421a7072d4ae"


def read_policy(path: Path) -> dict:
    _require(
        path.is_file() and not path.is_symlink() and sha256(path) == MANIFEST_SHA256,
        "approved BusyBox manifest identity mismatch",
    )
    return json.loads(path.read_text())


def load_manifest(path: Path) -> dict:
    recipe = read_policy(path)
    for record in [recipe["aports"], *recipe["patches"]]:
        source = path.parent / record["file"]
        _require(
            source.is_file()
            and not source.is_symlink()
            and sha256(source) == record["sha256"],
            "approved BusyBox source/patch identity mismatch",
        )
    for group in recipe["architecture_inputs"].values():
        for record in [
            group["index"],
            *[p["artifact"] for p in group["signed_packages"]],
        ]:
            source = path.parent / record["file"]
            _require(
                source.is_file()
                and not source.is_symlink()
                and sha256(source) == record["sha256"],
                "signed original BusyBox provenance-input identity mismatch",
            )
    return recipe


def validate_payload(group: dict, name: str, actual: list[dict]) -> None:
    original = next(
        p for p in group["signed_packages"] if p["index_record"]["P"] == name
    )
    expected = {row["path"]: row for row in original["original_payload"]}
    observed = {row["path"]: row for row in actual}
    _require(
        len(observed) == len(actual) and observed.keys() == expected.keys(),
        "original BusyBox runtime namespace changed",
    )
    for path, row in observed.items():
        if path in {"bin/busybox", "usr/bin/ssl_client"}:
            stripped = {
                key: value
                for key, value in row.items()
                if key not in {"sha256", "size"}
            }
            baseline = {
                key: value
                for key, value in expected[path].items()
                if key not in {"sha256", "size"}
            }
            _require(stripped == baseline, "original BusyBox binary metadata changed")
        else:
            _require(
                row == expected[path],
                "original BusyBox nonbinary payload changed: " + path,
            )


def derive_apkbuild(original: str, recipe: dict) -> str:
    _require(
        hashlib.sha256(original.encode()).hexdigest()
        == recipe["aports"]["original_recipe_sha256"],
        "original BusyBox recipe identity mismatch",
    )
    patches = recipe["patches"]
    additions = "".join("\t" + row["file"] + "\n" for row in patches)
    substitutions = [
        (
            "pkgver=1.37.0\n",
            'pkgver=1.37.0_p20261109\nbuilddir="$srcdir/busybox-1.37.0"\n',
        ),
        ("pkgrel=31\n", "pkgrel=0\n"),
        (
            'source="https://busybox.net/downloads/busybox-$pkgver.tar.bz2',
            'source="https://busybox.net/downloads/busybox-1.37.0.tar.bz2',
        ),
        ('\t$_extras_openrc_files\n"', "\t$_extras_openrc_files\n" + additions + '"'),
    ]
    derived = original
    for before, after in substitutions:
        _require(
            derived.count(before) == 1, "BusyBox recipe transformation witness mismatch"
        )
        derived = derived.replace(before, after)
    _require(derived.endswith('"\n'), "BusyBox source-checksum block witness mismatch")
    # abuild associates source/checksum rows by order. Preserve every existing
    # checksum and append the added patches in their source-list order.
    derived = (
        derived[:-2]
        + "".join(row["sha512"] + "  " + row["file"] + "\n" for row in patches)
        + '"\n'
    )
    _require(
        hashlib.sha256(derived.encode()).hexdigest()
        == recipe["aports"]["derived_recipe_sha256"],
        "derived BusyBox recipe identity mismatch",
    )
    return derived


def config_settings(text: str) -> dict:
    result = {}
    for line in text.splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
        elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
            key, value = line[2:-11], "n"
        else:
            continue
        _require(key not in result, "duplicate BusyBox configuration setting")
        result[key] = value
    return result


def environment(work: Path, output: Path, recipe: dict) -> dict:
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(work / "home"),
        "USER": "root",
        "LOGNAME": "root",
        "LC_ALL": "C",
        "TZ": "UTC",
        "SOURCE_DATE_EPOCH": str(recipe["source_date_epoch"]),
        "REPODEST": str(output / "packages"),
        "PACKAGER": "ECM owned BusyBox security backport",
    }


def check_environment(work: Path, output: Path, recipe: dict) -> dict:
    # The unchanged upstream tree tests expect their original Unicode glyphs.
    # Compilation remains locale-C; no expected test output is rewritten.
    return {**environment(work, output, recipe), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


def logged(command: list[str], log: Path, cwd: Path, env: dict) -> None:
    # Raw test output includes binary byte tests; never decode/rewrite it.
    with log.open("wb") as stream:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=False,
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
    if result.returncode:
        sys.stdout.buffer.write(log.read_bytes())
        sys.stdout.buffer.flush()
        raise subprocess.CalledProcessError(result.returncode, command)


def verify_sources(work: Path, recipe: dict) -> None:
    for name, expected in recipe["prepared_source_sha256"].items():
        path = work / "recipe/src/busybox-1.37.0" / name
        _require(
            path.is_file() and not path.is_symlink() and sha256(path) == expected,
            "complete BusyBox prepared-source witness mismatch: " + name,
        )


def verify_original_inputs(
    manifest: Path, machine: str, recipe: dict, log: Path
) -> None:
    group = recipe["architecture_inputs"][machine]
    index = manifest.parent / group["index"]["file"]
    with tarfile.open(index, "r:gz") as archive:
        signatures = [
            member.name for member in archive if member.name.startswith(".SIGN.RSA.")
        ]
    _require(len(signatures) == 1, "one original Alpine index signer required")
    key = (
        Path("/usr/share/apk/keys") / machine / signatures[0].removeprefix(".SIGN.RSA.")
    )
    _require(
        key.is_file() and sha256(key) == group["signer_key_sha256"],
        "original official image-supplied signer identity mismatch",
    )
    paths = [index] + [
        manifest.parent / p["artifact"]["file"] for p in group["signed_packages"]
    ]
    with log.open("wb") as stream:
        for path in paths:
            subprocess.run(
                [
                    "/sbin/apk",
                    "verify",
                    "--network=no",
                    "--keys-dir",
                    str(key.parent),
                    str(path),
                ],
                check=True,
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
    for package in group["signed_packages"]:
        expected = dict(package["index_record"])
        _require(
            expected["c"] == recipe["aports"]["commit"]
            and expected["o"] == "busybox"
            and expected["P"] in recipe["runtime_package_names"],
            "signed BusyBox source-origin/commit mismatch",
        )
        # Alpine's abuild index explicitly rewrites noarch entries to the
        # repository architecture. Preserve both raw signed fields in the map.
        expected["A"] = package["control_architecture"]
        _require(
            expected["A"] == machine
            or expected["P"] == "busybox-binsh"
            and expected["A"] == "noarch",
            "unexpected original package control architecture",
        )
        apk_payload(
            (manifest.parent / package["artifact"]["file"]).read_bytes(), expected
        )


def prepare_build(
    manifest: Path, arch: str, work: Path, output: Path, cache: Path
) -> None:
    recipe = load_manifest(manifest)
    release = Path("/etc/alpine-release").read_text()
    validate_platform(release, platform.machine(), arch)
    validate_repositories(release, Path("/etc/apk/repositories").read_text())
    _require(
        all(not p.exists() and not p.is_symlink() for p in [work, output]),
        "BusyBox build/output locations must be fresh",
    )
    _require(
        not any(c.isspace() for p in [work, output] for c in str(p)),
        "BusyBox build locations may not contain shell word separators",
    )
    for source in [cache, manifest.parent]:
        for destination in [work, output]:
            _require(
                not source.resolve().is_relative_to(destination.resolve())
                and not destination.resolve().is_relative_to(source.resolve()),
                "BusyBox input/output locations must be disjoint",
            )
    work.mkdir(parents=True)
    output.mkdir(parents=True)
    verify_original_inputs(
        manifest, platform.machine(), recipe, output / "original-signatures.log"
    )
    (work / "home").mkdir()
    safe_extract(manifest.parent / recipe["aports"]["file"], work / "original-aports")
    original = work / "original-aports/busybox"
    checks = re.findall(
        r"^([0-9a-f]{128})  (.+)$", (original / "APKBUILD").read_text(), re.MULTILINE
    )
    _require(
        len(checks) == recipe["aports"]["source_input_checksums"],
        "complete BusyBox vendor input checksums required",
    )
    archive = cache / recipe["source"]["sha256"]
    _require(
        archive.is_file()
        and not archive.is_symlink()
        and sha256(archive) == recipe["source"]["sha256"],
        "BusyBox source cache identity mismatch",
    )
    for expected, name in checks:
        source = archive if name == recipe["source"]["filename"] else original / name
        _require(
            source.is_file()
            and not source.is_symlink()
            and hashlib.sha512(source.read_bytes()).hexdigest() == expected,
            "BusyBox original source-input checksum mismatch: " + name,
        )
    directory = work / "recipe"
    shutil.copytree(original, directory)
    for patch in recipe["patches"]:
        shutil.copy2(manifest.parent / patch["file"], directory / patch["file"])
    (directory / "APKBUILD").write_text(
        derive_apkbuild((original / "APKBUILD").read_text(), recipe)
    )
    distfiles = work / "distfiles"
    distfiles.mkdir()
    shutil.copy2(archive, distfiles / recipe["source"]["filename"])
    env = environment(work, output, recipe)
    logged(
        ["abuild", "-F", "-K", "-s", str(distfiles), "fetch", "unpack", "prepare"],
        output / "prepare.log",
        directory,
        env,
    )
    verify_sources(work, recipe)
    logged(["abuild", "-F", "-K", "build"], output / "compile.log", directory, env)
    verify_sources(work, recipe)
    built = config_settings((directory / "src/build-dynamic/.config").read_text())
    expected = config_settings((original / "busyboxconfig").read_text())
    _require(
        built == expected and len(built) == recipe["configuration_settings"],
        "original BusyBox configuration surface changed",
    )


def check(manifest: Path, work: Path, output: Path) -> None:
    recipe = load_manifest(manifest)
    verify_sources(work, recipe)
    _require(
        not (output / "check.log").exists()
        and not (output / "check-output.json").exists(),
        "BusyBox check evidence must be fresh",
    )
    logged(
        ["abuild", "-F", "-K", "check"],
        output / "check.log",
        work / "recipe",
        check_environment(work, output, recipe),
    )
    _require(
        not any(
            line.startswith(b"FAIL:")
            for line in (output / "check.log").read_bytes().splitlines()
        ),
        "BusyBox upstream check failure retained",
    )
    result = {
        "status": "UPSTREAM_CHECKS_COMPLETED",
        "recipe_sha256": MANIFEST_SHA256,
        "log_sha256": sha256(output / "check.log"),
        "binary_sha256": sha256(work / "recipe/src/build-dynamic/busybox"),
        "helper_sha256": sha256(Path(__file__)),
        "pass_lines": sum(
            line.startswith(b"PASS:")
            for line in (output / "check.log").read_bytes().splitlines()
        ),
    }
    _require(result["pass_lines"] > 0, "BusyBox upstream check result is empty")
    (output / "check-output.json").write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n"
    )


def require_checks(work: Path, output: Path) -> dict:
    path = output / "check-output.json"
    _require(
        path.is_file() and not path.is_symlink(),
        "completed BusyBox upstream check receipt required",
    )
    result = json.loads(path.read_text())
    _require(
        result["status"] == "UPSTREAM_CHECKS_COMPLETED"
        and result["recipe_sha256"] == MANIFEST_SHA256
        and result["log_sha256"] == sha256(output / "check.log")
        and result["binary_sha256"] == sha256(work / "recipe/src/build-dynamic/busybox")
        and result["helper_sha256"] == sha256(Path(__file__)),
        "BusyBox upstream check identity mismatch",
    )
    lines = (output / "check.log").read_bytes().splitlines()
    _require(
        not any(line.startswith(b"FAIL:") for line in lines)
        and sum(line.startswith(b"PASS:") for line in lines)
        == result["pass_lines"]
        > 0,
        "BusyBox upstream check result mismatch",
    )
    return result


def package(manifest: Path, arch: str, work: Path, output: Path) -> dict:
    recipe = load_manifest(manifest)
    verify_sources(work, recipe)
    require_checks(work, output)
    env = environment(work, output, recipe)
    trust_paths = [Path("/etc/apk/keys"), Path("/lib/apk/keys")]
    before = {str(path): owned_artifacts(path) for path in trust_paths}
    public = output / "ecm-busybox.rsa.pub"
    with tempfile.TemporaryDirectory(prefix="build-only-key-", dir=work) as temporary:
        keys = Path(temporary)
        private, pub = keys / "ecm-busybox.rsa", keys / "ecm-busybox.rsa.pub"
        subprocess.run(
            [
                "openssl",
                "genpkey",
                "-algorithm",
                "RSA",
                "-pkeyopt",
                "rsa_keygen_bits:3072",
                "-out",
                str(private),
            ],
            check=True,
        )
        private.chmod(0o600)
        subprocess.run(
            ["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(pub)],
            check=True,
        )
        shutil.copy2(pub, public)
        trust = keys / "trust"
        trust.mkdir()
        for path in Path("/etc/apk/keys").glob("*.pub"):
            (trust / path.name).write_bytes(path.read_bytes())
        shutil.copy2(pub, trust / pub.name)
        logged(
            ["abuild", "-F", "-K", "rootpkg", "index"],
            output / "package.log",
            work / "recipe",
            {
                **env,
                "PACKAGER_PRIVKEY": str(private),
                "APK": "/sbin/apk --keys-dir " + str(trust),
            },
        )
    _require(
        before == {str(path): owned_artifacts(path) for path in trust_paths},
        "BusyBox build changed runtime trust",
    )
    directory = work / "recipe"
    binary = directory / "src/build-dynamic/busybox"
    applets = subprocess.run(
        [str(binary), "--list-full"], check=True, capture_output=True
    ).stdout
    _require(
        hashlib.sha256(applets).hexdigest() == recipe["applet_paths_sha256"]
        and len(applets.splitlines()) == recipe["applet_count"],
        "original complete BusyBox applet surface changed",
    )
    (output / "built-applets.txt").write_bytes(applets)
    shutil.copy2(directory / "src/build-dynamic/.config", output / "built-config")
    sources = output / "sources"
    sources.mkdir()
    shutil.copy2(
        work / "distfiles" / recipe["source"]["filename"],
        sources / recipe["source"]["filename"],
    )
    shutil.copytree(work / "original-aports/busybox", sources / "original-aports")
    owned_recipe = sources / "owned-recipe"
    owned_recipe.mkdir()
    shutil.copy2(manifest, owned_recipe / manifest.name)
    for record in [recipe["aports"], *recipe["patches"]]:
        target = owned_recipe / record["file"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(manifest.parent / record["file"], target)
    shutil.copy2(directory / "APKBUILD", sources / "derived-APKBUILD")
    shutil.copy2(Path(__file__), sources / "build_busybox_provider.py")
    selected = output / "runtime-packages"
    selected.mkdir()
    records = []
    for name in recipe["runtime_package_names"]:
        filename = name + "-" + recipe["package_version"] + ".apk"
        paths = list((output / "packages").rglob(filename))
        _require(len(paths) == 1, "exact BusyBox runtime package selection required")
        shutil.copy2(paths[0], selected / filename)
        payload = inspect_apk(selected / filename)
        group = recipe["architecture_inputs"][ARCHES[arch][0]]
        original = next(
            p for p in group["signed_packages"] if p["index_record"]["P"] == name
        )
        _require(
            payload["fields"].get("pkgname") == [name]
            and payload["fields"].get("pkgver") == [recipe["package_version"]]
            and payload["fields"].get("arch") == [original["control_architecture"]]
            and payload["control_scripts"] == original["original_control_scripts"]
            and payload["fields"].get("triggers", []) == original["original_triggers"],
            "original BusyBox package identity/control scripts changed",
        )
        validate_payload(group, name, payload["payload"])
        records.append(
            {
                "name": name,
                "file": filename,
                "sha256": sha256(selected / filename),
                "installed_payload": payload["payload"],
                "control_scripts": payload["control_scripts"],
            }
        )
    record = {
        "status": "BUILT_SIGNED_BUSYBOX_ONLY",
        "architecture": arch,
        "recipe_sha256": MANIFEST_SHA256,
        "package_version": recipe["package_version"],
        "runtime_packages": records,
        "public_key": {"file": public.name, "sha256": sha256(public)},
        "applets_sha256": hashlib.sha256(applets).hexdigest(),
        "configuration_settings": recipe["configuration_settings"],
        "upstream_pass_lines": sum(
            line.startswith(b"PASS:")
            for line in (output / "check.log").read_bytes().splitlines()
        ),
        "source_files": owned_artifacts(sources),
        "build_packages": subprocess.run(
            ["/sbin/apk", "info", "-v"], check=True, capture_output=True, text=True
        ).stdout,
        "static_dependency_source_status": "PENDING_SEPARATE_UTMPS_SKALIBS_CLOSURE",
        "scope": "Signed BusyBox backport, exact original applets/config and unchanged upstream checks only. No native installation, full static-source/advisory or whole-image approval.",
    }
    (output / "build-output.json").write_text(
        json.dumps(record, sort_keys=True, indent=2) + "\n"
    )
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare-build", "check", "package"])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--arch", choices=["amd64", "arm64"], required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path)
    args = parser.parse_args()
    manifest, work, output = (
        args.manifest.absolute(),
        args.work_dir.absolute(),
        args.output_dir.absolute(),
    )
    validate_platform(
        Path("/etc/alpine-release").read_text(), platform.machine(), args.arch
    )
    validate_repositories(
        Path("/etc/alpine-release").read_text(),
        Path("/etc/apk/repositories").read_text(),
    )
    if args.phase == "prepare-build":
        if args.cache_dir is None:
            parser.error("prepare-build requires an offline source cache")
        prepare_build(manifest, args.arch, work, output, args.cache_dir.absolute())
    elif args.phase == "check":
        check(manifest, work, output)
    else:
        print(json.dumps(package(manifest, args.arch, work, output), sort_keys=True))


if __name__ == "__main__":
    main()
