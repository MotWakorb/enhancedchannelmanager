"""Execute production command boundaries against a modeled external zypper.

The subprocess preserves every explicit package identity while rotating an
unpinned transitive dependency. This models repository refresh behavior, not
real vendor/signature/solver integration; disposable native builds cover that.
"""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
REVIEWED = b"synthetic reviewed signed repomd"
CHANGED = b"synthetic different signed repomd"
METADATA = "/var/cache/zypp/raw/oss/repodata/repomd.xml"
FAKE_ZYPPER = """#!/usr/bin/env python3
import json, os, pathlib, sys
p = pathlib.Path(os.environ['MODEL_STATE'])
s = json.loads(p.read_text())
args = sys.argv[1:]
commands = {'addrepo', 'refresh', 'update', 'install', 'clean'}
command = next(a for a in args if a in commands)
metadata = pathlib.Path(s['metadata'])
if command == 'refresh':
    metadata.write_bytes(s['remote'].encode())
    s['refreshes'] += 1
    if s['switch_at'] == 0:
        s['remote'] = s['changed']
elif command in ('update', 'install'):
    frozen = '--no-refresh' in args[:args.index(command)]
    if not frozen:
        metadata.write_bytes(s['remote'].encode())
    if not metadata.exists():
        sys.exit(23)  # A no-refresh solve cannot manufacture cleaned metadata.
    s['solves'].append({'command': command, 'metadata': metadata.read_text(),
                       'transitive_identity': 'lib-modeled=unreviewed' if metadata.read_text() == s['changed'] else 'lib-modeled=reviewed',
                       'explicit_identities': args[args.index(command) + 1:]})
    if len(s['solves']) >= s['switch_at']:
        s['remote'] = s['changed']
elif command == 'clean' and '--all' in args:
    metadata.unlink(missing_ok=True)
p.write_text(json.dumps(s))
"""


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def boundary(tmp_path):
    tools = tmp_path / "bin"
    tools.mkdir()
    metadata = tmp_path / "repomd.xml"
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "metadata": str(metadata),
                "remote": REVIEWED.decode(),
                "changed": CHANGED.decode(),
                "refreshes": 0,
                "switch_at": 0,
                "solves": [],
            }
        )
    )
    executable = tools / "zypper"
    executable.write_text(
        FAKE_ZYPPER.replace("#!/usr/bin/env python3", "#!" + sys.executable)
    )
    executable.chmod(0o755)
    for name in ("rpm", "python3.12"):
        stub = tools / name
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    return {
        "metadata": metadata,
        "state": state,
        "tools": tools,
        "env": {
            **os.environ,
            "PATH": str(tools) + ":/usr/bin:/bin",
            "MODEL_STATE": str(state),
        },
    }


def mutate_state(boundary, **updates):
    path = boundary["state"]
    state = json.loads(path.read_text())
    state.update(updates)
    path.write_text(json.dumps(state))


@pytest.mark.parametrize("frozen", [False, True])
def test_external_model_can_rotate_only_transitives_while_preserving_explicit_pins(
    boundary, frozen
):
    boundary["metadata"].write_bytes(REVIEWED)
    mutate_state(boundary, remote=CHANGED.decode())
    command = [str(boundary["tools"] / "zypper"), "--non-interactive"]
    if frozen:
        command.append("--no-refresh")
    command.extend(["install", "fixed-direct.x86_64=1-1", "fixed-security.noarch=2-1"])
    subprocess.run(command, env=boundary["env"], check=True)
    solve = json.loads(boundary["state"].read_text())["solves"][0]
    assert solve["explicit_identities"] == [
        "fixed-direct.x86_64=1-1",
        "fixed-security.noarch=2-1",
    ]
    assert solve["transitive_identity"] == (
        "lib-modeled=reviewed" if frozen else "lib-modeled=unreviewed"
    )


@pytest.mark.parametrize("switch_at", [0, 1])
def test_builder_transactions_cannot_refresh_after_cohort_check(
    monkeypatch, tmp_path, boundary, switch_at
):
    builder = load(
        "native_transaction_builder", ROOT / "scripts/build_native_runtime.py"
    )
    recipe = builder.load_recipe(ROOT / "docker/native/manifest.json", "amd64")[
        "architectures"
    ]["amd64"]
    recipe["repository_metadata_sha256"] = hashlib.sha256(REVIEWED).hexdigest()
    mutate_state(boundary, switch_at=switch_at)
    inventory = "\n".join(
        "\t".join(
            [
                p["name"],
                p["epoch"],
                *p["version"].rsplit("-", 1),
                p["arch"],
                p["sourcerpm"],
                "MIT",
            ]
        )
        for p in recipe["builder_rpms"] + recipe["security_runtime_rpms"]
    )
    original_read = Path.read_text
    original_glob = Path.glob
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda p, *a, **kw: (
            'ID="opensuse-tumbleweed"'
            if str(p) == "/etc/os-release"
            else original_read(p, *a, **kw)
        ),
    )
    monkeypatch.setattr(
        Path,
        "glob",
        lambda p, pattern: (
            iter(()) if str(p) == "/etc/zypp/repos.d" else original_glob(p, pattern)
        ),
    )
    original_check = builder.check_repository_cohort
    monkeypatch.setattr(
        builder,
        "check_repository_cohort",
        lambda selected: original_check(selected, boundary["metadata"]),
    )

    def run(args, **kwargs):
        if args[0] == "/usr/bin/zypper":
            return subprocess.check_output(
                [str(boundary["tools"] / "zypper"), *args[1:]],
                env=boundary["env"],
                text=True,
            )
        assert args[:2] == ["/usr/bin/rpm", "-qa"]
        return inventory

    monkeypatch.setattr(builder, "run", run)
    builder._install_builder(recipe, tmp_path, boundary["env"])
    solves = json.loads(boundary["state"].read_text())["solves"]
    assert len(solves) == 2
    assert all(s["metadata"] == REVIEWED.decode() for s in solves), (
        "post-check solve refreshed to unreviewed transitive metadata"
    )


def test_codec_transaction_cannot_refresh_after_native_build_delay(
    monkeypatch, tmp_path, boundary
):
    builder = load(
        "native_transaction_codecs", ROOT / "scripts/build_native_runtime.py"
    )
    selected = builder.load_recipe(ROOT / "docker/native/manifest.json", "amd64")[
        "architectures"
    ]["amd64"]
    boundary["metadata"].write_bytes(REVIEWED)
    mutate_state(boundary, remote=CHANGED.decode())
    work, sources = tmp_path / "work", tmp_path / "sources"
    work.mkdir()
    sources.mkdir()
    monkeypatch.setattr(builder, "_verify_key", lambda *a: None)
    monkeypatch.setattr(
        builder,
        "fetch_input",
        lambda record, dest, cache: dest.write_bytes(b"modeled RPM"),
    )

    def run(args, **kwargs):
        if args[0] == "/usr/bin/zypper":
            return subprocess.check_output(
                [str(boundary["tools"] / "zypper"), *args[1:]],
                env=boundary["env"],
                text=True,
            )
        if "--checksig" in args:
            return "signatures OK"
        if "-qp" in args:
            filename = Path(args[-1]).name
            record = next(
                p
                for p in selected["codec_rpms"] + selected["source_rpms"]
                if p["url"].endswith("/" + filename)
            )
            if "filename" in record:
                name, version, release = filename.removesuffix(".src.rpm").rsplit(
                    "-", 2
                )
                return f"{name}\t{version}-{release}\tx86_64\t(none)\t1\n"
            return f"{record['name']}\t{record['version']}\t{record['arch']}\t{record['sourcerpm']}\t(none)\n"
        return ""

    monkeypatch.setattr(builder, "run", run)
    builder._codec_inputs(
        selected, ROOT / "docker/native", work, sources, boundary["env"], None
    )
    solves = json.loads(boundary["state"].read_text())["solves"]
    assert len(solves) == 1 and solves[0]["metadata"] == REVIEWED.decode(), (
        "codec solve refreshed unreviewed transitives"
    )


def docker_run(stage):
    # Preserve spaces inside shell quotes: the SBOM parser canonicalizes them
    # for comparison and is intentionally not an executable-shell renderer.
    text = (ROOT / "docker/native/Dockerfile.suse").read_text().split(" AS " + stage + "\n", 1)[1]
    text = text.split("\nFROM ", 1)[0]
    pieces = []
    for line in text.splitlines():
        if line.startswith("RUN "):
            pieces = [line.removeprefix("RUN ").removesuffix("\\").strip()]
        elif pieces:
            pieces.append(line.removesuffix("\\").strip())
        if pieces and not line.endswith("\\"):
            command = " ".join(pieces)
            if "zypper" in command:
                return command
            pieces = []
    raise AssertionError("stage has no zypper RUN")


def execute_docker_run(stage, boundary, tmp_path):
    command = docker_run(stage)
    recipe = json.loads((ROOT / "docker/native/manifest.json").read_text())
    for arch in recipe["architectures"].values():
        command = command.replace(
            arch["repository_metadata_sha256"], hashlib.sha256(REVIEWED).hexdigest()
        )
    package_file = tmp_path / "runtime-packages.txt"
    package_file.write_text("fixed-package.x86_64=1-1\n")
    command = command.replace(METADATA, str(boundary["metadata"]))
    command = command.replace("/etc/zypp/repos.d/", str(tmp_path / "repos") + "/")
    command = command.replace("/tmp/runtime-packages.txt", str(package_file))
    return subprocess.run(
        ["/bin/sh", "-ec", command],
        check=False,
        env=boundary["env"],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("stage", ["native-builder", "runtime-base", "resdet-builder"])
@pytest.mark.parametrize("switch_at", [0, 1])
def test_docker_solves_use_only_checked_metadata_including_cold_resdet(
    stage, switch_at, boundary, tmp_path
):
    # Runtime's clean --all leaves resdet no metadata; start every stage cold.
    mutate_state(boundary, switch_at=switch_at)
    result = execute_docker_run(stage, boundary, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads(boundary["state"].read_text())
    assert state["refreshes"] >= 1, (
        "cold metadata must be explicitly GPG-refreshed before checking"
    )
    assert state["solves"]
    assert all(s["metadata"] == REVIEWED.decode() for s in state["solves"]), (
        "Docker solve auto-refreshed after cohort verification"
    )


@pytest.mark.parametrize("stage", ["native-builder", "runtime-base", "resdet-builder"])
def test_cold_changed_vendor_metadata_is_refused_before_any_solve(
    stage, boundary, tmp_path
):
    mutate_state(boundary, remote=CHANGED.decode())
    result = execute_docker_run(stage, boundary, tmp_path)
    assert result.returncode != 0, "different refreshed cohort must not reach install"
    assert json.loads(boundary["state"].read_text())["solves"] == []


@pytest.mark.parametrize("changed", [False, True])
def test_runtime_clean_all_requires_resdet_to_refresh_and_recheck(
    changed, boundary, tmp_path
):
    runtime = execute_docker_run("runtime-base", boundary, tmp_path)
    assert runtime.returncode == 0, runtime.stdout + runtime.stderr
    assert not boundary["metadata"].exists(), (
        "model must exercise actual runtime clean --all"
    )
    mutate_state(
        boundary,
        remote=(CHANGED if changed else REVIEWED).decode(),
        solves=[],
        refreshes=0,
    )
    resdet = execute_docker_run("resdet-builder", boundary, tmp_path)
    state = json.loads(boundary["state"].read_text())
    assert state["refreshes"] == 1
    if changed:
        assert resdet.returncode != 0 and state["solves"] == []
    else:
        assert resdet.returncode == 0, resdet.stdout + resdet.stderr
        assert len(state["solves"]) == 1
        assert state["solves"][0]["metadata"] == REVIEWED.decode()
