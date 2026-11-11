#!/usr/bin/env python3
"""Prepare exact libxml2 sources; exercise them only in a disposable musl builder.

Not an image/advisory acceptance gate. The integrating owner must refresh recipe
hash consumers and run both native architectures and full-image/source checks.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import re
import subprocess
from pathlib import Path

from build_alpine_codecs import apply_patch
from build_alpine_security_libraries import extract_source
from build_native_runtime import BuildError, _require, sha256


def prepare(repo: Path, archive: Path, source: Path, *, baseline: bool = False) -> dict:
    recipe_root = repo / "docker/alpine-native"
    recipe = json.loads((recipe_root / "security-libraries.json").read_text())
    item = next(item for item in recipe["libraries"] if item["name"] == "libxml2")
    _require(
        archive.is_file()
        and not archive.is_symlink()
        and archive.stat().st_size == item["archive"]["size"]
        and sha256(archive) == item["archive"]["sha256"],
        "libxml2 archive identity mismatch",
    )
    patches = item["patches"][:9] if baseline else item["patches"]
    _require(len(patches) >= 9, "original nine corrections required")
    extract_source(archive, source)
    evidence = source.parent / (source.name + "-patch-proof")
    evidence.mkdir(exist_ok=False)
    applied = []
    for patch in patches:
        asset = recipe_root / patch["file"]
        _require(
            asset.is_file()
            and not asset.is_symlink()
            and sha256(asset) == patch["sha256"],
            "libxml2 patch identity mismatch",
        )
        applied.append(apply_patch(patch, recipe_root, source, dict(os.environ)))
    receipt = {"archive": item["archive"], "patches": applied, "baseline": baseline}
    (evidence / "source.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def require_native() -> None:
    _require(
        Path("/etc/alpine-release").is_file()
        and Path("/etc/alpine-release").read_text().strip().startswith("3.24.")
        and platform.machine() in {"x86_64", "aarch64"}
        and os.geteuid() == 3000
        and os.environ.get("ECM_DISPOSABLE_LIBXML2_PROOF") == "1",
        "only a disposable native Alpine 3.24 proof builder is allowed",
    )


def native_build(repo: Path, source: Path) -> None:
    require_native()
    item = next(
        item
        for item in json.loads(
            (repo / "docker/alpine-native/security-libraries.json").read_text()
        )["libraries"]
        if item["name"] == "libxml2"
    )
    commands = [
        ["./configure", *item["configure"]],
        ["make", "-j2", "libxml2.la", "xmlcatalog", "runtest", "testparser"],
        [
            "cc",
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Iinclude",
            str(repo / "docker/alpine-native/libxml2-recursion-smoke.c"),
            "-L.libs",
            "-lxml2",
            "-ldl",
            "-o",
            "recursion-smoke",
        ],
    ]
    (source / "proof-build-commands.json").write_text(
        json.dumps({"commands": commands, "CFLAGS": os.environ.get("CFLAGS")}, indent=2)
        + "\n"
    )
    for number, command in enumerate(commands):
        with (source / f"proof-build-{number}.log").open("w") as log:
            subprocess.run(
                command, cwd=source, stdout=log, stderr=subprocess.STDOUT, check=True
            )


def validate_smoke_status(output: bytes, code: int, name: str, expected: int) -> None:
    text = output.decode("utf-8", errors="replace")
    _require(code == expected, f"unexpected native status for {name}: {code}")
    if expected == 0:
        lines = text.splitlines()
        _require(
            len(lines) == 32
            and all(
                line.startswith(f"{name} round={index} ") and line.endswith(" PASS")
                for index, line in enumerate(lines)
            ),
            f"incomplete lifecycle output for {name}",
        )
    else:
        _require(
            f"{name} round=0 " in text and " FAIL" in text,
            f"not a regression RED for {name}",
        )


def native_run(repo: Path, source: Path, *, expect_red: bool = False) -> dict:
    require_native()
    (source / "proof-self.sgml").write_text(
        "CATALOG proof-self.sgml\nCATALOG proof-self.sgml\nSYSTEM key proof:value\n"
    )
    for index in range(61):
        (source / f"proof-cat{index}.sgml").write_text(
            f"CATALOG proof-cat{index + 1}.sgml\n"
            if index < 60
            else "SYSTEM tail proof:value\n"
        )
    tests = [
        (name, None, name not in {"rng-default", "rng-env-zero"})
        for name in [
            "sgml-dedup",
            "sgml-depth",
            "rng-default",
            "rng-api",
            "rng-api-exact",
            "rng-api-reset",
            "rng-deep",
        ]
    ]
    tests.extend([("rng-env", "2", True), ("rng-env-zero", "0", False)])
    tests.extend(
        ("rng-invalid", value, True)
        for value in ["not-a-limit", "-1", "2147483648", "9999999999999999999999999999"]
    )
    results = []
    for number, (name, limit, red) in enumerate(tests):
        env = dict(os.environ)
        env.pop("RNG_INCLUDE_LIMIT", None)
        env["LD_LIBRARY_PATH"] = str(source / ".libs")
        if limit is not None:
            env["RNG_INCLUDE_LIMIT"] = limit
        command = [str(source / "recursion-smoke"), name]
        result = subprocess.run(
            command,
            cwd=source,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=120,
            check=False,
        )
        log = source / f"proof-run-{number}-{name}.log"
        log.write_bytes(result.stdout)
        results.append(
            {
                "command": command,
                "limit": limit,
                "exit_code": result.returncode,
                "expected_exit_code": 1 if expect_red and red else 0,
                "log": log.name,
                "log_sha256": sha256(log),
            }
        )
        validate_smoke_status(
            result.stdout, result.returncode, name, 1 if expect_red and red else 0
        )
    shell = source / "test/catalogs/shell-bounds.sh"
    original = repo / "docker/alpine-native/libxml2-shell-bounds.sh"
    if shell.exists():
        _require(sha256(shell) == sha256(original), "shell regression fixture drift")
    else:
        shell = original
    command = ["sh", str(shell), "./xmlcatalog", "test/catalogs/docbook.xml"]
    result = subprocess.run(
        command,
        cwd=source,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )
    log = source / "proof-run-shell.log"
    log.write_bytes(result.stdout)
    results.append(
        {
            "command": command,
            "exit_code": result.returncode,
            "expected_exit_code": 1 if expect_red else 0,
            "log": log.name,
            "log_sha256": sha256(log),
        }
    )
    receipt = {
        "architecture": platform.machine(),
        "uid": os.geteuid(),
        "stack_limit": resource.getrlimit(resource.RLIMIT_STACK),
        "expect_red": expect_red,
        "results": results,
    }
    (source / "proof-run.json").write_text(json.dumps(receipt, indent=2) + "\n")
    _require(
        all(r["exit_code"] == r["expected_exit_code"] for r in results),
        "libxml2 native regression matrix failed; inspect proof-run.json",
    )
    return receipt


def native_mutants(repo: Path, source: Path) -> list[dict]:
    """Remove each dangerous guard separately, rebuild and require behavioral RED."""
    require_native()
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = str(source / ".libs")
    env.pop("RNG_INCLUDE_LIMIT", None)
    mutations = [
        (
            "sgml-depth",
            "catalog.c",
            "if (depth > MAX_CATAL_DEPTH)",
            "if (0)",
            2,
            "sgml-depth",
        ),
        (
            "sgml-dedup",
            "catalog.c",
            "res = xmlHashAddEntry(catal->sgml, filename, entry);",
            "xmlFreeCatalogEntry(entry, NULL); res = 0;",
            1,
            "sgml-dedup",
        ),
        (
            "rng-limit",
            "relaxng.c",
            "if (ctxt->incNr >= ctxt->incLimit)",
            "if (0)",
            1,
            "rng-api",
        ),
        (
            "rng-propagation",
            "relaxng.c",
            "if (xmlRelaxNGIncludePush(ctxt, ret) < 0) {\n        return (NULL);\n    }",
            "xmlRelaxNGIncludePush(ctxt, ret);",
            1,
            "rng-api",
        ),
        *[
            (f"shell-{name}", "xmlcatalog.c", guard, "if (0)", 1, "shell")
            for name, guard in [
                ("command", "if (i >= (int)sizeof(command) - 2)"),
                ("argument", "if (i >= (int)sizeof(arg) - 2)"),
                ("argv", "if (i >= (int)sizeof(argv) / (int)sizeof(char*))"),
            ]
        ],
    ]
    results = []
    for name, relative, old, new, count, test in mutations:
        path = source / relative
        original = path.read_bytes()
        original_hash = sha256(path)
        text = original.decode()
        _require(text.count(old) == count, "mutation preimage mismatch")
        command = (
            [
                "sh",
                str(repo / "docker/alpine-native/libxml2-shell-bounds.sh"),
                "./xmlcatalog",
                "test/catalogs/docbook.xml",
            ]
            if test == "shell"
            else [str(source / "recursion-smoke"), test]
        )
        try:
            path.write_text(text.replace(old, new))
            with (source / f"proof-mutant-{name}-build.log").open("w") as log:
                subprocess.run(
                    ["make", "-j2", "libxml2.la", "xmlcatalog"],
                    cwd=source,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                )
            result = subprocess.run(
                command,
                cwd=source,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=120,
                check=False,
            )
            log = source / f"proof-mutant-{name}.log"
            log.write_bytes(result.stdout)
            results.append(
                {
                    "name": name,
                    "path": relative,
                    "before_sha256": original_hash,
                    "mutant_sha256": sha256(path),
                    "old": old,
                    "new": new,
                    "occurrences": count,
                    "command": command,
                    "exit_code": result.returncode,
                    "log": log.name,
                    "log_sha256": sha256(log),
                }
            )
            _require(result.returncode == 1, f"dangerous mutant not detected: {name}")
            if test != "shell":
                validate_smoke_status(result.stdout, result.returncode, test, 1)
        finally:
            path.write_bytes(original)
            with (source / f"proof-mutant-{name}-restore.log").open("w") as log:
                subprocess.run(
                    ["make", "-j2", "libxml2.la", "xmlcatalog"],
                    cwd=source,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                )
    (source / "proof-mutants.json").write_text(json.dumps(results, indent=2) + "\n")
    return results


def native_upstream(source: Path) -> dict:
    require_native()
    results = []
    for command, name in [
        (["./runtest"], "runtest"),
        (["./testparser"], "testparser"),
        (["make", "Catatests"], "Catatests"),
    ]:
        result = subprocess.run(
            command,
            cwd=source,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=240,
            check=False,
        )
        log = source / f"proof-upstream-{name}.log"
        log.write_bytes(result.stdout)
        _require(result.returncode == 0, f"upstream test failed: {name}")
        results.append({"command": command, "log": log.name, "sha256": sha256(log)})
    # Old Makefile loops ignore golden-test failures. Check every available pair
    # independently without auto-creating/updating expected results.
    fixtures = []
    for script in sorted((source / "test/catalogs").glob("*.script")):
        for suffix in [".xml", ".sgml"]:
            catalog = script.with_suffix(suffix)
            if not catalog.exists():
                continue
            expected = source / "result/catalogs" / script.stem
            command = ["./xmlcatalog", "--shell", str(catalog.relative_to(source))]
            result = subprocess.run(
                command,
                cwd=source,
                input=script.read_bytes(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
            _require(
                expected.is_file()
                and result.returncode == 0
                and result.stdout == expected.read_bytes(),
                f"catalog golden mismatch: {catalog.name}",
            )
            fixtures.append(str(catalog.relative_to(source)))
    receipt = {"upstream": results, "catalog_golden_pairs": fixtures}
    (source / "proof-upstream.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def native_abi(source: Path, baseline: Path) -> dict:
    require_native()

    def symbols(directory: Path) -> dict:
        output = subprocess.check_output(
            [
                "nm",
                "-D",
                "--defined-only",
                "--format=posix",
                str(directory / ".libs/libxml2.so.2"),
            ],
            text=True,
        )
        return {
            fields[0]: [
                fields[1],
                fields[3] if len(fields) > 3 and fields[1] in "BDRV" else None,
            ]
            for fields in (line.split() for line in output.splitlines())
        }

    old, new = symbols(baseline), symbols(source)
    _require(
        {key: new.get(key) for key in old} == old
        and set(new) - set(old) == {"xmlRelaxParserSetIncLImit"},
        "existing exported ABI changed",
    )

    def dynamic(directory: Path) -> list:
        text = subprocess.check_output(
            ["readelf", "-d", "--wide", str(directory / ".libs/libxml2.so.2")],
            text=True,
        )
        _require(
            not re.search(r"\((?:RPATH|RUNPATH)\)", text), "unexpected loader path"
        )
        return sorted(re.findall(r"\((SONAME|NEEDED)\).*\[([^]]+)\]", text))

    before, after = dynamic(baseline), dynamic(source)
    _require(
        before == after and ("SONAME", "libxml2.so.2") in after,
        "SONAME/dependency ABI changed",
    )
    for relative in ["config.h", "include/libxml/xmlversion.h", "configure"]:
        _require(
            sha256(baseline / relative) == sha256(source / relative),
            "compiled feature configuration changed",
        )
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = str(source / ".libs")
    env.pop("RNG_INCLUDE_LIMIT", None)
    command = [str(baseline / "recursion-smoke"), "rng-default"]
    result = subprocess.run(
        command,
        cwd=source,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    (source / "proof-old-consumer.log").write_bytes(result.stdout)
    receipt = {
        "original_export_count": len(old),
        "added_exports": sorted(set(new) - set(old)),
        "dynamic": after,
        "old_header_consumer": command,
    }
    (source / "proof-abi.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["prepare", "build", "run", "mutants", "upstream", "abi"]
    )
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--expect-red", action="store_true")
    parser.add_argument("--baseline-source", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        _require(args.archive is not None, "archive required")
        print(
            json.dumps(
                prepare(args.repo, args.archive, args.source, baseline=args.baseline)
            )
        )
    elif args.action == "build":
        native_build(args.repo, args.source)
    elif args.action == "run":
        print(
            json.dumps(native_run(args.repo, args.source, expect_red=args.expect_red))
        )
    elif args.action == "mutants":
        print(json.dumps(native_mutants(args.repo, args.source)))
    elif args.action == "upstream":
        print(json.dumps(native_upstream(args.source)))
    else:
        _require(args.baseline_source is not None, "baseline source required")
        print(json.dumps(native_abi(args.source, args.baseline_source)))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from exc
