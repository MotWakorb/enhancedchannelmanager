"""Build-only acquisition of exact stable-Alpine external APK/source evidence.

No package installation, recipe execution or target execution. The original
runtime capture must precede builder-only package additions. The delivered graph
is reauthenticated offline by verify_alpine_external_sources before success, but
neither tool grants aggregate runtime source, license or advisory clearance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from alpine_repository_policy import REPOSITORIES, validate_repositories
from alpine_source_inputs import (
    InputError,
    bind_apk_control,
    bind_git_recipe,
    recipe_tree,
    require,
)
from alpine_source_inventory import source_plan
from verify_alpine_external_sources import (
    KEYS,
    OWNED_PENDING,
    index_rows,
    read_json,
    sha256,
    verify,
    verify_signature,
)

GIT_ORIGIN = "https://github.com/alpinelinux/aports"
DISTFILES = "https://distfiles.alpinelinux.org/distfiles/v3.24/"
UPSTREAM_ROUTES = {
    ("libopenmpt", "libopenmpt-0.8.10+release.autotools.tar.gz"): {
        "sha512": "c6eba868e1f9b65a1c8a4829b461ffe8a1411527336de31131e1f2494f271d2f364ec6f884cbcd93bea7dedb85e18a601fb4537ebdee8f7a3c1eb6116ca7d592",
        "url": "https://lib.openmpt.org/files/libopenmpt/src/libopenmpt-0.8.10+release.autotools.tar.gz",
    },
}


class SourceFetchError(InputError):
    """Acquisition failed; retain every attempted route without a silent retry."""

    def __init__(self, message: str, attempts: list[dict]):
        super().__init__(message)
        self.attempts = attempts


def validate_destinations(output: Path, work: Path, receipt: Path) -> None:
    for path in [output, work, receipt]:
        require(
            not path.exists() and not path.is_symlink(),
            "fresh source output/work/receipt required",
        )
    a, b, c = output.resolve(), work.resolve(), receipt.resolve()
    require(
        not a.is_relative_to(b) and not b.is_relative_to(a),
        "source output/work must be disjoint",
    )
    require(
        not c.is_relative_to(a) and not c.is_relative_to(b),
        "source receipt must be outside output/work",
    )


def download_url(url: str, path: Path) -> dict:
    require(
        url.startswith("https://") and not path.exists() and not path.is_symlink(),
        "HTTPS and fresh download destination required",
    )
    request = Request(
        url, headers={"User-Agent": "ECM-exact-Alpine-source-builder/1.0"}
    )
    with urlopen(request, timeout=120) as response:
        require(
            response.status == 200 and response.url.startswith("https://"),
            "successful HTTPS source response required",
        )
        size = 0
        with path.open("xb") as target:
            while data := response.read(1024 * 1024):
                size += len(data)
                require(size <= 512 * 1024**2, "oversized source/APK download")
                target.write(data)
        return {
            "url": url,
            "final_url": response.url,
            "status": response.status,
            "headers": dict(response.headers),
            "size": size,
            "sha256": sha256(path),
        }


def fetch_source(origin: str, filename: str, expected: str, target: Path) -> dict:
    attempts = []
    primary = DISTFILES + quote(filename, safe="")
    try:
        attempts.append(download_url(primary, target))
    except HTTPError as exc:
        attempts.append(
            {
                "url": primary,
                "status": exc.code,
                "error": str(exc),
                "headers": dict(exc.headers or {}),
            }
        )
        route = UPSTREAM_ROUTES.get((origin, filename))
        if exc.code != 404 or route is None or route["sha512"] != expected:
            raise SourceFetchError(
                "source retrieval failed; no authorized alternate route", attempts
            ) from exc
        try:
            attempts.append(download_url(route["url"], target))
        except (HTTPError, URLError, OSError) as failed:
            attempts.append(
                {
                    "url": route["url"],
                    "status": getattr(failed, "code", None),
                    "error": str(failed),
                }
            )
            raise SourceFetchError(
                "exact official upstream source retrieval failed", attempts
            ) from failed
    except (URLError, OSError) as exc:
        attempts.append({"url": primary, "error": str(exc)})
        raise SourceFetchError(
            "source retrieval failed; no alternate on transport/authentication error",
            attempts,
        ) from exc
    with target.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha512").hexdigest()
    if actual != expected:
        raise SourceFetchError(
            "source SHA512 mismatch; no alternate on wrong bytes", attempts
        )
    return {
        "filename": filename,
        "sha512": actual,
        "sha256": sha256(target),
        "attempts": attempts,
    }


def select_packages(
    plan: dict, indexes: dict[str, list[dict]]
) -> dict[str, tuple[str, dict]]:
    pending = {p["name"] for p in plan["unresolved_packages"]}
    require(pending <= OWNED_PENDING, "unknown unresolved runtime source subject")
    virtual = {p["name"] for p in plan["virtual_packages"]}
    result = {}
    for package in plan["packages"]:
        if package["name"] in pending | virtual:
            continue
        fields = package["fields"]
        matches = [
            (repo, row)
            for repo, rows in indexes.items()
            for row in rows
            if all(
                row.get(k) == fields.get(k)
                for k in ["C", "P", "V", "A", "o", "c", "L", "S", "I", "T", "U"]
            )
        ]
        require(
            len(matches) == 1,
            "exact unique signed-index runtime package subject required: "
            + package["name"],
        )
        result[package["name"]] = matches[0]
    require(bool(result), "nonempty exact external runtime package selection required")
    return result


def copy_record(source: Path, output: Path, name: str) -> dict:
    require(
        source.is_file() and not source.is_symlink(),
        "regular exact source input required",
    )
    target = output / name
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = sha256(source)
    if target.exists():
        require(
            target.is_file() and not target.is_symlink() and sha256(target) == digest,
            "conflicting duplicate source delivery path",
        )
    else:
        shutil.copyfile(source, target)
        target.chmod(0o644)
    require(sha256(target) == digest, "copied source input bytes differ")
    return {"path": name, "sha256": digest}


def git_command(repo: Path, args: list[str], env: dict[str, str], log: Path) -> bytes:
    command = ["git", "-C", str(repo), *args]
    result = subprocess.run(
        command, check=False, capture_output=True, env=env, timeout=900
    )
    with log.open("ab") as output:
        output.write((json.dumps(command) + "\n").encode())
        output.write(result.stderr)
        output.write(("exit=" + str(result.returncode) + "\n").encode())
    require(
        result.returncode == 0,
        "exact original Git object retrieval failed; see " + str(log),
    )
    return result.stdout


def prepare(
    capture_path: Path, machine: str, output: Path, work: Path, receipt_path: Path
) -> dict:
    validate_destinations(output, work, receipt_path)
    require(machine in KEYS, "unsupported source architecture")
    validate_repositories(
        Path("/etc/alpine-release").read_text(),
        Path("/etc/apk/repositories").read_text(),
    )
    capture = read_json(capture_path)
    plan = source_plan(capture, machine)
    output.mkdir(parents=True)
    work.mkdir(parents=True)
    fetched = []
    groups = []
    try:
        key_root = Path("/usr/share/apk/keys").resolve()
        for name, digest in KEYS[machine].items():
            source = key_root / machine / name
            resolved = source.resolve()
            require(
                resolved.is_relative_to(key_root) and sha256(resolved) == digest,
                "official source verification key differs from approved bytes",
            )
            copy_record(resolved, output, "keys/" + name)
        inventory = copy_record(capture_path, output, "inventory.json")
        indexes = {}
        index_files = []
        for repository in REPOSITORIES:
            name = repository.rsplit("/", 1)[-1]
            path = work / (name + "-APKINDEX.tar.gz")
            fetched.append(
                download_url(repository + "/" + machine + "/APKINDEX.tar.gz", path)
            )
            verify_signature(path, output / "keys")
            indexes[name] = index_rows(path, machine)
            index_files.append(
                {
                    "repository": name,
                    **copy_record(path, output, "indexes/" + name + "-APKINDEX.tar.gz"),
                }
            )
        selected = select_packages(plan, indexes)
        packages = []
        for number, (name, (repo, metadata)) in enumerate(selected.items(), 1):
            filename = metadata["P"] + "-" + metadata["V"] + ".apk"
            path = work / filename
            url = (
                next(r for r in REPOSITORIES if r.endswith("/" + repo))
                + "/"
                + machine
                + "/"
                + filename
            )
            fetched.append(download_url(url, path))
            require(
                path.stat().st_size == int(metadata["S"]),
                "downloaded APK differs from exact signed-index size",
            )
            verify_signature(path, output / "keys")
            bind_apk_control(path, metadata, machine)
            packages.append(
                {
                    "repository": repo,
                    **copy_record(path, output, "packages/" + filename),
                    "index_record": metadata,
                }
            )
            print(
                f"Authenticated external APK {number}/{len(selected)}: {name}",
                flush=True,
            )
        git_home = work / "git-home"
        git_home.mkdir()
        repo = work / "aports.git"
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(git_home),
            "LC_ALL": "C",
            "TZ": "UTC",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
        log = work / "git-fetch.log"
        result = subprocess.run(
            ["git", "init", "--bare", str(repo)],
            check=False,
            capture_output=True,
            env=env,
            timeout=30,
        )
        require(
            result.returncode == 0, "cannot create isolated source Git object store"
        )
        for args in [
            ["remote", "add", "origin", GIT_ORIGIN],
            ["config", "remote.origin.promisor", "true"],
            ["config", "remote.origin.partialclonefilter", "blob:none"],
        ]:
            git_command(repo, args, env, log)
        commits = sorted({r["commit"] for r in plan["source_groups"]})
        require(0 < len(commits) <= 4096, "bounded complete source commit set required")
        git_command(
            repo,
            [
                "fetch",
                "--no-tags",
                "--depth=1",
                "--filter=blob:none",
                "origin",
                *commits,
            ],
            env,
            log,
        )
        for number, group in enumerate(plan["source_groups"], 1):
            origin = group["origin"]
            commit_id = group["commit"]
            matches = []
            for prefix in ["main", "community"]:
                path = prefix + "/" + origin
                entry = (
                    git_command(repo, ["ls-tree", commit_id, "--", path], env, log)
                    .decode()
                    .strip()
                )
                if entry:
                    matches.append((path, entry))
            require(
                len(matches) == 1,
                "exact unique main/community source recipe path required",
            )
            git_path, entry = matches[0]
            expected_tree = entry.split()[2]
            commit = git_command(repo, ["cat-file", "commit", commit_id], env, log)
            root_id = commit.split(b"\n", 1)[0][5:].decode()
            parent_id = (
                git_command(
                    repo,
                    ["rev-parse", commit_id + ":" + git_path.split("/")[0]],
                    env,
                    log,
                )
                .decode()
                .strip()
            )
            trees = {
                oid: git_command(repo, ["cat-file", "tree", oid], env, log)
                for oid in [root_id, parent_id]
            }
            require(
                bind_git_recipe(commit_id, commit, trees, git_path, origin=origin)
                == expected_tree,
                "original recipe Git path binding mismatch",
            )
            objects = []
            for kind, oid, data in [
                ("commit", commit_id, commit),
                *[("tree", oid, data) for oid, data in trees.items()],
            ]:
                source = work / (kind + "-" + oid)
                if source.exists():
                    require(
                        source.read_bytes() == data,
                        "conflicting original Git object bytes",
                    )
                else:
                    source.write_bytes(data)
                objects.append(
                    {
                        **copy_record(source, output, "objects/" + kind + "-" + oid),
                        "kind": kind,
                        "git_id": oid,
                    }
                )
            body = git_command(
                repo, ["show", commit_id + ":" + git_path + "/APKBUILD"], env, log
            )
            archive = work / (origin + "-" + commit_id + ".tar")
            archive.write_bytes(
                git_command(
                    repo,
                    ["archive", "--format=tar", commit_id + ":" + git_path],
                    env,
                    log,
                )
            )
            recipe_sha = hashlib.sha256(body).hexdigest()
            tree = recipe_tree(archive, expected_tree, recipe_sha)
            recipe = {
                **copy_record(archive, output, "recipes/" + archive.name),
                "apkbuild_sha256": recipe_sha,
            }
            sources = {}
            for filename, expected in tree["checksums"].items():
                if filename in tree["entries"]:
                    continue
                source = work / (origin + "-" + commit_id + "-" + filename)
                fetched.append(fetch_source(origin, filename, expected, source))
                sources[filename] = copy_record(
                    source,
                    output,
                    "sources/" + origin + "/" + commit_id + "/" + filename,
                )
            groups.append(
                {
                    **group,
                    "git_path": git_path,
                    "git_objects": objects,
                    "recipe": recipe,
                    "sources": sources,
                }
            )
            print(
                f"Prepared exact original source group {number}/{len(plan['source_groups'])}: {origin}",
                flush=True,
            )
        manifest = {
            "schema_version": 1,
            "architecture": machine,
            "inventory": inventory,
            "indexes": index_files,
            "packages": packages,
            "source_groups": groups,
        }
        (output / "index.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n"
        )
        result = verify(output, capture)
        receipt = {
            "status": "PREPARED_AND_VERIFIED_EXTERNAL_SOURCE_LAYER_ONLY",
            "full_runtime_source_acceptance": False,
            "result": result,
            "index_sha256": sha256(output / "index.json"),
            "acquisition": fetched,
            "scope": "Exact original external APK/source evidence only. Builder-only packages are excluded by the prior runtime capture. No package/recipe/target execution, runtime trust change or aggregate source/advisory acceptance.",
        }
    except Exception as exc:
        receipt = {
            "status": "EXTERNAL_SOURCE_PREPARATION_FAILED",
            "full_runtime_source_acceptance": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "acquisition": fetched,
            "source_attempts": getattr(exc, "attempts", []),
            "scope": "Failed preparation supplies no source acceptance. Partial files and failed attempts are retained.",
        }
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
        raise
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-inventory", type=Path, required=True)
    parser.add_argument("--architecture", choices=sorted(KEYS), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--receipt-file", type=Path, required=True)
    args = parser.parse_args()
    record = prepare(
        args.runtime_inventory.absolute(),
        args.architecture,
        args.output_dir.absolute(),
        args.work_dir.absolute(),
        args.receipt_file.absolute(),
    )
    print(
        json.dumps(
            {"status": record["status"], "result": record["result"]}, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
