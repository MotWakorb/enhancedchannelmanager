"""Tests for ``scripts/check_binary_artifacts.py`` (bead
enhancedchannelmanager-6mqn5).

The guard fails a pull request that ADDS a credential-bearing or bulk binary
artifact. Its value rests on two properties and both are tested here:

1. It fires on a planted artifact. Every blocked container format gets a
   positive case, built at runtime rather than committed, so this test file
   adds no binary to the repository and nothing for the secrets ratchet to
   scan (`docs/pytest_conventions.md`, "Credential Fixtures in Security
   Tests").
2. It stays silent on what this repository legitimately tracks.
   ``TestTrackedTreeIsClean`` runs the guard over the LIVE output of
   ``git ls-files``, so the visual-regression baselines under ``e2e/``, the
   operator-guide screenshots under ``docs/images/``, and the frontend static
   assets are proved clean rather than assumed clean. A guard that red-lines
   on those would be turned off, which is strictly worse than no guard.

The planted-artifact cases are deliberately synthetic. Nothing here decrypts,
opens, or reads a real ECM backup; the ECMBKENC case is eight magic bytes plus
random-looking filler, because eight magic bytes is exactly what the guard
inspects.
"""
from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_binary_artifacts.py"

# A minimal, valid PNG (1x1, fully transparent). Stands in for every
# screenshot and baseline the repository tracks: binary, allowed by type.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


def _load_script_module():
    """Load check_binary_artifacts.py as an ad-hoc module (not a package)."""
    spec = importlib.util.spec_from_file_location(
        "check_binary_artifacts", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_binary_artifacts"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def guard():
    return _load_script_module()


def _write(root: Path, rel_path: str, data: bytes) -> Path:
    full = root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(data)
    return full


def _rules(findings) -> list[str]:
    return sorted(finding.rule for finding in findings)


def _details(findings) -> str:
    return " | ".join(finding.detail for finding in findings)


# --- Blocked container signatures -------------------------------------------


class TestBlockedSignatures:
    """Every high-signal container format the guard sniffs for."""

    def test_ecm_encrypted_backup_envelope(self, guard, tmp_path):
        # The real envelope is MAGIC + version + scrypt params + ciphertext.
        # The guard reads the first eight bytes, so that is what is planted.
        _write(tmp_path, "enc-artifact.zip", b"ECMBKENC" + os.urandom(512))
        findings = guard.scan_paths(
            ["enc-artifact.zip"], {}, repo_root=tmp_path, aggregate=False
        )
        assert _rules(findings) == ["signature"]
        assert "ECMBKENC" in _details(findings)

    def test_dbas_artifact_is_named_as_such(self, guard, tmp_path):
        """A ZIP carrying manifest.json is reported as the DBAS shape."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("manifest.json", '{"schema_version": 1}')
            archive.writestr("settings.json", "{}")
        _write(tmp_path, "std-artifact.zip", buffer.getvalue())
        findings = guard.scan_paths(
            ["std-artifact.zip"], {}, repo_root=tmp_path, aggregate=False
        )
        assert _rules(findings) == ["signature"]
        assert "DBAS backup artifact" in _details(findings)
        assert "manifest.json" in _details(findings)

    def test_plain_zip_without_a_manifest(self, guard, tmp_path):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("trace.trace", "x" * 64)
        _write(tmp_path, "trace.zip", buffer.getvalue())
        findings = guard.scan_paths(
            ["trace.zip"], {}, repo_root=tmp_path, aggregate=False
        )
        assert _rules(findings) == ["signature"]
        assert "ZIP archive" in _details(findings)

    def test_tar_archive(self, guard, tmp_path):
        member = tmp_path / "payload.txt"
        member.write_text("payload", encoding="utf-8")
        bundle = tmp_path / "dump.tar"
        with tarfile.open(bundle, "w") as archive:
            archive.add(member, arcname="payload.txt")
        findings = guard.scan_paths(
            ["dump.tar"], {}, repo_root=tmp_path, aggregate=False
        )
        assert _rules(findings) == ["signature"]
        assert "tar archive" in _details(findings)

    def test_sqlite_database(self, guard, tmp_path):
        import sqlite3

        database = tmp_path / "ecm.db"
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE t (a TEXT)")
        connection.commit()
        connection.close()
        findings = guard.scan_paths(
            ["ecm.db"], {}, repo_root=tmp_path, aggregate=False
        )
        assert _rules(findings) == ["signature"]
        assert "SQLite database" in _details(findings)

    @pytest.mark.parametrize(
        ("name", "magic", "expected"),
        [
            ("dump.gz", b"\x1f\x8b\x08\x00", "gzip archive"),
            ("dump.bz2", b"BZh9", "bzip2 archive"),
            ("dump.xz", b"\xfd7zXZ\x00", "xz archive"),
            ("dump.zst", b"\x28\xb5\x2f\xfd", "zstd archive"),
            ("dump.7z", b"7z\xbc\xaf\x27\x1c", "7-Zip archive"),
            ("dump.rar", b"Rar!\x1a\x07\x00", "RAR archive"),
        ],
    )
    def test_compressed_container_magics(
        self, guard, tmp_path, name, magic, expected
    ):
        _write(tmp_path, name, magic + os.urandom(256))
        findings = guard.scan_paths(
            [name], {}, repo_root=tmp_path, aggregate=False
        )
        assert _rules(findings) == ["signature"]
        assert expected in _details(findings)

    def test_signature_beats_an_allowed_extension(self, guard, tmp_path):
        """Renaming a ZIP to .png does not get it past the guard.

        Extension-only detection is exactly the evasion this rule exists to
        close: the motivating artifact happened to be named `.zip`, but the
        risk is the blob, not the name.
        """
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("manifest.json", "{}")
        _write(tmp_path, "screenshot.png", buffer.getvalue())
        findings = guard.scan_paths(
            ["screenshot.png"], {}, repo_root=tmp_path, aggregate=False
        )
        assert _rules(findings) == ["signature"]

    def test_bzh_in_plain_text_is_not_a_bzip2_archive(self, guard, tmp_path):
        """`BZh` needs its block-size digit; three ASCII letters are not enough."""
        _write(tmp_path, "notes.md", b"BZhang wrote the following note.\n")
        assert (
            guard.scan_paths(
                ["notes.md"], {}, repo_root=tmp_path, aggregate=False
            )
            == []
        )


# --- Binary type rule -------------------------------------------------------


class TestBinaryTypeRule:
    def test_allowed_image_type_passes(self, guard, tmp_path):
        _write(tmp_path, "docs/images/user_guide/new-section/1-shot.png", PNG_BYTES)
        assert (
            guard.scan_paths(
                ["docs/images/user_guide/new-section/1-shot.png"],
                {},
                repo_root=tmp_path,
                aggregate=False,
            )
            == []
        )

    def test_unrecognized_binary_suffix_is_blocked(self, guard, tmp_path):
        _write(tmp_path, "vendor/blob.dat", b"\x00\x01\x02\x03" * 64)
        findings = guard.scan_paths(
            ["vendor/blob.dat"], {}, repo_root=tmp_path, aggregate=False
        )
        assert _rules(findings) == ["binary"]
        assert ".dat" in _details(findings)

    def test_extensionless_binary_is_blocked(self, guard, tmp_path):
        _write(tmp_path, "vendor/helper", b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 64)
        findings = guard.scan_paths(
            ["vendor/helper"], {}, repo_root=tmp_path, aggregate=False
        )
        assert _rules(findings) == ["binary"]

    def test_text_is_never_binary(self, guard, tmp_path):
        _write(tmp_path, "src/app.ts", "export const a = 1;\n".encode("utf-8"))
        assert (
            guard.scan_paths(
                ["src/app.ts"], {}, repo_root=tmp_path, aggregate=False
            )
            == []
        )

    def test_svg_is_text_and_passes(self, guard, tmp_path):
        _write(
            tmp_path,
            "frontend/src/assets/logo.svg",
            b'<svg xmlns="http://www.w3.org/2000/svg"/>',
        )
        assert (
            guard.scan_paths(
                ["frontend/src/assets/logo.svg"],
                {},
                repo_root=tmp_path,
                aggregate=False,
            )
            == []
        )


# --- Size rules -------------------------------------------------------------


class TestSizeRules:
    def test_oversize_file_is_blocked(self, guard, tmp_path):
        _write(tmp_path, "docs/images/huge.png", PNG_BYTES + b"\x00" * (3 * 1024 * 1024))
        findings = guard.scan_paths(
            ["docs/images/huge.png"], {}, repo_root=tmp_path, aggregate=False
        )
        assert _rules(findings) == ["size"]
        assert "per-file ceiling" in _details(findings)

    def test_aggregate_ceiling_catches_a_pile_of_small_files(self, guard, tmp_path):
        """No single member is remarkable; the total is the signal.

        This is the 2,321-file, 3.1 GB `.playwright-mcp/` shape reduced to a
        size the test can build.
        """
        chunk = PNG_BYTES + b"\x00" * (1024 * 1024)
        names = [f"shots/{index}.png" for index in range(30)]
        for name in names:
            _write(tmp_path, name, chunk)
        findings = guard.scan_paths(
            names, {}, repo_root=tmp_path, aggregate=True
        )
        assert _rules(findings) == ["aggregate"]

    def test_aggregate_is_not_applied_when_disabled(self, guard, tmp_path):
        chunk = PNG_BYTES + b"\x00" * (1024 * 1024)
        names = [f"shots/{index}.png" for index in range(30)]
        for name in names:
            _write(tmp_path, name, chunk)
        assert (
            guard.scan_paths(names, {}, repo_root=tmp_path, aggregate=False) == []
        )

    def test_beads_board_is_exempt_from_size_only(self, guard, tmp_path):
        _write(tmp_path, ".beads/issues.jsonl", b'{"id": "x"}\n' * 400_000)
        assert (
            guard.scan_paths(
                [".beads/issues.jsonl"], {}, repo_root=tmp_path, aggregate=True
            )
            == []
        )

    def test_size_exemption_does_not_excuse_a_signature(self, guard, tmp_path):
        _write(tmp_path, ".beads/leftover.zip", b"ECMBKENC" + os.urandom(64))
        findings = guard.scan_paths(
            [".beads/leftover.zip"], {}, repo_root=tmp_path, aggregate=True
        )
        assert _rules(findings) == ["signature"]


# --- Escape hatch -----------------------------------------------------------


class TestAllowlist:
    def test_entry_suppresses_the_finding(self, guard, tmp_path):
        _write(tmp_path, "e2e/fixtures/sample.zip", b"PK\x03\x04" + os.urandom(64))
        allowlist = guard.parse_allowlist(
            "e2e/fixtures/sample.zip  # 68-byte empty fixture, no credentials\n"
        )
        assert allowlist == {
            "e2e/fixtures/sample.zip": "68-byte empty fixture, no credentials"
        }
        assert (
            guard.scan_paths(
                ["e2e/fixtures/sample.zip"],
                allowlist,
                repo_root=tmp_path,
                aggregate=True,
            )
            == []
        )

    def test_entry_without_a_reason_is_rejected(self, guard):
        with pytest.raises(guard.GuardError) as error:
            guard.parse_allowlist("e2e/fixtures/sample.zip\n")
        assert "no `# reason`" in str(error.value)

    def test_entry_with_an_empty_reason_is_rejected(self, guard):
        with pytest.raises(guard.GuardError) as error:
            guard.parse_allowlist("e2e/fixtures/sample.zip #  \n")
        assert "no `# reason`" in str(error.value)

    def test_glob_entries_are_rejected(self, guard):
        with pytest.raises(guard.GuardError) as error:
            guard.parse_allowlist("*.zip  # all the archives\n")
        assert "glob" in str(error.value)

    def test_comments_and_blank_lines_are_ignored(self, guard):
        text = "# header\n\n   \n# another\npath/a.bin  # reason\n"
        assert guard.parse_allowlist(text) == {"path/a.bin": "reason"}

    def test_repository_allowlist_parses_and_is_empty(self, guard):
        """The shipped allowlist is valid and grants no exemptions today."""
        assert guard.load_allowlist(REPO_ROOT) == {}


# --- The negative case that matters: the real tracked tree ------------------


class TestTrackedTreeIsClean:
    """The guard must stay silent on everything this repository tracks.

    Run against live ``git ls-files`` output rather than a hand-picked list,
    so a newly tracked binary type shows up here as a failure to triage
    instead of surfacing as a red PR for whoever added it.
    """

    def test_no_findings_across_every_tracked_file(self, guard):
        paths = guard.tracked_paths(REPO_ROOT)
        assert len(paths) > 1000, "git ls-files returned an implausible tree"
        findings = guard.scan_paths(
            paths, guard.load_allowlist(REPO_ROOT), repo_root=REPO_ROOT, aggregate=False
        )
        assert findings == [], guard_report(guard, findings)

    def test_the_tracked_visual_baselines_are_actually_inspected(self, guard):
        """Guards against the tree scan passing because it scanned nothing."""
        paths = guard.tracked_paths(REPO_ROOT)
        assert any(
            path.startswith("e2e/snapshots/") and path.endswith(".png")
            for path in paths
        )
        assert any(
            path.startswith("docs/images/") and path.endswith(".png")
            for path in paths
        )
        assert any(path.startswith("frontend/public/") for path in paths)


def guard_report(guard, findings) -> str:
    stream = io.StringIO()
    guard.report(findings, stream)
    return "\n" + stream.getvalue()


# --- Policy pin -------------------------------------------------------------


class TestPolicyIsPinned:
    """Weakening the guard has to be a deliberate, reviewable edit.

    `.github/workflows/test.yml` runs HEAD's copy of the script, not the
    merge base's, so that a false-positive fix takes one pull request rather
    than two. The cost of that choice is that a pull request could otherwise
    add `.zip` to the allowed-suffix set and slip an artifact past its own
    guard in a diff that reads as housekeeping. These assertions are what
    makes that edit visible: it fails `Backend Tests`, a required context,
    until the pin is updated in the same diff and reviewed alongside it.

    Adding a format or tightening a ceiling is welcome. Update the pin.
    """

    def test_blocked_formats_are_pinned(self, guard):
        names = {name for _, name in guard._SIGNATURES}
        names.add("bzip2 archive")
        names.add("tar archive")
        assert names == {
            "ECM encrypted backup envelope (ECMBKENC)",
            "SQLite database",
            "7-Zip archive",
            "xz archive",
            "RAR archive",
            "zstd archive",
            "gzip archive",
            "ZIP archive",
            "bzip2 archive",
            "tar archive",
        }

    def test_allowed_binary_suffixes_are_pinned(self, guard):
        assert guard.ALLOWED_BINARY_SUFFIXES == frozenset(
            {
                ".png",
                ".jpg",
                ".jpeg",
                ".gif",
                ".webp",
                ".avif",
                ".ico",
                ".woff",
                ".woff2",
                ".ttf",
                ".otf",
            }
        )
        # No archive, database, or executable type may be waved through by
        # extension. The signature rule would still catch a well-formed one,
        # but a truncated or wrapped artifact might not carry its magic.
        assert not guard.ALLOWED_BINARY_SUFFIXES & {
            ".zip",
            ".gz",
            ".tar",
            ".7z",
            ".rar",
            ".db",
            ".sqlite",
            ".bin",
            ".exe",
        }

    def test_size_ceilings_are_pinned(self, guard):
        assert guard.MAX_FILE_BYTES == 2 * 1024 * 1024
        assert guard.MAX_TOTAL_BYTES == 25 * 1024 * 1024

    def test_size_exempt_prefixes_are_pinned(self, guard):
        assert guard.SIZE_EXEMPT_PREFIXES == (".beads/",)


# --- End-to-end through main(), in a throwaway git repository ---------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture()
def sandbox(tmp_path: Path) -> Path:
    """A tiny git repository with a `base` branch to diff against."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "base")
    _git(repo, "config", "user.email", "guard@example.invalid")
    _git(repo, "config", "user.name", "Guard Test")
    _write(repo, "README.md", b"# sandbox\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "change")
    return repo


class TestEndToEnd:
    def test_clean_change_exits_zero(self, guard, sandbox, capsys):
        _write(sandbox, "docs/images/new.png", PNG_BYTES)
        _git(sandbox, "add", "docs/images/new.png")
        _git(sandbox, "commit", "-q", "-m", "add a screenshot")
        exit_code = guard.main(
            ["--repo-root", str(sandbox), "--base-ref", "base"]
        )
        assert exit_code == 0
        assert "PASS" in capsys.readouterr().out

    def test_planted_artifact_exits_one_and_names_the_remedy(
        self, guard, sandbox, capsys
    ):
        _write(sandbox, ".playwright-mcp/enc-artifact.zip", b"ECMBKENC" + os.urandom(256))
        _git(sandbox, "add", "-A")
        _git(sandbox, "commit", "-q", "-m", "oops")
        exit_code = guard.main(
            ["--repo-root", str(sandbox), "--base-ref", "base"]
        )
        assert exit_code == 1
        stderr = capsys.readouterr().err
        # Names the offending path, why it is blocked, and the escape hatch.
        assert ".playwright-mcp/enc-artifact.zip" in stderr
        assert "ECMBKENC" in stderr
        assert ".binary-artifacts.allowlist" in stderr
        assert "enhancedchannelmanager-6mqn5" in stderr

    def test_an_untracked_artifact_is_caught_before_it_is_ever_staged(
        self, guard, sandbox, capsys
    ):
        """The motivating incident: untracked, unignored, one `git add -A` away."""
        _write(sandbox, "drill/enc-artifact.zip", b"ECMBKENC" + os.urandom(256))
        exit_code = guard.main(
            ["--repo-root", str(sandbox), "--base-ref", "base"]
        )
        assert exit_code == 1
        assert "drill/enc-artifact.zip" in capsys.readouterr().err

    def test_a_gitignored_artifact_is_already_contained(self, guard, sandbox, capsys):
        _write(sandbox, ".gitignore", b".playwright-mcp/\n")
        _write(sandbox, ".playwright-mcp/enc-artifact.zip", b"ECMBKENC" + os.urandom(256))
        _git(sandbox, "add", ".gitignore")
        _git(sandbox, "commit", "-q", "-m", "ignore the drill directory")
        assert (
            guard.main(["--repo-root", str(sandbox), "--base-ref", "base"]) == 0
        )

    def test_allowlisted_artifact_passes_end_to_end(self, guard, sandbox, capsys):
        _write(
            sandbox,
            ".binary-artifacts.allowlist",
            b"fixtures/sample.zip  # empty fixture, no credentials\n",
        )
        _write(sandbox, "fixtures/sample.zip", b"PK\x03\x04" + os.urandom(64))
        _git(sandbox, "add", "-A")
        _git(sandbox, "commit", "-q", "-m", "add an allowlisted fixture")
        assert (
            guard.main(["--repo-root", str(sandbox), "--base-ref", "base"]) == 0
        )
        assert "1 path(s) exempted" in capsys.readouterr().out

    def test_an_unusable_allowlist_fails_the_run(self, guard, sandbox, capsys):
        _write(sandbox, ".binary-artifacts.allowlist", b"*.zip  # everything\n")
        _git(sandbox, "add", "-A")
        _git(sandbox, "commit", "-q", "-m", "bad allowlist")
        assert (
            guard.main(["--repo-root", str(sandbox), "--base-ref", "base"]) == 1
        )
        assert "cannot run" in capsys.readouterr().err

    def test_all_mode_inspects_the_tracked_tree(self, guard, sandbox, capsys):
        _write(sandbox, "kept.zip", b"PK\x03\x04" + os.urandom(64))
        _git(sandbox, "add", "-A")
        _git(sandbox, "commit", "-q", "-m", "track an archive")
        assert guard.main(["--repo-root", str(sandbox), "--all"]) == 1
        assert "kept.zip" in capsys.readouterr().err

    def test_paths_mode_inspects_named_paths(self, guard, sandbox, capsys):
        _write(sandbox, "loose.zip", b"PK\x03\x04" + os.urandom(64))
        assert (
            guard.main(["--repo-root", str(sandbox), "--paths", "loose.zip"]) == 1
        )
        assert "loose.zip" in capsys.readouterr().err

    def test_an_unresolvable_base_ref_fails_loudly(self, guard, sandbox, capsys):
        assert (
            guard.main(["--repo-root", str(sandbox), "--base-ref", "origin/nope"])
            == 1
        )
        stderr = capsys.readouterr().err
        assert "cannot run" in stderr
        assert "fetch-depth: 0" in stderr
