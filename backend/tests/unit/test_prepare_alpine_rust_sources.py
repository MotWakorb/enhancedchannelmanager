"""Rust release authentication stays independent and builder-only."""

import base64
import gzip
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def producer(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "prepare_alpine_rust_sources", ROOT / "scripts/prepare_alpine_rust_sources.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def recorded(tmp_path):
    fixture = json.loads(
        gzip.decompress(
            (
                ROOT
                / "backend/tests/fixtures/alpine/rust-release-public-evidence.json.gz"
            ).read_bytes()
        )
    )
    root = tmp_path / "bundle"
    root.mkdir()
    for name, encoded in fixture["files"].items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(encoded))
    return root


def install_gpg_fixture(producer, monkeypatch, mutation):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if "--show-keys" in args:
            key = "0" * 40 if mutation == "wrong-key" else producer.RELEASE_KEY
            return SimpleNamespace(
                returncode=0,
                stdout="pub:::::::::\nfpr:::::::::" + key + ":\n",
                stderr="",
            )
        if "--import" in args:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        text = (
            "[GNUPG:] VALIDSIG "
            + producer.RELEASE_KEY
            + " 2026-05-28 1780000000 0 4 0 1 10 00 "
            + producer.RELEASE_KEY
            + "\n"
        )
        if mutation == "bad-status":
            text += "[GNUPG:] BADSIG invalid\n"
        elif mutation == "missing-validsig":
            text = "[GNUPG:] GOODSIG label-only\n"
        elif mutation == "duplicate-validsig":
            text *= 2
        elif mutation == "wrong-signing-primary":
            text = text.rsplit(" ", 1)[0] + " " + "0" * 40 + "\n"
        return SimpleNamespace(
            returncode=1 if mutation == "nonzero" else 0, stdout=text, stderr=""
        )

    monkeypatch.setattr(producer.subprocess, "run", run)
    return calls


def test_both_original_archives_require_scoped_release_signature_verification(
    producer, recorded, tmp_path, monkeypatch
):
    calls = install_gpg_fixture(producer, monkeypatch, None)
    result = producer.verify_releases(recorded, tmp_path / "gnupg")
    assert set(result) == {"1.95.0", "1.96.0"} and len(calls) == 4
    assert all(
        "--no-auto-key-retrieve" in args and "--homedir" in args for args, _ in calls
    )
    assert all(
        kwargs["env"]["GNUPGHOME"] == str(tmp_path / "gnupg") for _, kwargs in calls
    )
    assert all(
        row["fingerprint"] == producer.RELEASE_KEY and row["exit"] == 0
        for row in result.values()
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong-key",
        "bad-status",
        "missing-validsig",
        "duplicate-validsig",
        "wrong-signing-primary",
        "nonzero",
    ],
)
def test_successful_gpg_status_or_version_label_alone_is_never_release_authentication(
    producer, recorded, tmp_path, monkeypatch, mutation
):
    install_gpg_fixture(producer, monkeypatch, mutation)
    with pytest.raises(producer.InputError):
        producer.verify_releases(recorded, tmp_path / "gnupg")


def test_preexisting_keyring_cannot_supply_ambient_trust(producer, recorded, tmp_path):
    home = tmp_path / "gnupg"
    home.mkdir()
    with pytest.raises(producer.InputError):
        producer.verify_releases(recorded, home)
