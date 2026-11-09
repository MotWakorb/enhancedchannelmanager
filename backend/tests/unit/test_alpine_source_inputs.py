"""Signed-control architecture and complete Git/checksum joins stay distinct."""

import copy
import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ASSETS = ROOT / "docker/alpine-busybox"


@pytest.fixture
def inputs(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "alpine_source_inputs", ROOT / "scripts/alpine_source_inputs.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "name,control_arch", [("busybox-binsh", "noarch"), ("ssl_client", "x86_64")]
)
def test_actual_original_apks_preserve_repository_and_control_architectures(
    inputs, name, control_arch
):
    policy = json.loads((ASSETS / "manifest.json").read_text())
    row = next(
        r
        for r in policy["architecture_inputs"]["x86_64"]["signed_packages"]
        if r["index_record"]["P"] == name
    )
    original = copy.deepcopy(row["index_record"])
    result = inputs.bind_apk_control(
        ASSETS / row["artifact"]["file"], row["index_record"], "x86_64"
    )
    assert result["repository_architecture"] == "x86_64"
    assert result["control_architecture"] == control_arch
    assert result["commit"] == policy["aports"]["commit"]
    assert row["index_record"] == original  # Never normalize or rewrite signed fields.
    assert result["status"] == "CONTROL_AND_PAYLOAD_BINDING_SIGNATURE_REQUIRED"


@pytest.mark.parametrize(
    "key,value",
    [
        ("A", "aarch64"),
        ("P", "other"),
        ("V", "9.9-r0"),
        ("c", "0" * 40),
        ("L", "MIT"),
        ("C", "Q1AAAAAAAAAAAAAAAAAAAAAAAAAAA="),
    ],
)
def test_even_valid_signature_subject_cannot_be_joined_to_different_index_fields(
    inputs, key, value
):
    policy = json.loads((ASSETS / "manifest.json").read_text())
    row = next(
        r
        for r in policy["architecture_inputs"]["x86_64"]["signed_packages"]
        if r["index_record"]["P"] == "ssl_client"
    )
    expected = {**row["index_record"], key: value}
    with pytest.raises((inputs.InputError, inputs.AttributionError)):
        inputs.bind_apk_control(ASSETS / row["artifact"]["file"], expected, "x86_64")


def skalibs_tree(tmp_path):
    graph = json.loads((ASSETS / "static-sources.json").read_text())
    component = next(r for r in graph["components"] if r["name"] == "skalibs")
    with tarfile.open(ASSETS / component["aports"]["file"]) as original:
        body = original.extractfile("main/skalibs/APKBUILD").read()
    path = tmp_path / "recipe.tar"
    with tarfile.open(path, "w") as target:
        member = tarfile.TarInfo("APKBUILD")
        member.size = len(body)
        member.mode = 0o664
        target.addfile(member, io.BytesIO(body))
    return path, component


def test_real_recipe_tree_matches_actual_git_object_and_every_source_checksum(
    inputs, tmp_path
):
    archive, component = skalibs_tree(tmp_path)
    tree = inputs.recipe_tree(
        archive,
        "4a9ee54467ec24599696760c36ce52931ece7a10",
        component["aports"]["original_recipe_sha256"],
    )
    source = component["source"]
    result = inputs.verify_source_inputs(
        tree, {source["filename"]: ASSETS / source["file"]}
    )
    assert result["source_input_count"] == 1 and result["external_input_count"] == 1
    assert result["status"] == "RECIPE_TREE_AND_INPUT_CHECKSUMS_ONLY"


def test_no_recipe_archive_label_can_substitute_another_git_tree(inputs, tmp_path):
    archive, component = skalibs_tree(tmp_path)
    with pytest.raises(inputs.InputError, match="Git tree"):
        inputs.recipe_tree(
            archive, "0" * 40, component["aports"]["original_recipe_sha256"]
        )


@pytest.mark.parametrize("mutation", ["missing", "extra", "wrong-bytes", "symlink"])
def test_source_rows_require_the_entire_exact_external_input_set(
    inputs, tmp_path, mutation
):
    archive, component = skalibs_tree(tmp_path)
    tree = inputs.recipe_tree(
        archive,
        "4a9ee54467ec24599696760c36ce52931ece7a10",
        component["aports"]["original_recipe_sha256"],
    )
    source = component["source"]
    mapping = {source["filename"]: ASSETS / source["file"]}
    if mutation == "missing":
        mapping = {}
    elif mutation == "extra":
        mapping["unreviewed-source.tar.gz"] = ASSETS / source["file"]
    else:
        replacement = tmp_path / "source"
        if mutation == "symlink":
            replacement.symlink_to(ASSETS / source["file"])
        else:
            replacement.write_bytes(b"not the complete source archive")
        mapping[source["filename"]] = replacement
    with pytest.raises(inputs.InputError):
        inputs.verify_source_inputs(tree, mapping)


@pytest.mark.parametrize(
    "name", ["archive.tar.gz", "alpine-devel@lists.alpinelinux.org-6165ee59.rsa.pub"]
)
def test_inline_literal_checksum_is_not_misclassified_as_generated_source(inputs, name):
    digest = "a" * 128
    assert inputs.checksum_rows(f'sha512sums="{digest}  {name}"\n') == {name: digest}


@pytest.mark.parametrize(
    "body",
    [
        'sha512sums="SKIP  archive.tar.gz"\n',
        'sha512sums="${other}"\n',
        'sha512sums="' + "a" * 128 + '  ../operator"\n',
        'sha512sums="' + "a" * 128 + "  a\n" + "b" * 128 + '  a"\n',
    ],
)
def test_unresolved_or_unsafe_source_checksum_lists_cannot_be_accepted(inputs, body):
    with pytest.raises(inputs.InputError):
        inputs.checksum_rows(body)


def test_omitted_checksums_are_not_a_generic_generated_source_exemption(inputs):
    with pytest.raises(inputs.InputError, match="generated"):
        inputs.checksum_rows("pkgname=unknown\npackage() { echo unreviewed; }\n")


def test_actual_generated_recipe_is_bound_to_entire_original_bytes(inputs):
    body = (ROOT / "backend/tests/fixtures/alpine/krb5-conf-APKBUILD").read_text()
    assert (
        hashlib.sha256(body.encode()).hexdigest()
        == "7962206597c478b726f46b0392a73bec21bfa70afa56c62ccb043d1ade3d6286"
    )
    assert inputs.checksum_rows(body) == {}
    with pytest.raises(inputs.InputError):
        inputs.checksum_rows(body.replace("rdns = false", "rdns = true"))


@pytest.mark.parametrize(
    "change", ["extra", "duplicate", "executable", "symlink-escape", "renamed"]
)
def test_recipe_archives_must_reconstruct_the_entire_original_git_tree(
    inputs, tmp_path, change
):
    original, component = skalibs_tree(tmp_path)
    with tarfile.open(original) as archive:
        body = archive.extractfile("APKBUILD").read()
    mutant = tmp_path / "mutant.tar"
    with tarfile.open(mutant, "w") as archive:
        member = tarfile.TarInfo("OTHER" if change == "renamed" else "APKBUILD")
        member.mode = 0o755 if change == "executable" else 0o664
        member.size = len(body)
        archive.addfile(member, io.BytesIO(body))
        if change in {"extra", "duplicate", "symlink-escape"}:
            extra = tarfile.TarInfo(
                "APKBUILD" if change == "duplicate" else "added-source"
            )
            if change == "symlink-escape":
                extra.type = tarfile.SYMTYPE
                extra.linkname = "../operator"
            archive.addfile(extra)
    with pytest.raises(inputs.InputError):
        inputs.recipe_tree(
            mutant,
            "4a9ee54467ec24599696760c36ce52931ece7a10",
            component["aports"]["original_recipe_sha256"],
        )


def path_proof():
    def identity(kind, data):
        return hashlib.sha1(
            kind.encode() + b" " + str(len(data)).encode() + b"\0" + data
        ).hexdigest()

    recipe_tree = "4a9ee54467ec24599696760c36ce52931ece7a10"
    main = (
        b"40000 other\0"
        + bytes.fromhex(recipe_tree)
        + b"40000 skalibs\0"
        + bytes.fromhex(recipe_tree)
    )
    root = b"40000 main\0" + bytes.fromhex(identity("tree", main))
    commit = (
        b"tree "
        + identity("tree", root).encode()
        + b"\nauthor fixture\ncommitter fixture\n\nfixture\n"
    )
    return (
        identity("commit", commit),
        commit,
        {identity("tree", root): root, identity("tree", main): main},
    )


def test_entire_recipe_path_is_bound_to_the_signed_package_commit(inputs):
    commit_id, commit, trees = path_proof()
    assert (
        inputs.bind_git_recipe(
            commit_id, commit, trees, "main/skalibs", origin="skalibs"
        )
        == "4a9ee54467ec24599696760c36ce52931ece7a10"
    )


@pytest.mark.parametrize(
    "change", ["commit", "tree", "missing-tree", "other-path", "testing-path"]
)
def test_recipe_path_cannot_be_switched_under_a_valid_source_commit(inputs, change):
    commit_id, commit, trees = path_proof()
    path = "main/skalibs"
    if change == "commit":
        commit += b"mutated"
    elif change == "tree":
        trees = {key: value + b"mutated" for key, value in trees.items()}
    elif change == "missing-tree":
        trees = {}
    elif change == "other-path":
        path = "main/other"
    else:
        path = "testing/skalibs"
    with pytest.raises(inputs.InputError):
        inputs.bind_git_recipe(commit_id, commit, trees, path, origin="skalibs")
