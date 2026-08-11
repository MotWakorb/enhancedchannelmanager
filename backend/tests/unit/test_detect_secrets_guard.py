"""Contracts for the generic secret-scanning CI layer (bead h9av3)."""

from __future__ import annotations

import json
import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "test.yml"
BASELINE = REPO_ROOT / ".secrets.baseline"
HELPER = REPO_ROOT / "scripts" / "check_secrets.py"
SCANNER = Path(sys.executable).with_name("detect-secrets")
if not SCANNER.exists():
    SCANNER = Path(shutil.which("detect-secrets") or "detect-secrets")


def test_workflow_pins_scanner_and_forbids_network_verification():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "detect-secrets==1.5.0" in workflow
    assert 'git show "origin/$BASE_REF:scripts/check_secrets.py"' in workflow
    assert '--repo-root "$GITHUB_WORKSPACE" --base-ref "origin/$BASE_REF"' in workflow
    assert 'python3 scripts/check_secrets.py --base-ref "origin/$BASE_REF"' in workflow


def test_workflow_executes_helper_instead_of_an_inline_scanner_pipeline():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "xargs -0 -r detect-secrets-hook" not in workflow


def test_changed_and_deleted_path_queries_disable_rename_collapsing():
    source = HELPER.read_text(encoding="utf-8")
    assert source.count('"--no-renames"') == 2


def test_baseline_is_pinned_and_contains_hashes_not_values():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert baseline["version"] == "1.5.0"
    findings = [item for items in baseline["results"].values() for item in items]
    assert findings, "an unexpectedly empty baseline disables the ratchet contract"
    for finding in findings:
        assert set(finding) <= {
            "type",
            "filename",
            "hashed_secret",
            "is_verified",
            "line_number",
        }
        assert re.fullmatch(r"[0-9a-f]{40}", finding["hashed_secret"])


def test_baseline_excludes_itself_from_scanning():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert {
        "path": "detect_secrets.filters.common.is_baseline_file",
        "filename": ".secrets.baseline",
    } in baseline["filters_used"]


def test_candidate_inline_allowlist_filter_is_not_policy_authority():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert {
        "path": "detect_secrets.filters.allowlist.is_line_allowlisted"
    } not in baseline["filters_used"]


def test_baseline_update_acceptance_rejects_replaced_finding():
    spec = importlib.util.spec_from_file_location("check_secrets", HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    before = json.loads(BASELINE.read_text(encoding="utf-8"))
    after = json.loads(json.dumps(before))
    finding = next(item for items in after["results"].values() for item in items)
    finding["hashed_secret"] = "0" * 40
    assert not module._is_strict_finding_removal(before, after)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True
    )


def _run_guard(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "--repo-root",
            str(repo),
            "--base-ref",
            "base",
            "--scanner",
            str(SCANNER),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def _write_shrunken_candidate(repo: Path) -> None:
    path = repo / ".secrets.baseline"
    baseline = json.loads(path.read_text(encoding="utf-8"))
    baseline["results"].pop("legacy.py")
    path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def guarded_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "ci@example.com")
    _git(tmp_path, "config", "user.name", "CI")
    (tmp_path / "README.md").write_text("# Synthetic repository\n", encoding="utf-8")
    legacy = "AKIA" + "BASELINEONLYABCD"
    (tmp_path / "legacy.py").write_text(
        "# Synthetic legacy configuration\n"
        "mode = \"fixture\"\n"
        f'legacy_token = "{legacy}"\n'
        "enabled = True\n"
        "retries = 3\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "README.md", "legacy.py")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "branch", "base")
    refresh = subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "--repo-root",
            str(tmp_path),
            "--base-ref",
            "base",
            "--scanner",
            str(SCANNER),
            "--refresh-bootstrap",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert refresh.returncode == 0, refresh.stderr
    _git(tmp_path, "add", ".secrets.baseline")
    _git(tmp_path, "commit", "-q", "-m", "bootstrap")
    return tmp_path


@pytest.mark.parametrize("kind", ["private-key", "jwt"])
def test_exact_helper_detects_delegated_secret_without_echoing_value(
    guarded_repo: Path, kind: str
):
    if kind == "private-key":
        synthetic = "-----BEGIN " + "RSA PRIVATE KEY-----"
    else:
        synthetic = ".".join(
            [
                "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
                "eyJzdWIiOiJzeW50aGV0aWMtaGVscGVyLXRlc3QifQ",
                "Qs4Tv7" * 7,
            ]
        )
    (guarded_repo / "changed.txt").write_text(synthetic + "\n", encoding="utf-8")
    _git(guarded_repo, "add", "changed.txt")
    _git(guarded_repo, "commit", "-q", "-m", "candidate")
    result = _run_guard(guarded_repo)
    assert result.returncode != 0
    assert "changed.txt" in result.stdout + result.stderr
    assert synthetic not in result.stdout + result.stderr


def test_exact_helper_allows_clean_changed_file(guarded_repo: Path):
    (guarded_repo / "changed.txt").write_text("ordinary text\n", encoding="utf-8")
    _git(guarded_repo, "add", "changed.txt")
    _git(guarded_repo, "commit", "-q", "-m", "candidate")
    assert _run_guard(guarded_repo).returncode == 0


def test_exact_helper_scans_option_like_filename(guarded_repo: Path):
    synthetic = "-----BEGIN " + "RSA PRIVATE KEY-----"
    path = guarded_repo / "--help"
    path.write_text(synthetic + "\n", encoding="utf-8")
    _git(guarded_repo, "add", "--", "--help")
    _git(guarded_repo, "commit", "-q", "-m", "candidate")
    result = _run_guard(guarded_repo)
    assert result.returncode != 0
    assert "--help" in result.stdout + result.stderr
    assert synthetic not in result.stdout + result.stderr


def test_same_pr_inline_allowlist_cannot_suppress_secret(guarded_repo: Path):
    synthetic = "AKIA" + "SYNTHETICABCDEFG"
    (guarded_repo / "changed.py").write_text(
        f'token = "{synthetic}"  # pragma: allowlist secret\n', encoding="utf-8"
    )
    _git(guarded_repo, "add", "changed.py")
    _git(guarded_repo, "commit", "-q", "-m", "candidate")
    result = _run_guard(guarded_repo)
    assert result.returncode != 0
    assert synthetic not in result.stdout + result.stderr


def test_clean_followup_uses_nonempty_base_baseline(guarded_repo: Path):
    _git(guarded_repo, "branch", "-f", "base", "HEAD")
    (guarded_repo / "clean.txt").write_text("ordinary follow-up\n", encoding="utf-8")
    _git(guarded_repo, "add", "clean.txt")
    _git(guarded_repo, "commit", "-q", "-m", "follow-up")
    result = _run_guard(guarded_repo)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mutation", ["finding", "metadata", "corrupt", "delete"])
def test_clean_pr_cannot_modify_or_remove_baseline_authority(
    guarded_repo: Path, mutation: str
):
    _git(guarded_repo, "branch", "-f", "base", "HEAD")
    baseline_path = guarded_repo / ".secrets.baseline"
    if mutation == "delete":
        baseline_path.unlink()
        _git(guarded_repo, "add", "-u", ".secrets.baseline")
    elif mutation == "corrupt":
        baseline_path.write_text("{invalid metadata\n", encoding="utf-8")
        _git(guarded_repo, "add", ".secrets.baseline")
    else:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        if mutation == "finding":
            baseline["results"]["invented.py"] = [
                {
                    "type": "AWS Access Key",
                    "filename": "invented.py",
                    "hashed_secret": "0" * 40,
                    "is_verified": False,
                }
            ]
        else:
            baseline["version"] = "0.0.0"
        baseline_path.write_text(
            json.dumps(baseline, indent=2) + "\n", encoding="utf-8"
        )
        _git(guarded_repo, "add", ".secrets.baseline")
    (guarded_repo / "clean.txt").write_text("ordinary change\n", encoding="utf-8")
    _git(guarded_repo, "add", "clean.txt")
    _git(guarded_repo, "commit", "-q", "-m", "tamper with authority")
    assert _run_guard(guarded_repo).returncode != 0


def _commit_symlinked_baseline(guarded_repo: Path) -> None:
    baseline = guarded_repo / ".secrets.baseline"
    store = guarded_repo / ".authority-store.json"
    baseline.rename(store)
    (guarded_repo / ".gitignore").write_text(
        "/.authority-store.json\n", encoding="utf-8"
    )
    baseline.symlink_to(store.name)
    _git(guarded_repo, "add", ".gitignore", ".secrets.baseline")
    _git(guarded_repo, "add", "-f", ".authority-store.json")


def test_clean_pr_rejects_symlinked_candidate_baseline(guarded_repo: Path):
    _git(guarded_repo, "branch", "-f", "base", "HEAD")
    _commit_symlinked_baseline(guarded_repo)
    (guarded_repo / "clean.txt").write_text("ordinary change\n", encoding="utf-8")
    _git(guarded_repo, "add", "clean.txt")
    _git(guarded_repo, "commit", "-q", "-m", "symlink candidate")
    result = _run_guard(guarded_repo)
    assert result.returncode != 0
    assert "Location: .secrets.baseline" in result.stderr


def test_future_base_rejects_symlinked_recorded_authority(guarded_repo: Path):
    _commit_symlinked_baseline(guarded_repo)
    _git(guarded_repo, "commit", "-q", "-m", "poisoned base")
    _git(guarded_repo, "branch", "-f", "base", "HEAD")
    (guarded_repo / "clean.txt").write_text("ordinary follow-up\n", encoding="utf-8")
    _git(guarded_repo, "add", "clean.txt")
    _git(guarded_repo, "commit", "-q", "-m", "clean follow-up")
    result = _run_guard(guarded_repo)
    assert result.returncode != 0
    assert "regular non-executable Git blob" in result.stderr


def test_clean_pr_rejects_executable_candidate_baseline(guarded_repo: Path):
    _git(guarded_repo, "branch", "-f", "base", "HEAD")
    (guarded_repo / ".secrets.baseline").chmod(0o755)
    _git(guarded_repo, "add", ".secrets.baseline")
    (guarded_repo / "clean.txt").write_text("ordinary change\n", encoding="utf-8")
    _git(guarded_repo, "add", "clean.txt")
    _git(guarded_repo, "commit", "-q", "-m", "executable candidate")
    result = _run_guard(guarded_repo)
    assert result.returncode != 0
    assert "Location: .secrets.baseline" in result.stderr


@pytest.mark.parametrize("cleanup", ["redact", "delete"])
def test_post_bootstrap_tolerated_finding_cleanup_passes(
    guarded_repo: Path, cleanup: str
):
    _git(guarded_repo, "branch", "-f", "base", "HEAD")
    legacy = guarded_repo / "legacy.py"
    if cleanup == "redact":
        legacy.write_text('legacy_token = "REDACTED"\n', encoding="utf-8")
        _git(guarded_repo, "add", "legacy.py")
    else:
        legacy.unlink()
        _git(guarded_repo, "add", "-u", "legacy.py")
    _write_shrunken_candidate(guarded_repo)
    _git(guarded_repo, "add", ".secrets.baseline")
    _git(guarded_repo, "commit", "-q", "-m", "remove tolerated finding")
    result = _run_guard(guarded_repo)
    assert result.returncode == 0, result.stderr
    _git(guarded_repo, "branch", "-f", "base", "HEAD")
    (guarded_repo / "clean.txt").write_text("ordinary follow-up\n", encoding="utf-8")
    _git(guarded_repo, "add", "clean.txt")
    _git(guarded_repo, "commit", "-q", "-m", "clean follow-up")
    assert _run_guard(guarded_repo).returncode == 0


def test_cleanup_without_persisted_baseline_update_is_blocked(guarded_repo: Path):
    _git(guarded_repo, "branch", "-f", "base", "HEAD")
    (guarded_repo / "legacy.py").write_text(
        'legacy_token = "REDACTED"\n', encoding="utf-8"
    )
    _git(guarded_repo, "add", "legacy.py")
    _git(guarded_repo, "commit", "-q", "-m", "unpersisted cleanup")
    assert _run_guard(guarded_repo).returncode != 0


@pytest.mark.parametrize("mutation", ["addition", "replacement", "policy"])
def test_cleanup_rejects_noncanonical_candidate_baseline(
    guarded_repo: Path, mutation: str
):
    _git(guarded_repo, "branch", "-f", "base", "HEAD")
    (guarded_repo / "legacy.py").write_text(
        'legacy_token = "REDACTED"\n', encoding="utf-8"
    )
    _write_shrunken_candidate(guarded_repo)
    path = guarded_repo / ".secrets.baseline"
    baseline = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "addition":
        baseline["results"]["invented.py"] = [
            {
                "type": "AWS Access Key",
                "filename": "invented.py",
                "hashed_secret": "0" * 40,
                "is_verified": False,
            }
        ]
    elif mutation == "replacement":
        baseline["results"]["legacy.py"] = [
            {
                "type": "AWS Access Key",
                "filename": "legacy.py",
                "hashed_secret": "1" * 40,
                "is_verified": False,
            }
        ]
    else:
        baseline["plugins_used"] = [
            item
            for item in baseline["plugins_used"]
            if item["name"] != "AWSKeyDetector"
        ]
    path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    _git(guarded_repo, "add", "legacy.py", ".secrets.baseline")
    _git(guarded_repo, "commit", "-q", "-m", "crafted cleanup baseline")
    assert _run_guard(guarded_repo).returncode != 0


def test_removal_plus_new_secret_remains_blocked(guarded_repo: Path):
    _git(guarded_repo, "branch", "-f", "base", "HEAD")
    (guarded_repo / "legacy.py").write_text(
        'legacy_token = "REDACTED"\n', encoding="utf-8"
    )
    synthetic = "-----BEGIN " + "RSA PRIVATE KEY-----"
    (guarded_repo / "new.txt").write_text(synthetic + "\n", encoding="utf-8")
    _write_shrunken_candidate(guarded_repo)
    _git(guarded_repo, "add", "legacy.py", "new.txt", ".secrets.baseline")
    _git(guarded_repo, "commit", "-q", "-m", "replace tolerated finding")
    result = _run_guard(guarded_repo)
    assert result.returncode != 0
    assert synthetic not in result.stdout + result.stderr


def test_replacement_secret_remains_blocked(guarded_repo: Path):
    _git(guarded_repo, "branch", "-f", "base", "HEAD")
    synthetic = "AKIA" + "REPLACEMENTABCDE"
    (guarded_repo / "legacy.py").write_text(
        f'legacy_token = "{synthetic}"\n', encoding="utf-8"
    )
    _write_shrunken_candidate(guarded_repo)
    _git(guarded_repo, "add", "legacy.py", ".secrets.baseline")
    _git(guarded_repo, "commit", "-q", "-m", "replace tolerated finding")
    result = _run_guard(guarded_repo)
    assert result.returncode != 0
    assert synthetic not in result.stdout + result.stderr


def test_rename_and_redaction_persists_shrink_then_clean_followup_passes(
    guarded_repo: Path,
):
    _git(guarded_repo, "branch", "-f", "base", "HEAD")
    _git(guarded_repo, "mv", "legacy.py", "renamed.py")
    (guarded_repo / "renamed.py").write_text(
        "# Synthetic legacy configuration\n"
        "mode = \"fixture\"\n"
        'legacy_token = "REDACTED"\n'
        "enabled = True\n"
        "retries = 3\n",
        encoding="utf-8",
    )
    _write_shrunken_candidate(guarded_repo)
    _git(guarded_repo, "add", "renamed.py", ".secrets.baseline")
    _git(guarded_repo, "commit", "-q", "-m", "rename and remove debt")
    assert _run_guard(guarded_repo).returncode == 0
    _git(guarded_repo, "branch", "-f", "base", "HEAD")
    (guarded_repo / "clean.txt").write_text("ordinary follow-up\n", encoding="utf-8")
    _git(guarded_repo, "add", "clean.txt")
    _git(guarded_repo, "commit", "-q", "-m", "clean follow-up")
    assert _run_guard(guarded_repo).returncode == 0


def test_rename_source_removal_does_not_hide_new_destination_secret(
    guarded_repo: Path,
):
    _git(guarded_repo, "branch", "-f", "base", "HEAD")
    _git(guarded_repo, "mv", "legacy.py", "renamed.py")
    synthetic = "-----BEGIN " + "RSA PRIVATE KEY-----"
    (guarded_repo / "renamed.py").write_text(synthetic + "\n", encoding="utf-8")
    _write_shrunken_candidate(guarded_repo)
    _git(guarded_repo, "add", "renamed.py", ".secrets.baseline")
    _git(guarded_repo, "commit", "-q", "-m", "rename and replace debt")
    result = _run_guard(guarded_repo)
    assert result.returncode != 0
    assert synthetic not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "mutation",
    ["add-suppression", "remove-plugin", "remove-filter", "change-threshold"],
)
def test_head_baseline_cannot_weaken_base_authority(
    guarded_repo: Path, mutation: str
):
    synthetic = "-----BEGIN " + "RSA PRIVATE KEY-----"
    (guarded_repo / "changed.txt").write_text(synthetic + "\n", encoding="utf-8")
    baseline_path = guarded_repo / ".secrets.baseline"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if mutation == "add-suppression":
        baseline["results"]["changed.txt"] = [
            {
                "type": "Private Key",
                "filename": "changed.txt",
                "hashed_secret": "0" * 40,
                "is_verified": False,
                "line_number": 1,
            }
        ]
    elif mutation == "remove-plugin":
        baseline["plugins_used"] = [
            item for item in baseline["plugins_used"] if item["name"] != "PrivateKeyDetector"
        ]
    elif mutation == "remove-filter":
        baseline["filters_used"] = [
            item
            for item in baseline["filters_used"]
            if item["path"]
            != "detect_secrets.filters.heuristic.is_indirect_reference"
        ]
    else:
        plugin = next(
            item
            for item in baseline["plugins_used"]
            if item["name"] == "Base64HighEntropyString"
        )
        plugin["limit"] = 8.0
    baseline_path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    _git(guarded_repo, "add", "changed.txt", ".secrets.baseline")
    _git(guarded_repo, "commit", "-q", "-m", "candidate")
    result = _run_guard(guarded_repo)
    assert result.returncode != 0
    assert synthetic not in result.stdout + result.stderr
