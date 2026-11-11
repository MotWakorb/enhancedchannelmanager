"""Verify one delivered external-APK/source graph, never full runtime clearance.

Read-only and offline: authenticate exact official indexes/APKs, join the supplied
runtime inventory and rederive every Git/source-input binding. No recipe or
target ELF is executed or archive extracted. Owned packages, Python base,
compiler/language support, licenses and advisories remain separate required gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

from alpine_source_inputs import (
    InputError,
    bind_apk_control,
    bind_git_recipe,
    recipe_tree,
    require,
    verify_source_inputs,
)
from alpine_source_inventory import InventoryError, source_plan

KEYS = {
    "aarch64": {
        "alpine-devel@lists.alpinelinux.org-58199dcc.rsa.pub": "73867d92083f2f8ab899a26ccda7ef63dfaa0032a938620eda605558958a8041",
        "alpine-devel@lists.alpinelinux.org-616ae350.rsa.pub": "d11f6b21c61b4274e182eb888883a8ba8acdbf820dcc7a6d82a7d9fc2fd2836d",
    },
    "x86_64": {
        "alpine-devel@lists.alpinelinux.org-4a6a0840.rsa.pub": "9c102bcc376af1498d549b77bdbfa815ae86faa1d2d82f040e616b18ef2df2d4",
        "alpine-devel@lists.alpinelinux.org-5261cecb.rsa.pub": "12f899e55a7691225603d6fb3324940fc51cd7f133e7ead788663c2b7eecb00c",
        "alpine-devel@lists.alpinelinux.org-6165ee59.rsa.pub": "207e4696d3c05f7cb05966aee557307151f1f00217af4143c1bcaf33b8df733f",
    },
}
OWNED_PENDING = frozenset(
    {"busybox", "busybox-binsh", "ssl_client", "ecm-owned-native"}
)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def unique(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate source-delivery JSON key")
        result[key] = value
    return result


def path_in(root: Path, name: str) -> Path:
    require(
        isinstance(name, str) and bool(name) and all(ord(c) >= 32 for c in name),
        "invalid source delivery path",
    )
    relative = PurePosixPath(name)
    require(
        not relative.is_absolute()
        and str(relative) == name
        and ".." not in relative.parts
        and "\\" not in name,
        "unsafe source delivery path",
    )
    path = root / relative
    require(
        path.resolve().is_relative_to(root.resolve()), "source delivery path escape"
    )
    require(
        all(not (root / parent).is_symlink() for parent in relative.parents),
        "source delivery directory symlink",
    )
    require(
        path.is_file() and not path.is_symlink(),
        "missing regular source delivery file: " + name,
    )
    return path


def read_json(path: Path) -> dict:
    require(
        0 < path.stat().st_size <= 32 * 1024**2, "empty/oversized source delivery JSON"
    )
    return json.loads(path.read_text(), object_pairs_hook=unique)


def verify_signature(path: Path, keys: Path) -> None:
    result = subprocess.run(
        ["/sbin/apk", "verify", "--network=no", "--keys-dir", str(keys), str(path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    require(
        result.returncode == 0,
        "official APK/index signature verification failed: "
        + repr(
            {
                "file": str(path),
                "exit": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        ),
    )


def checked_record(root: Path, record: dict, declared: set[str]) -> Path:
    require(
        isinstance(record, dict)
        and re.fullmatch(r"[0-9a-f]{64}", record.get("sha256", "")) is not None,
        "source delivery requires complete SHA256 identity",
    )
    path = path_in(root, record["path"])
    require(
        sha256(path) == record["sha256"],
        "source delivery SHA256 mismatch: " + record["path"],
    )
    declared.add(record["path"])
    return path


def inventory_plan(capture: dict, machine: str) -> dict:
    try:
        return source_plan(capture, machine)
    except (InventoryError, KeyError, TypeError) as exc:
        raise InputError("runtime inventory mismatch: " + str(exc)) from exc


def index_rows(path: Path, machine: str) -> list[dict]:
    with tarfile.open(path, mode="r:gz") as archive:
        members = archive.getmembers()
        require(
            len({m.name for m in members}) == len(members),
            "duplicate official index member",
        )
        signers = [
            m.name.removeprefix(".SIGN.RSA.")
            for m in members
            if m.name.startswith(".SIGN.RSA.")
        ]
        require(
            len(signers) == 1 and signers[0] in KEYS[machine],
            "unapproved official index signer",
        )
        selected = [m for m in members if m.name == "APKINDEX"]
        require(
            len(selected) == 1
            and selected[0].isreg()
            and 0 < selected[0].size <= 32 * 1024**2,
            "regular complete official APK index required",
        )
        stream = archive.extractfile(selected[0])
        require(stream is not None, "unreadable official index")
        text = stream.read().decode()
    rows = []
    for block in text.strip().split("\n\n"):
        pairs = [line.split(":", 1) for line in block.splitlines()]
        require(
            all(len(pair) == 2 and len(pair[0]) == 1 for pair in pairs),
            "invalid official index field",
        )
        rows.append(unique(pairs))
    require(bool(rows), "empty official APK index")
    return rows


def verify(root: Path, runtime_capture: dict) -> dict:
    require(
        root.is_dir() and not root.is_symlink(),
        "regular external source-delivery root required",
    )
    index = read_json(path_in(root, "index.json"))
    require(
        set(index)
        == {
            "schema_version",
            "architecture",
            "inventory",
            "indexes",
            "packages",
            "source_groups",
        }
        and type(index["schema_version"]) is int
        and index["schema_version"] == 1,
        "exact external source-delivery schema required; clearance labels are not inputs",
    )
    machine = index["architecture"]
    require(machine in KEYS, "unsupported external source architecture")
    declared = {"index.json"}
    stored = inventory_plan(
        read_json(checked_record(root, index["inventory"], declared)), machine
    )
    runtime = inventory_plan(runtime_capture, machine)
    require(
        runtime == stored,
        "supplied runtime inventory differs from delivered source subject",
    )
    pending = {p["name"] for p in runtime["unresolved_packages"]}
    require(pending <= OWNED_PENDING, "unknown unresolved runtime source subject")
    virtual = {p["name"] for p in runtime["virtual_packages"]}
    expected = {
        p["name"]: p["fields"]
        for p in runtime["packages"]
        if p["name"] not in pending | virtual
    }
    require(bool(expected), "nonempty external runtime package set required")
    keys = root / "keys"
    require(
        keys.is_dir()
        and not keys.is_symlink()
        and {p.name for p in keys.iterdir()} == set(KEYS[machine]),
        "exact official architecture-scoped verification keys required",
    )
    for name, digest in KEYS[machine].items():
        checked_record(root, {"path": "keys/" + name, "sha256": digest}, declared)
    indexes = {}
    require(
        isinstance(index["indexes"], list) and bool(index["indexes"]),
        "complete official source indexes required",
    )
    for record in index["indexes"]:
        require(
            set(record) == {"repository", "path", "sha256"}
            and record["repository"] in {"main", "community"}
            and record["repository"] not in indexes,
            "duplicate/unapproved official source repository",
        )
        path = checked_record(root, record, declared)
        verify_signature(path, keys)
        indexes[record["repository"]] = index_rows(path, machine)
    verified = {}
    for record in index["packages"]:
        require(
            set(record) == {"repository", "path", "sha256", "index_record"},
            "exact external APK proof schema required",
        )
        metadata = record["index_record"]
        name = metadata.get("P")
        require(
            name in expected
            and name not in verified
            and record["repository"] in indexes,
            "duplicate/unexpected external source package",
        )
        require(
            sum(row == metadata for row in indexes[record["repository"]]) == 1,
            "APK subject not found uniquely in actual authenticated index",
        )
        require(
            all(
                expected[name].get(k) == metadata.get(k)
                for k in ["C", "P", "V", "A", "o", "c", "L", "S", "I", "T", "U"]
            ),
            "signed source subject differs from installed package metadata",
        )
        path = checked_record(root, record, declared)
        require(path.stat().st_size <= 512 * 1024**2, "oversized external APK proof")
        verify_signature(path, keys)
        verified[name] = bind_apk_control(path, metadata, machine)
    require(set(verified) == set(expected), "incomplete exact external APK proof set")
    groups = {
        (r["origin"], r["commit"], r["package_version"]): r["packages"]
        for r in runtime["source_groups"]
    }
    observed = set()
    proofs = []
    for group in index["source_groups"]:
        require(
            set(group)
            == {
                "origin",
                "commit",
                "package_version",
                "packages",
                "git_path",
                "git_objects",
                "recipe",
                "sources",
            },
            "exact external source graph schema required",
        )
        key = (group["origin"], group["commit"], group["package_version"])
        require(
            key in groups and key not in observed and group["packages"] == groups[key],
            "duplicate, omitted or relabeled source origin/commit/version group",
        )
        observed.add(key)
        for name in group["packages"]:
            require(
                verified[name]["origin"] == group["origin"]
                and verified[name]["commit"] == group["commit"],
                "source group differs from actual signed APK control",
            )
        objects = {}
        for record in group["git_objects"]:
            require(
                set(record) == {"path", "sha256", "kind", "git_id"}
                and record["kind"] in {"tree", "commit"}
                and re.fullmatch(r"[0-9a-f]{40}", record["git_id"]) is not None,
                "complete original Git object proof required",
            )
            subject = (record["kind"], record["git_id"])
            require(subject not in objects, "duplicate source Git object")
            objects[subject] = checked_record(root, record, declared).read_bytes()
        require(
            len(objects) == 3
            and ("commit", group["commit"]) in objects
            and sum(kind == "tree" for kind, _ in objects) == 2,
            "complete exact commit/root/path Git proof required",
        )
        tree_id = bind_git_recipe(
            group["commit"],
            objects["commit", group["commit"]],
            {oid: data for (kind, oid), data in objects.items() if kind == "tree"},
            group["git_path"],
            origin=group["origin"],
        )
        require(
            set(group["recipe"]) == {"path", "sha256", "apkbuild_sha256"},
            "exact recipe archive identity required",
        )
        archive = checked_record(root, group["recipe"], declared)
        tree = recipe_tree(archive, tree_id, group["recipe"]["apkbuild_sha256"])
        require(
            isinstance(group["sources"], dict), "complete source input map required"
        )
        mapping = {
            name: checked_record(root, record, declared)
            for name, record in group["sources"].items()
        }
        proofs.append(verify_source_inputs(tree, mapping))
    require(observed == set(groups), "incomplete source origin/commit/version delivery")
    actual = set()
    for path in root.rglob("*"):
        require(
            not path.is_symlink() and (path.is_file() or path.is_dir()),
            "unsafe external source-delivery entry",
        )
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    require(
        actual == declared,
        "external source delivery contains missing or unindexed files",
    )
    return {
        "status": "EXTERNAL_APK_SOURCE_DELIVERY_ONLY",
        "full_runtime_source_acceptance": False,
        "architecture": machine,
        "package_set_sha256": runtime["package_set_sha256"],
        "external_packages": len(verified),
        "source_groups": len(proofs),
        "source_input_count": sum(p["source_input_count"] for p in proofs),
        "source_files": len(declared),
        "unresolved_owned_packages": runtime["unresolved_packages"],
        "virtual_packages": runtime["virtual_packages"],
        "scope": "Exact supplied-runtime capture and delivered official signature/control/Git/source bytes only. Owned providers, Python base, compiler/language support, full source acceptance, license/advisory and ECM/MCP gates remain separate. No source or target executable was run.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--runtime-inventory", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            verify(args.source_root, read_json(args.runtime_inventory)),
            sort_keys=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
