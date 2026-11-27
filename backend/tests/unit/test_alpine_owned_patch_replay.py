"""Literal source replay: original recorded patches, no fuzz/offset or execution."""

import copy
import gzip
import hashlib
import importlib.util
import json
import shutil
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def consumer(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "alpine_owned_busybox", ROOT / "scripts/alpine_owned_busybox.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["wget", "time"])
def original(request):
    policy = json.loads((ROOT / "docker/alpine-busybox/manifest.json").read_text())
    if request.param == "wget":
        record = policy["aports"]["vendor_patch_witnesses"][0]
        with tarfile.open(
            ROOT / "docker/alpine-busybox" / policy["aports"]["file"]
        ) as archive:
            patch = archive.extractfile("main/busybox/" + record["file"]).read()
    else:
        record = policy["patches"][1]
        patch = (ROOT / "docker/alpine-busybox" / record["file"]).read_bytes()
    data = gzip.decompress(
        (
            ROOT
            / f"backend/tests/fixtures/alpine/busybox-original-{request.param}.c.gz"
        ).read_bytes()
    )
    return {record["witnesses"][0]["path"]: data}, patch, record["witnesses"]


def test_actual_original_patches_replay_to_every_recorded_full_file_hash(
    consumer, original
):
    files, patch, witnesses = original
    unchanged = dict(files)
    result = consumer.replay_patch(files, patch, witnesses)
    assert files == unchanged
    assert set(result) == {w["path"] for w in witnesses}
    for witness in witnesses:
        assert (
            hashlib.sha256(result[witness["path"]]).hexdigest()
            == witness["after_sha256"]
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "preimage",
        "postimage",
        "unbound-path",
        "missing-witness",
        "wrong-hunk-position",
        "missing-hunk",
        "context",
        "nonliteral-newline",
    ],
)
def test_matching_recipe_labels_never_enable_fuzzy_partial_or_unbound_replay(
    consumer, original, mutation
):
    files, patch, witnesses = original
    files, witnesses = dict(files), copy.deepcopy(witnesses)
    if mutation == "preimage":
        name = witnesses[0]["path"]
        files[name] = b"X" + files[name][1:]
    elif mutation == "postimage":
        witnesses[0]["after_sha256"] = "0" * 64
    elif mutation == "unbound-path":
        patch = patch.replace(b"+++ b/", b"+++ b/../", 1)
    elif mutation == "missing-witness":
        witnesses = []
    elif mutation == "wrong-hunk-position":
        patch = patch.replace(b"@@ -", b"@@ -9", 1)
    elif mutation == "missing-hunk":
        patch = patch.split(b"@@ -", 1)[0]
    elif mutation == "nonliteral-newline":
        patch = patch.replace(b"\n", b"\r\n")
    else:
        start = patch.index(b"@@ -")
        line_start = patch.index(b"\n ", start) + 2
        patch = patch[:line_start] + b"X" + patch[line_start + 1 :]
    with pytest.raises(consumer.InputError):
        consumer.replay_patch(files, patch, witnesses)


def test_reviewed_zero_old_count_creates_only_the_bound_new_file(consumer):
    content = b"new literal source\n"
    witnesses = [
        {
            "path": "new.c",
            "before_sha256": None,
            "after_sha256": hashlib.sha256(content).hexdigest(),
        }
    ]
    patch = b"--- /dev/null\n+++ b/new.c\n@@ -0,0 +1 @@\n+new literal source\n"
    assert consumer.replay_patch({}, patch, witnesses) == {"new.c": content}
    with pytest.raises(consumer.InputError):
        consumer.replay_patch({"new.c": b"already exists\n"}, patch, witnesses)


def test_exact_input_and_output_hunk_positions_are_both_required(consumer):
    old, new = b"before\noriginal\nafter\n", b"before\nreplacement\nafter\n"
    witnesses = [
        {
            "path": "data.c",
            "before_sha256": hashlib.sha256(old).hexdigest(),
            "after_sha256": hashlib.sha256(new).hexdigest(),
        }
    ]
    patch = b"--- a/data.c\n+++ b/data.c\n@@ -2 +2 @@\n-original\n+replacement\n"
    assert consumer.replay_patch({"data.c": old}, patch, witnesses) == {"data.c": new}
    with pytest.raises(consumer.InputError):
        consumer.replay_patch(
            {"data.c": old}, patch.replace(b"+2 @@", b"+1 @@"), witnesses
        )


def test_explicit_corresponding_diffs_preserve_all_original_full_source_witnesses(
    consumer,
):
    policy = json.loads((ROOT / "docker/alpine-busybox/manifest.json").read_text())
    plan = consumer.load_corresponding_plan(
        ROOT / "docker/alpine-busybox/source-replay/source-replay.json", policy
    )
    assert len(plan["patches"]) == 5
    original = {
        row["file"]: row
        for row in policy["aports"]["vendor_patch_witnesses"] + policy["patches"]
    }
    assert sum(len(row["witnesses"]) for row in plan["patches"]) == 7
    for row in plan["patches"]:
        assert row["witnesses"] == original[row["original_file"]]["witnesses"]
    assert plan["patched_regular_files"] == 2881


@pytest.mark.parametrize(
    "mutation",
    [
        "manifest",
        "patch-bytes",
        "missing-patch",
        "extra-file",
        "file-symlink",
        "parent-symlink",
        "changed-witness",
    ],
)
def test_corresponding_plan_is_not_an_unbound_patch_fallback(
    consumer, tmp_path, mutation
):
    policy = json.loads((ROOT / "docker/alpine-busybox/manifest.json").read_text())
    root = tmp_path / "source-replay"
    shutil.copytree(ROOT / "docker/alpine-busybox/source-replay", root)
    manifest = root / "source-replay.json"
    plan = json.loads(manifest.read_text())
    patch = root / plan["patches"][0]["file"]
    if mutation == "manifest":
        manifest.write_bytes(manifest.read_bytes() + b" ")
    elif mutation == "patch-bytes":
        patch.write_bytes(b"X" + patch.read_bytes()[1:])
    elif mutation == "missing-patch":
        patch.unlink()
    elif mutation == "extra-file":
        (root / "unreviewed.patch").write_bytes(b"unbound")
    elif mutation == "file-symlink":
        patch.rename(tmp_path / "outside.patch")
        patch.symlink_to(tmp_path / "outside.patch")
    elif mutation == "parent-symlink":
        (root / "patches").rename(tmp_path / "outside")
        (root / "patches").symlink_to(tmp_path / "outside", target_is_directory=True)
    else:
        policy["aports"]["vendor_patch_witnesses"][28]["witnesses"][0][
            "after_sha256"
        ] = "0" * 64
    with pytest.raises(consumer.InputError):
        consumer.load_corresponding_plan(manifest, policy)


@pytest.mark.parametrize(
    "mutation",
    [
        "empty-header",
        "invalid-utf8",
        "duplicate-witness",
        "dot-path",
        "boolean-hash",
        "missing-hash",
    ],
)
def test_malformed_source_replay_data_fails_as_a_typed_input_error(consumer, mutation):
    old, new = b"original\n", b"replacement\n"
    witness = {
        "path": "data.c",
        "before_sha256": hashlib.sha256(old).hexdigest(),
        "after_sha256": hashlib.sha256(new).hexdigest(),
    }
    patch = b"--- a/data.c\n+++ b/data.c\n@@ -1 +1 @@\n-original\n+replacement\n"
    witnesses = [witness]
    files = {"data.c": old}
    if mutation == "empty-header":
        patch = patch.replace(b"+++ b/data.c", b"+++ ")
    elif mutation == "invalid-utf8":
        patch = patch.replace(b"+++ b/data.c", b"+++ b/\xff")
    elif mutation == "duplicate-witness":
        witnesses.append(dict(witness))
    elif mutation == "dot-path":
        witness["path"] = "."
        patch = patch.replace(b"data.c", b".")
        files = {".": old}
    elif mutation == "boolean-hash":
        witness["before_sha256"] = True
    else:
        witness.pop("after_sha256")
    with pytest.raises(consumer.InputError):
        consumer.replay_patch(files, patch, witnesses)
