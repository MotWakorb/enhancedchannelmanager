"""Alpine equivalents for distribution-specific runtime metadata assertions.

The shared behavioral suite and its separate source-integrity gate remain
required. This module does not provide source or advisory clearance.
"""

from __future__ import annotations

import hashlib
import json
import platform
import stat
import subprocess
from pathlib import Path, PurePosixPath

SECURITY_RECIPE_SHA256 = (
    "4940090f0dd959262a986933a43856279f5f33bda1c37980371939cf183f7bfa"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def missing_identity() -> dict:
    result = subprocess.run(
        ["getent", "passwd", "ecm-missing-user-for-port-check"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return {"exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def getent_metadata(command, user: list[str], group: list[str]) -> dict:
    assert command("getent", "passwd", user[2]).strip() == ":".join(user), (
        "numeric passwd identity differs"
    )
    assert command("getent", "group", group[2]).strip() == ":".join(group), (
        "numeric group identity differs"
    )
    missing = missing_identity()
    assert missing == {"exit": 2, "stdout": "", "stderr": ""}, (
        "missing identity exit/output contract differs"
    )
    ownership = command("/sbin/apk", "info", "--who-owns", "/usr/bin/getent").strip()
    assert ownership.startswith("/usr/bin/getent is owned by musl-utils-"), (
        "getent is not the official musl identity tool"
    )
    return {
        "getent_backend": "musl-utils",
        "getent_owner": ownership,
        "getent_missing_identity": missing,
    }


def verify_owned(root: Path, rows: list[dict]) -> None:
    assert rows and len({row["path"] for row in rows}) == len(rows), (
        "incomplete owned security inventory"
    )
    for row in rows:
        relative = PurePosixPath(row["path"])
        assert not relative.is_absolute() and ".." not in relative.parts, (
            "unsafe owned inventory path"
        )
        path = root / relative
        assert path.resolve().is_relative_to(root.resolve()), (
            "owned artifact escapes root"
        )
        info = path.lstat()
        if row["type"] == "file":
            assert (
                stat.S_ISREG(info.st_mode)
                and info.st_size == row["size"]
                and stat.S_IMODE(info.st_mode) == row["mode"]
                and sha256(path) == row["sha256"]
            ), "owned file bytes/mode differ: " + row["path"]
        else:
            assert (
                row["type"] == "symlink"
                and stat.S_ISLNK(info.st_mode)
                and str(path.readlink()) == row["target"]
                and sha256(path) == row["target_sha256"]
            ), "owned library link differs: " + row["path"]


def security_runtime_packages(command, root: Path = Path("/")) -> dict:
    release = (root / "etc/alpine-release").read_text().strip()
    assert release.startswith("3.24."), "unexpected Alpine runtime branch"
    repos = [
        line.strip()
        for line in (root / "etc/apk/repositories").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert repos == [
        "https://dl-cdn.alpinelinux.org/alpine/v3.24/main",
        "https://dl-cdn.alpinelinux.org/alpine/v3.24/community",
    ], "non-stable runtime repositories"
    package = json.loads(
        (root / "usr/share/ecm/provenance/native-package.json").read_text()
    )
    assert (
        package["status"] == "SIGNED_OWNED_APK_ONLY"
        and package["package"]["name"] == "ecm-owned-native"
    ), "unexpected native package identity"
    security = package["components"]["security"]
    assert security["recipe_sha256"] == SECURITY_RECIPE_SHA256, (
        "owned security source recipe differs"
    )
    path = root / "usr/share/ecm/provenance/owned/security.json"
    assert (
        path.is_file()
        and not path.is_symlink()
        and sha256(path) == security["receipt_sha256"]
    ), "owned security receipt differs from exact package"
    receipt = json.loads(path.read_text())
    assert (
        receipt["status"] == "BUILT_SECURITY_API_ONLY"
        and receipt["recipe_sha256"] == SECURITY_RECIPE_SHA256
    ), "owned security receipt subject differs"
    assert (
        receipt["architecture"]
        == {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
    ), "owned security architecture differs"
    expected = {
        "libxml2": ("2.13.9", "libxml2.so.2"),
        "speex": ("1.2.1", "libspeex.so.1"),
        "librsvg": ("2.63.4", "librsvg-2.so.2"),
    }
    assert {
        row["name"]: (row["version"], row["soname"]) for row in receipt["libraries"]
    } == expected, "security provider version/ABI set differs"
    verify_owned(root, receipt["owned_artifacts"])
    paths = ["/" + row["path"] for row in receipt["owned_artifacts"]]
    ownership = command("/sbin/apk", "info", "--who-owns", *paths).splitlines()
    assert len(ownership) == len(paths) and set(ownership) == {
        p + " is owned by ecm-owned-native-" + package["package"]["version"]
        for p in paths
    }, "owned native APK file ownership differs"
    installed = json.loads(
        command(
            "/sbin/apk",
            "query",
            "--from",
            "installed",
            "--format",
            "json",
            "--all-matches",
            "*",
        )
    )
    by_name = {row["name"]: row for row in installed}
    assert len(by_name) == len(installed), "duplicate installed APK identity"
    for name, origin in [
        ("ca-certificates", "ca-certificates"),
        ("ca-certificates-bundle", "ca-certificates"),
        ("brotli-libs", "brotli"),
    ]:
        assert name in by_name and by_name[name].get("origin") == origin, (
            "missing official runtime security provider: " + name
        )
    assert by_name["brotli-libs"]["version"].split("-r")[0] == "1.2.1", (
        "Brotli upstream version differs"
    )
    return {
        "distribution": "alpine",
        "release": release,
        "owned_security_recipe_sha256": SECURITY_RECIPE_SHA256,
        "owned_security_libraries": {
            name: {"version": version, "soname": soname}
            for name, (version, soname) in expected.items()
        },
        "official_runtime_packages": {
            name: by_name[name]
            for name in ["ca-certificates", "ca-certificates-bundle", "brotli-libs"]
        },
        "scope": "Actual installed owned-library bytes/links/APK ownership and official package origins only; complete source and advisory gates remain separate.",
    }
