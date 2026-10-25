"""CVE-2026-102991: dependency floor and harmless URI-boundary regressions.

The ntpath test simulates only Template's normalization boundary, not Windows
filesystem execution. Templates are fixed synthetic text, never untrusted input.
"""

import email
import hashlib
import importlib.metadata
import json
import ntpath
from pathlib import Path
from types import SimpleNamespace

import pytest
from mako import exceptions, template
from mako.lookup import TemplateLookup
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[3]


def test_mako_installed_and_locked_security_floor() -> None:
    assert Version(importlib.metadata.version("mako")) >= Version("1.4.2")
    for name in ("requirements.in", "requirements.txt"):
        requirements = [
            Requirement(line.split("#", 1)[0].strip())
            for line in (ROOT / "backend" / name).read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        mako = next(item for item in requirements if item.name.lower() == "mako")
        assert Version("1.4.2") in mako.specifier
        assert Version("1.4.1") not in mako.specifier


def test_mako_current_notice_matches_recorded_public_metadata() -> None:
    fixture = ROOT / "backend/tests/fixtures/mako_security/METADATA"
    metadata = email.message_from_bytes(fixture.read_bytes())
    assert metadata["Name"] == "Mako"
    assert metadata["Version"] == "1.4.2"
    assert metadata["License-Expression"] == "MIT"
    directory = ROOT / "docker/python-third-party"
    index = json.loads(
        (directory / "artifact-index/pure-python-shared.json").read_text()
    )
    mako = next(item for item in index["distributions"] if item["name"] == "mako")
    assert mako["version"] == metadata["Version"]
    assert (
        mako["current_x86_metadata_sha256"]
        == hashlib.sha256(fixture.read_bytes()).hexdigest()
    )
    for notice in mako["shared_notices"]:
        path = directory / notice["shared_candidate_path"]
        assert "mako-1.4.2.dist-info" in path.parts
        assert hashlib.sha256(path.read_bytes()).hexdigest() == notice["sha256"]


@pytest.mark.parametrize("uri", ["nested/ok.txt", "/nested/ok.txt"])
def test_template_lookup_reads_confined_synthetic_template(
    tmp_path: Path, uri: str
) -> None:
    nested = tmp_path / "templates/nested"
    nested.mkdir(parents=True)
    (nested / "ok.txt").write_text("synthetic confined template")
    lookup = TemplateLookup(directories=[str(tmp_path / "templates")])
    assert lookup.get_template(uri).render() == "synthetic confined template"


@pytest.mark.parametrize("uri", ["../outside.txt", "/../outside.txt"])
def test_template_lookup_rejects_escape_to_synthetic_file(
    tmp_path: Path, uri: str
) -> None:
    directory = tmp_path / "templates"
    directory.mkdir()
    (tmp_path / "outside.txt").write_text("synthetic outside template")
    lookup = TemplateLookup(directories=[str(directory)])
    with pytest.raises(exceptions.TemplateLookupException):
        lookup.get_template(uri)


@pytest.mark.parametrize("uri", ["C:/../../outside.txt", r"C:\..\..\outside.txt"])
def test_template_uri_confinement_uses_posix_semantics_under_ntpath(
    monkeypatch, uri: str
) -> None:
    # Swap only mako.template's module-local OS binding, not the host's os.path.
    # Before 1.4.2 ntpath absorbs the traversal into a drive root; no file is read.
    monkeypatch.setattr(template, "os", SimpleNamespace(path=ntpath))
    with pytest.raises(exceptions.TemplateLookupException, match="outside.*root path"):
        template.Template(text="synthetic fixed text", uri=uri)


def test_template_uri_ntpath_simulation_allows_confined_text(monkeypatch) -> None:
    monkeypatch.setattr(template, "os", SimpleNamespace(path=ntpath))
    assert (
        template.Template(text="synthetic fixed text", uri="C:/nested/ok.txt").render()
        == "synthetic fixed text"
    )
