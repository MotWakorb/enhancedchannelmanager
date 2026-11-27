"""Acquiring public base evidence must retain exact digests and scoped trust."""

import base64
import gzip
import hashlib
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
        "prepare_alpine_python_base", ROOT / "scripts/prepare_alpine_python_base.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_fingerprint_is_checked_independently_of_success_status(
    producer, tmp_path, monkeypatch
):
    commands = []

    def run(args, **kwargs):
        commands.append(args)
        if "--show-keys" in args:
            return SimpleNamespace(
                returncode=0,
                stdout="pub:::::::::\nfpr:::::::::" + producer.RELEASE_KEY + ":\n",
                stderr="",
            )
        if "--import" in args:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(
            returncode=0,
            stdout="[GNUPG:] VALIDSIG "
            + producer.RELEASE_KEY
            + " 2026-10-25 1792955973 0 4 0 1 10 00 "
            + producer.RELEASE_KEY
            + "\n",
            stderr="",
        )

    monkeypatch.setattr(producer.subprocess, "run", run)
    fixture = json.loads(
        gzip.decompress(
            (
                ROOT / "backend/tests/fixtures/alpine/python-base-oci-metadata.json.gz"
            ).read_bytes()
        )
    )
    for name in [
        "source/Python-3.12.16.tar.xz.asc",
        "source/python-release-manager.asc",
    ]:
        p = tmp_path / name
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(base64.b64decode(fixture["files"][name]))
    result = producer.verify_release(tmp_path / "source", tmp_path / "gpg")
    assert result["fingerprint"] == producer.RELEASE_KEY and len(commands) == 3
    assert all("--no-auto-key-retrieve" in c and "--homedir" in c for c in commands)


@pytest.mark.parametrize(
    "mutation",
    ["wrong-key", "nonzero", "bad-status", "missing-validsig", "duplicate-validsig"],
)
def test_successful_gpg_process_is_not_enough_to_authenticate_the_release(
    producer, tmp_path, monkeypatch, mutation
):
    fixture = json.loads(
        gzip.decompress(
            (
                ROOT / "backend/tests/fixtures/alpine/python-base-oci-metadata.json.gz"
            ).read_bytes()
        )
    )
    for name in [
        "source/Python-3.12.16.tar.xz.asc",
        "source/python-release-manager.asc",
    ]:
        p = tmp_path / name
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(base64.b64decode(fixture["files"][name]))

    def run(args, **kwargs):
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
            + " 2026-10-25 1792955973 0 4 0 1 10 00 "
            + producer.RELEASE_KEY
            + "\n"
        )
        if mutation == "bad-status":
            text += "[GNUPG:] BADSIG test\n"
        elif mutation == "missing-validsig":
            text = "[GNUPG:] GOODSIG label-only\n"
        elif mutation == "duplicate-validsig":
            text *= 2
        return SimpleNamespace(
            returncode=1 if mutation == "nonzero" else 0, stdout=text, stderr=""
        )

    monkeypatch.setattr(producer.subprocess, "run", run)
    with pytest.raises(producer.InputError):
        producer.verify_release(tmp_path / "source", tmp_path / "gpg")


def test_digest_failure_retains_failed_download_and_has_no_fallback(
    producer, tmp_path, monkeypatch
):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            if getattr(self, "done", False):
                return b""
            self.done = True
            return b"incorrect original bytes"

    calls = []

    def urlopen(request, **kwargs):
        calls.append(request.full_url)
        return Response()

    monkeypatch.setattr(producer, "urlopen", urlopen)
    target = tmp_path / "download"
    with pytest.raises(producer.InputError, match="hash"):
        producer.fetch("https://official.example/source", target, "0" * 64)
    assert len(calls) == 1 and target.read_bytes() == b"incorrect original bytes"


def test_bearer_token_is_used_only_in_request_not_public_receipt(
    producer, tmp_path, monkeypatch
):
    data = b"original blob"

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            if getattr(self, "done", False):
                return b""
            self.done = True
            return data

    requests = []

    def urlopen(request, **kwargs):
        requests.append(request)
        return Response()

    monkeypatch.setattr(producer, "urlopen", urlopen)
    result = producer.fetch(
        "https://registry.example/blob",
        tmp_path / "blob",
        hashlib.sha256(data).hexdigest(),
        headers={"Authorization": "Bearer secret-fixture"},
    )
    assert requests[0].get_header("Authorization") == "Bearer secret-fixture"
    assert "secret-fixture" not in json.dumps(result)
