"""Build-only acquisition of frozen nested Rust/source/evidence inputs.

Uses only the saved authenticated external runtime graph, never the builder's
expanded installed package set. Git/GPG stay build-only. No recipe, project,
compiler, target or build script is executed; final independent verification is
offline and does not grant aggregate runtime source or advisory acceptance.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from alpine_repository_policy import validate_repositories
from alpine_rust_source_inputs import COMPILERS, locked_crates
from alpine_source_inputs import InputError, require
from prepare_alpine_external_sources import (
    GIT_ORIGIN,
    git_command,
    validate_destinations,
)
from prepare_alpine_python_base import fetch
from verify_alpine_external_sources import read_json
from verify_alpine_external_sources import verify as verify_external
from verify_alpine_rust_sources import (
    PUBLIC_KEY_SHA256,
    RELEASE_KEY,
    SIGNATURES,
    project_locks,
    release_evidence,
    verify,
)

__all__ = ["RELEASE_KEY", "InputError", "prepare", "verify_releases"]


def verify_releases(root: Path, home: Path) -> dict:
    require(
        not home.exists() and not home.is_symlink(),
        "fresh isolated Rust release GPG home required",
    )
    release_evidence(root, set())
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
    key = root / "rust-key.gpg.ascii"
    shown = subprocess.run(
        [*command, "--with-colons", "--show-keys", str(key)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    fingerprints = [
        line.split(":")[9]
        for line in shown.stdout.splitlines()
        if line.startswith("fpr:")
    ]
    require(
        shown.returncode == 0
        and sum(line.startswith("pub:") for line in shown.stdout.splitlines()) == 1
        and bool(fingerprints)
        and fingerprints[0] == RELEASE_KEY,
        "original Rust release key fingerprint differs",
    )
    imported = subprocess.run(
        [*command, "--import", str(key)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    require(imported.returncode == 0, "cannot import isolated Rust release public key")
    result = {}
    for version in sorted(COMPILERS):
        archive = root / "compilers" / version / f"rustc-{version}-src.tar.xz"
        signature = archive.with_suffix(archive.suffix + ".asc")
        verified = subprocess.run(
            [*command, "--status-fd", "1", "--verify", str(signature), str(archive)],
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
        statuses = [
            line
            for line in verified.stdout.splitlines()
            if line.startswith("[GNUPG:] VALIDSIG ")
        ]
        require(
            verified.returncode == 0
            and len(statuses) == 1
            and statuses[0].split()[-1] == RELEASE_KEY
            and not re.search(
                r"\b(BADSIG|ERRSIG|EXPSIG|EXPKEYSIG|REVKEYSIG)\b",
                verified.stdout + verified.stderr,
            ),
            "original Rust source release signature verification failed",
        )
        result[version] = {
            "fingerprint": RELEASE_KEY,
            "exit": verified.returncode,
            "stdout": verified.stdout,
            "stderr": verified.stderr,
        }
    return result


def prepare(
    external_root: Path, capture_path: Path, output: Path, work: Path, receipt: Path
) -> dict:
    validate_destinations(output, work, receipt)
    validate_repositories(
        Path("/etc/alpine-release").read_text(),
        Path("/etc/apk/repositories").read_text(),
    )
    output.mkdir(parents=True)
    work.mkdir(parents=True)
    acquisition = []
    try:
        capture = read_json(capture_path)
        external = verify_external(external_root, capture)
        locks, _ = project_locks(external_root, read_json(external_root / "index.json"))
        (output / "locks").mkdir()
        lock_paths = {}
        for origin, data in locks.items():
            path = output / "locks" / (origin + "-Cargo.lock")
            path.write_bytes(data)
            lock_paths[origin] = path
        plan = locked_crates(lock_paths)
        (output / "crates").mkdir()

        def acquire_crate(row):
            name = row["name"] + "-" + row["version"] + ".crate"
            return fetch(
                "https://static.crates.io/crates/" + row["name"] + "/" + name,
                output / "crates" / name,
                row["sha256"],
            )

        # Inputs/targets are disjoint, hash-bound original archives. Preserve
        # successful receipts in lock order; failed bytes are never overwritten.
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(acquire_crate, row) for row in plan["registry"]]
            failures = []
            for number, future in enumerate(futures, 1):
                try:
                    acquisition.append(future.result())
                except (InputError, OSError) as exc:
                    failures.append(
                        {
                            "crate": plan["registry"][number - 1]["name"],
                            "error": str(exc),
                        }
                    )
                if number % 50 == 0:
                    print(
                        f"Checked original registry inputs {number}/{len(futures)}",
                        flush=True,
                    )
            require(
                not failures,
                "original Rust registry acquisition failed: " + repr(failures),
            )
        acquisition.append(
            fetch(
                "https://static.rust-lang.org/rust-key.gpg.ascii",
                output / "rust-key.gpg.ascii",
                PUBLIC_KEY_SHA256,
            )
        )
        git_home = work / "git-home"
        git_home.mkdir()
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(git_home),
            "LC_ALL": "C",
            "TZ": "UTC",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
        repo = work / "aports.git"
        log = work / "git-fetch.log"
        initialized = subprocess.run(
            ["git", "init", "--bare", str(repo)],
            check=False,
            capture_output=True,
            env=env,
            timeout=30,
        )
        require(
            initialized.returncode == 0, "cannot create isolated Rust recipe Git store"
        )
        for args in [
            ["remote", "add", "origin", GIT_ORIGIN],
            ["config", "remote.origin.promisor", "true"],
            ["config", "remote.origin.partialclonefilter", "blob:none"],
        ]:
            git_command(repo, args, env, log)
        git_command(
            repo,
            [
                "fetch",
                "--no-tags",
                "--depth=1",
                "--filter=blob:none",
                "origin",
                *[row["aports_commit"] for row in COMPILERS.values()],
            ],
            env,
            log,
        )
        for version, expected in COMPILERS.items():
            prefix = output / "compilers" / version
            prefix.mkdir(parents=True)
            name = f"rustc-{version}-src.tar.xz"
            acquisition.append(
                fetch(
                    "https://static.rust-lang.org/dist/" + name,
                    prefix / name,
                    expected["sha256"],
                )
            )
            acquisition.append(
                fetch(
                    "https://static.rust-lang.org/dist/" + name + ".asc",
                    prefix / (name + ".asc"),
                    SIGNATURES[version],
                )
            )
            commit_id = expected["aports_commit"]
            commit = git_command(repo, ["cat-file", "commit", commit_id], env, log)
            root_id = commit.split(b"\n", 1)[0][5:].decode()
            main_id = (
                git_command(repo, ["rev-parse", commit_id + ":main"], env, log)
                .decode()
                .strip()
            )
            objects = prefix / "objects"
            objects.mkdir()
            (objects / ("commit-" + commit_id)).write_bytes(commit)
            for tree_id in [root_id, main_id]:
                (objects / ("tree-" + tree_id)).write_bytes(
                    git_command(repo, ["cat-file", "tree", tree_id], env, log)
                )
            (prefix / "recipe.tar").write_bytes(
                git_command(
                    repo,
                    ["archive", "--format=tar", commit_id + ":main/rust"],
                    env,
                    log,
                )
            )
            print("Acquired original compiler/source recipe " + version, flush=True)
        signatures = verify_releases(output, work / "gnupg")
        result = verify(output, external_root, capture)
        record = {
            "status": "PREPARED_VERIFIED_FROZEN_RUST_SOURCE_INPUTS_ONLY",
            "full_runtime_source_acceptance": False,
            "architecture": result["architecture"],
            "external_package_set_sha256": external["package_set_sha256"],
            "result": result,
            "release_signatures": signatures,
            "acquisition": acquisition,
            "scope": "Delivered exact source inputs and original release signature verification, with independently repeated offline consumer. Known metadata/compiler-build/linkage gaps, aggregate source/attribution/advisory, MCP and final acceptance are not cleared. No runtime trust-store or repository changes.",
        }
    except Exception as exc:
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(
            json.dumps(
                {
                    "status": "RUST_SOURCE_PREPARATION_FAILED",
                    "full_runtime_source_acceptance": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "acquisition": acquisition,
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
    parser.add_argument("--external-root", type=Path, required=True)
    parser.add_argument("--runtime-inventory", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--receipt-file", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(
        args.external_root,
        args.runtime_inventory,
        args.output_dir.absolute(),
        args.work_dir.absolute(),
        args.receipt_file.absolute(),
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "architecture": result["architecture"],
                "full_runtime_source_acceptance": False,
            }
        )
    )


if __name__ == "__main__":
    main()
