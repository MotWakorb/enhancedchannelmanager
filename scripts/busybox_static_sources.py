"""Two official BusyBox static SDK providers: exact signed-byte/source bindings."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

from alpine_apk_v2 import inspect_apk
from build_native_runtime import (
    BuildError,
    _require,
    owned_artifacts,
    safe_extract,
    sha256,
)
from verify_musllinux_gnu import apk_payload

__all__ = [
    "STATIC_SOURCES_SHA256",
    "BuildError",
    "load_sources",
    "verify_package",
    "verify_sdk",
    "verify_source",
    "verify_static_inputs",
]
STATIC_SOURCES_SHA256 = (
    "4b7bda7103059c19fad7d84a054a6b234eedb3db0d875976f969534af3b55887"
)


def load_sources(path: Path) -> dict:
    _require(
        path.is_file()
        and not path.is_symlink()
        and sha256(path) == STATIC_SOURCES_SHA256,
        "reviewed BusyBox static-source graph identity mismatch",
    )
    recipe = json.loads(path.read_text())
    for component in recipe["components"]:
        for row in [component["source"], component["aports"], component["notice"]]:
            source = path.parent / row["file"]
            _require(
                source.is_file()
                and not source.is_symlink()
                and sha256(source) == row["sha256"],
                "BusyBox static source/recipe/notice identity mismatch",
            )
    for rows in recipe["architecture_inputs"].values():
        for row in rows:
            source = path.parent / row["artifact"]["file"]
            _require(
                source.is_file()
                and not source.is_symlink()
                and sha256(source) == row["artifact"]["sha256"],
                "BusyBox static provenance APK identity mismatch",
            )
    return recipe


def verify_source(assets: Path, component: dict, work: Path) -> None:
    safe_extract(assets / component["aports"]["file"], work)
    directory = work / component["name"]
    original = directory / "APKBUILD"
    _require(
        sha256(original) == component["aports"]["original_recipe_sha256"],
        "BusyBox static original recipe mismatch",
    )
    rows = re.findall(r"^([0-9a-f]{128})  (.+)$", original.read_text(), re.MULTILINE)
    _require(
        len(rows) == component["aports"]["source_input_checksums"],
        "complete static provider source input set required",
    )
    for expected, name in rows:
        path = (
            assets / component["source"]["file"]
            if name == component["source"]["filename"]
            else directory / name
        )
        _require(
            path.is_file()
            and not path.is_symlink()
            and hashlib.sha512(path.read_bytes()).hexdigest() == expected,
            "static provider source input SHA512 mismatch",
        )
    with tarfile.open(assets / component["source"]["file"]) as archive:
        copies = [
            m
            for m in archive
            if m.name == component["name"] + "-" + component["version"] + "/COPYING"
        ]
        _require(
            len(copies) == 1 and copies[0].isreg(),
            "static provider original notice missing",
        )
        source = archive.extractfile(copies[0])
        _require(
            source is not None
            and hashlib.sha256(source.read()).hexdigest()
            == component["notice"]["sha256"],
            "static provider notice differs from actual upstream archive",
        )


def verify_package(
    assets: Path, row: dict, components: list[dict], machine: str
) -> None:
    index = row["index_record"]
    matches = [p for p in components if p["name"] == index["o"]]
    _require(len(matches) == 1, "unknown static provider source origin")
    component = matches[0]
    _require(
        index["P"] in {component["name"] + "-static", component["name"] + "-dev"}
        and index["c"] == component["aports"]["commit"]
        and index["V"] == component["package_version"]
        and index["A"] == machine,
        "static provider signed source/version/architecture mismatch",
    )
    path = assets / row["artifact"]["file"]
    apk_payload(path.read_bytes(), index)
    actual = inspect_apk(path)
    _require(
        actual["fields"]["origin"] == [component["name"]]
        and actual["payload"] == row["payload"],
        "static provider actual SDK/archive payload mismatch",
    )


def verify_sdk(root: Path, rows: list[dict]) -> None:
    for row in rows:
        name = PurePosixPath(row["path"])
        _require(
            not name.is_absolute() and ".." not in name.parts, "unsafe static SDK path"
        )
        path = root / name
        _require(
            path.parent.resolve().is_relative_to(root.resolve()),
            "static SDK parent escape",
        )
        info = path.lstat()
        _require(
            info.st_uid == row["uid"]
            and info.st_gid == row["gid"]
            and stat.S_IMODE(info.st_mode) == row["mode"],
            "installed static SDK metadata mismatch",
        )
        if row["type"] == "file":
            _require(
                stat.S_ISREG(info.st_mode)
                and info.st_size == row["size"]
                and sha256(path) == row["sha256"],
                "installed static SDK/archive bytes mismatch",
            )
        elif row["type"] == "directory":
            _require(
                stat.S_ISDIR(info.st_mode), "installed static SDK directory mismatch"
            )
        else:
            _require(
                row["type"] == "symlink"
                and stat.S_ISLNK(info.st_mode)
                and str(path.readlink()) == row["target"],
                "installed static SDK link mismatch",
            )


def verify_delivery(directory: Path) -> dict:
    manifest = directory / "static-sources.json"
    _require(
        manifest.is_file()
        and not manifest.is_symlink()
        and sha256(manifest) == STATIC_SOURCES_SHA256,
        "delivered static source graph mismatch",
    )
    recipe = json.loads(manifest.read_text())
    for component in recipe["components"]:
        target = directory / component["name"]
        for path, expected in [
            (target / component["source"]["filename"], component["source"]["sha256"]),
            (target / "COPYING", component["notice"]["sha256"]),
            (target / "aports/APKBUILD", component["aports"]["original_recipe_sha256"]),
        ]:
            _require(
                path.is_file() and not path.is_symlink() and sha256(path) == expected,
                "delivered static source/recipe/notice mismatch",
            )
        rows = re.findall(
            r"^([0-9a-f]{128})  (.+)$",
            (target / "aports/APKBUILD").read_text(),
            re.MULTILINE,
        )
        _require(
            len(rows) == component["aports"]["source_input_checksums"],
            "delivered static source input coverage mismatch",
        )
        for expected, name in rows:
            path = (
                target / name
                if name == component["source"]["filename"]
                else target / "aports" / name
            )
            _require(
                path.is_file()
                and not path.is_symlink()
                and hashlib.sha512(path.read_bytes()).hexdigest() == expected,
                "delivered static source input checksum mismatch",
            )
    return recipe


def verify_static_inputs(
    manifest: Path, policy: dict, machine: str, work: Path, delivery: Path, log: Path
) -> dict:
    recipe = load_sources(manifest)
    work.mkdir()
    delivery.mkdir()
    assets = manifest.parent
    group = policy["architecture_inputs"][machine]
    index = assets / group["index"]["file"]
    _require(
        sha256(index) == group["index"]["sha256"],
        "static source signed index identity mismatch",
    )
    with tarfile.open(index) as archive:
        signers = [
            m.name.removeprefix(".SIGN.RSA.")
            for m in archive
            if m.name.startswith(".SIGN.RSA.")
        ]
        raw = archive.extractfile("APKINDEX")
        _require(raw is not None, "static source index missing")
        paragraphs = raw.read().decode().split("\n\n")
    _require(
        len(signers) == 1
        and sha256(Path("/usr/share/apk/keys") / machine / signers[0])
        == group["signer_key_sha256"],
        "static source official signing key mismatch",
    )
    index_rows = [
        dict(
            line.split(":", 1)
            for line in paragraph.splitlines()
            if len(line) > 1 and line[1] == ":"
        )
        for paragraph in paragraphs
    ]
    packages = recipe["architecture_inputs"][machine]
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "LC_ALL": "C",
        "TZ": "UTC",
    }
    with log.open("wb") as output:
        subprocess.run(
            [
                "/sbin/apk",
                "verify",
                "--network=no",
                "--keys-dir",
                "/usr/share/apk/keys/" + machine,
                str(index),
            ],
            check=True,
            stdout=output,
            stderr=subprocess.STDOUT,
            env=env,
        )
        for row in packages:
            _require(
                sum(p == row["index_record"] for p in index_rows) == 1,
                "static provider does not match the actual signed index",
            )
            path = assets / row["artifact"]["file"]
            subprocess.run(
                [
                    "/sbin/apk",
                    "verify",
                    "--network=no",
                    "--keys-dir",
                    "/usr/share/apk/keys/" + machine,
                    str(path),
                ],
                check=True,
                stdout=output,
                stderr=subprocess.STDOUT,
                env=env,
            )
            verify_package(assets, row, recipe["components"], machine)
            verify_sdk(Path("/"), row["payload"])
            filenames = [
                "/" + p["path"] for p in row["payload"] if p["type"] != "directory"
            ]
            owners = subprocess.run(
                ["/sbin/apk", "info", "--who-owns", *filenames],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            ).stdout.splitlines()
            expected = {
                name
                + " is owned by "
                + row["index_record"]["P"]
                + "-"
                + row["index_record"]["V"]
                for name in filenames
            }
            _require(
                len(owners) == len(expected) and set(owners) == expected,
                "installed static SDK/archive package ownership mismatch",
            )
    for component in recipe["components"]:
        verify_source(assets, component, work / component["name"])
        target = delivery / component["name"]
        target.mkdir()
        shutil.copytree(work / component["name"] / component["name"], target / "aports")
        shutil.copy2(
            assets / component["source"]["file"],
            target / component["source"]["filename"],
        )
        shutil.copy2(assets / component["notice"]["file"], target / "COPYING")
    shutil.copy2(manifest, delivery / "static-sources.json")
    receipt = {
        "status": "TWO_OFFICIAL_STATIC_PROVIDERS_SOURCE_BOUND",
        "static_sources_sha256": STATIC_SOURCES_SHA256,
        "machine": machine,
        "packages": [p["index_record"] for p in packages],
        "static_archives": [
            item
            for row in packages
            for item in row["payload"]
            if item["path"].endswith(".a")
        ],
        "source_files": owned_artifacts(delivery),
        "scope": "Signed official utmps/skalibs SDK and static-archive bytes bound to corresponding sources/notices. Not independent rebuild equivalence, compiler-support source closure or advisory clearance.",
    }
    return receipt
