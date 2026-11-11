"""A delivered APK/source graph must carry every exact byte and Git subject."""

import base64
import gzip
import hashlib
import importlib.util
import io
import json
import shutil
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ASSETS = ROOT / "docker/alpine-busybox"


@pytest.fixture
def consumer(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "verify_alpine_external_sources",
        ROOT / "scripts/verify_alpine_external_sources.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bundle(tmp_path, consumer, monkeypatch):
    root = tmp_path / "bundle"
    root.mkdir()
    proof = json.loads(
        gzip.decompress(
            (
                ROOT
                / "backend/tests/fixtures/alpine/external-source-skalibs-git-proof.json.gz"
            ).read_bytes()
        )
    )
    policy = json.loads((ASSETS / "manifest.json").read_text())
    graph = json.loads((ASSETS / "static-sources.json").read_text())
    package = next(
        p
        for p in graph["architecture_inputs"]["x86_64"]
        if p["index_record"]["P"] == "skalibs-static"
    )
    component = next(c for c in graph["components"] if c["name"] == "skalibs")

    def write(name, data):
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return {"path": name, "sha256": hashlib.sha256(data).hexdigest()}

    for name, text in proof["public_keys"].items():
        write("keys/" + name, text.encode())
    index = write(
        "indexes/main-APKINDEX.tar.gz",
        (
            ASSETS / policy["architecture_inputs"]["x86_64"]["index"]["file"]
        ).read_bytes(),
    )
    archive = write(
        "packages/skalibs-static.apk",
        (ASSETS / package["artifact"]["file"]).read_bytes(),
    )
    source = write(
        "sources/skalibs.tar.gz", (ASSETS / component["source"]["file"]).read_bytes()
    )
    objects = []
    for obj in proof["git_objects"]:
        row = write(obj["path"], base64.b64decode(obj["base64"]))
        objects.append({**row, "kind": obj["kind"], "git_id": obj["git_id"]})
    with tarfile.open(ASSETS / component["aports"]["file"]) as original:
        body = original.extractfile("main/skalibs/APKBUILD").read()
    path = root / "recipes/skalibs.tar"
    path.parent.mkdir()
    with tarfile.open(path, "w") as tar:
        m = tarfile.TarInfo("APKBUILD")
        m.size = len(body)
        m.mode = 0o664
        tar.addfile(m, io.BytesIO(body))
    recipe = {
        "path": "recipes/skalibs.tar",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "apkbuild_sha256": component["aports"]["original_recipe_sha256"],
    }
    fields = package["index_record"]
    # Synthetic installed-state projection of a recorded real signed SDK package.
    # The actual full-candidate consumer integration is a separate native receipt.
    capture = {
        "installed_database": "\n".join(k + ":" + v for k, v in fields.items())
        + "\n\n",
        "packages": [
            {
                "name": fields["P"],
                "version": fields["V"],
                "arch": fields["A"],
                "description": fields["T"],
                "license": fields["L"],
                "origin": fields["o"],
                "url": fields["U"],
                "file-size": int(fields["S"]),
            }
        ],
    }
    inventory = write("inventory.json", (json.dumps(capture) + "\n").encode())
    manifest = {
        "schema_version": 1,
        "architecture": "x86_64",
        "inventory": inventory,
        "indexes": [{"repository": "main", **index}],
        "packages": [{"repository": "main", **archive, "index_record": fields}],
        "source_groups": [
            {
                "origin": "skalibs",
                "commit": proof["commit"],
                "package_version": fields["V"],
                "packages": [fields["P"]],
                "git_path": "main/skalibs",
                "git_objects": objects,
                "recipe": recipe,
                "sources": {component["source"]["filename"]: source},
            }
        ],
    }
    (root / "index.json").write_text(json.dumps(manifest))
    calls = []
    monkeypatch.setattr(
        consumer, "verify_signature", lambda path, keys: calls.append((path, keys))
    )
    return root, manifest, capture, calls


def test_recorded_complete_source_graph_is_not_full_runtime_clearance(consumer, bundle):
    root, _, capture, calls = bundle
    result = consumer.verify(root, capture)
    assert result["status"] == "EXTERNAL_APK_SOURCE_DELIVERY_ONLY"
    assert result["full_runtime_source_acceptance"] is False
    assert (
        result["external_packages"]
        == result["source_groups"]
        == result["source_input_count"]
        == 1
    )
    assert len(calls) == 2 and all(keys == root / "keys" for _, keys in calls)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-apk",
        "changed-apk",
        "changed-source",
        "missing-source",
        "changed-recipe",
        "extra-file",
        "symlink",
        "changed-key",
        "missing-key",
        "extra-key",
    ],
)
def test_delivered_bytes_and_trust_evidence_are_exact_not_receipt_labels(
    consumer, bundle, mutation
):
    root, manifest, capture, _ = bundle
    apk = root / manifest["packages"][0]["path"]
    source = root / next(iter(manifest["source_groups"][0]["sources"].values()))["path"]
    key = next((root / "keys").iterdir())
    if mutation == "missing-apk":
        apk.unlink()
    elif mutation == "changed-apk":
        apk.write_bytes(b"other package bytes")
    elif mutation == "changed-source":
        source.write_bytes(b"other source bytes")
    elif mutation == "missing-source":
        source.unlink()
    elif mutation == "changed-recipe":
        (root / manifest["source_groups"][0]["recipe"]["path"]).write_bytes(
            b"other recipe bytes"
        )
    elif mutation == "extra-file":
        (root / "unreviewed").write_bytes(b"extra")
    elif mutation == "symlink":
        source.unlink()
        source.symlink_to("/etc/passwd")
    elif mutation == "changed-key":
        key.write_bytes(b"other public key")
    elif mutation == "missing-key":
        key.unlink()
    else:
        shutil.copy2(key, root / "keys/unapproved.pub")
    with pytest.raises(consumer.InputError):
        consumer.verify(root, capture)


@pytest.mark.parametrize(
    "mutation",
    [
        "drop-package",
        "duplicate-package",
        "wrong-origin",
        "wrong-commit",
        "wrong-path-origin",
        "drop-source-group",
        "duplicate-source-group",
        "fake-clearance",
        "unsafe-path",
    ],
)
def test_manifest_cannot_skip_or_relabel_signed_subjects(consumer, bundle, mutation):
    root, manifest, capture, _ = bundle
    if mutation == "drop-package":
        manifest["packages"] = []
    elif mutation == "duplicate-package":
        manifest["packages"] *= 2
    elif mutation == "wrong-origin":
        manifest["source_groups"][0]["origin"] = "other"
    elif mutation == "wrong-commit":
        manifest["source_groups"][0]["commit"] = "0" * 40
    elif mutation == "wrong-path-origin":
        manifest["source_groups"][0]["git_path"] = "main/other"
    elif mutation == "drop-source-group":
        manifest["source_groups"] = []
    elif mutation == "duplicate-source-group":
        manifest["source_groups"] *= 2
    elif mutation == "fake-clearance":
        manifest["full_runtime_source_acceptance"] = True
    else:
        manifest["packages"][0]["path"] = "../operator.apk"
    (root / "index.json").write_text(json.dumps(manifest))
    with pytest.raises(consumer.InputError):
        consumer.verify(root, capture)


def test_live_inventory_is_not_replaced_by_the_build_capture(consumer, bundle):
    root, _, capture, _ = bundle
    capture["packages"][0]["version"] = "unrelated"
    with pytest.raises(consumer.InputError):
        consumer.verify(root, capture)


def test_signature_failure_never_becomes_source_acceptance(
    consumer, bundle, monkeypatch
):
    root, _, capture, _ = bundle

    def deny(*args):
        raise consumer.InputError("BAD signature")

    monkeypatch.setattr(consumer, "verify_signature", deny)
    with pytest.raises(consumer.InputError, match="BAD signature"):
        consumer.verify(root, capture)


@pytest.mark.parametrize(
    "mutation", ["source-and-hash", "recipe-and-hashes", "commit-and-hash"]
)
def test_rehashed_manifest_cannot_replace_authenticated_source_bytes(
    consumer, bundle, mutation
):
    root, manifest, capture, _ = bundle
    group = manifest["source_groups"][0]
    if mutation == "source-and-hash":
        row = next(iter(group["sources"].values()))
        path = root / row["path"]
        path.write_bytes(b"substituted source archive")
        row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    elif mutation == "recipe-and-hashes":
        row = group["recipe"]
        path = root / row["path"]
        with tarfile.open(path) as tar:
            body = tar.extractfile("APKBUILD").read()
        changed = body.replace(b"pkgver=2.15.0.0", b"pkgver=9.15.0.0")
        assert changed != body
        with tarfile.open(path, "w") as tar:
            member = tarfile.TarInfo("APKBUILD")
            member.size = len(changed)
            member.mode = 0o664
            tar.addfile(member, io.BytesIO(changed))
        row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        row["apkbuild_sha256"] = hashlib.sha256(changed).hexdigest()
    else:
        row = next(row for row in group["git_objects"] if row["kind"] == "commit")
        path = root / row["path"]
        path.write_bytes(path.read_bytes() + b"changed source commit\n")
        row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (root / "index.json").write_text(json.dumps(manifest))
    with pytest.raises(consumer.InputError):
        consumer.verify(root, capture)
