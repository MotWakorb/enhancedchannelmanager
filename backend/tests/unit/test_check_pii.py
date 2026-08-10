"""Tests for ``scripts/check_pii.py`` (bead enhancedchannelmanager-il1xz).

The script is the ratchet guard against NEW personal identifiers in tracked
text. It is enforcement code on the CI critical path, so it ships with its
own fixtures per the "Enforcement Code Tests Itself" convention (the same
convention bead enhancedchannelmanager-yducf exists because of).

Four properties carry the guard's whole value, and each has a test class:

1. It flags the identifier classes the 2026-08-01 PII sweep actually found.
2. It does NOT flag the things that legitimately look like them: documentation
   sample addresses, placeholder home directories, and above all the committer
   identity that every `.beads` record carries.
3. It scans only lines the branch ADDS, so pre-existing debt on untouched lines
   does not red-line CI.
4. It never prints a matched value, because CI logs on this repository are
   public.

Every identifier in this file is synthetic. Nothing here is a real address,
account, or path, and the deny-list test hashes a made-up token rather than
importing a real one.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_pii.py"

# Synthetic fixtures. Not real people, accounts, hosts, or machines.
FAKE_EMAIL = "dana.reyes@fictional-isp.net"
FAKE_HOME_PATH = "/home/dreyes/ecm/wt-newui"
FAKE_PRIVATE_URL = "https://mockup-v16.dreyes-acct.chatgpt.site/?view=channels"
FAKE_DENY_TERM = "zzsyntheticacct"
FAKE_SEPARATED_TERMS = (
    "zzsynthetic-acct",
    "zzsynthetic_acct",
    "zzsynthetic.acct",
)
# Committer identity on a bead row. Deliberately shaped so that the email rule
# WOULD flag it if the identity-field skip regressed, which is what makes the
# `.beads` ratchet tests below discriminating rather than vacuous.
FAKE_IDENTITY = "ci-bot@fictional-isp.net"


def _load_script_module():
    """Load check_pii.py as an ad-hoc module (it is not a package)."""
    spec = importlib.util.spec_from_file_location("check_pii", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_pii"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _load_script_module()


def _rules(violations):
    return sorted({v.rule for v in violations})


# --- Rule: personal-email ---------------------------------------------------


class TestPersonalEmailRule:
    def test_personal_address_is_flagged(self, script):
        assert script.find_personal_emails(f"Page {FAKE_EMAIL} until on-call exists") == [
            FAKE_EMAIL
        ]

    @pytest.mark.parametrize(
        "text",
        [
            "Send alerts to alerts@example.com and oncall@example.com.",
            "Sample recipient: you@example.com",
            "Committer identity is noreply@anthropic.com on every bead row.",
            "GitHub writes 1234+user@users.noreply.github.com.",
            "Role mailbox support@fictional-isp.net is organisational.",
            "Reserved TLDs cannot resolve: someone@corp.example, a@b.invalid.",
        ],
    )
    def test_impersonal_addresses_are_not_flagged(self, script, text):
        assert script.find_personal_emails(text) == []

    def test_single_character_local_part_is_not_an_address(self, script):
        # `.beads/issues.jsonl` carries `\n@app.post` from quoted FastAPI
        # decorators, and `.post` is a real TLD. This is the guard against
        # that exact false positive.
        assert script.find_personal_emails("body\\n@app.post('/x')") == []


# --- Rule: local-user-path --------------------------------------------------


class TestLocalUserPathRule:
    @pytest.mark.parametrize(
        "text",
        [
            f"Read from source at {FAKE_HOME_PATH} (branch newui)",
            "Mac checkout at /Users/dreyes/src/ecm",
            "Windows checkout at C:\\Users\\dreyes\\ecm",
        ],
    )
    def test_real_account_home_is_flagged(self, script, text):
        assert script.find_local_user_paths(text) != []

    @pytest.mark.parametrize(
        "text",
        [
            "Clone to /home/user/ecm and run make.",
            "CI checks out under /home/runner/work/ecm.",
            "The container runs as /home/appuser.",
            "Elided in the doc as /home/... for brevity.",
            "Shell form: /home/$USER/ecm and /home/${USER}/ecm.",
            "Angle-bracket placeholder /home/<user>/ecm.",
        ],
    )
    def test_placeholder_and_role_accounts_are_not_flagged(self, script, text):
        assert script.find_local_user_paths(text) == []


# --- Rule: private-host-url -------------------------------------------------


class TestPrivateHostUrlRule:
    def test_personal_share_host_is_flagged(self, script):
        assert script.find_private_host_urls(f"Visual reference: {FAKE_PRIVATE_URL}") != []

    @pytest.mark.parametrize(
        "text",
        [
            "See https://github.com/MotWakorb/enhancedchannelmanager/issues/104",
            "Docs live at https://docs.example.com/guide.",
            "Local dev runs on http://localhost:6100/api/health/ready.",
        ],
    )
    def test_public_hosts_are_not_flagged(self, script, text):
        assert script.find_private_host_urls(text) == []


# --- Rule: known-identifier -------------------------------------------------


class TestKnownIdentifierRule:
    @pytest.fixture
    def with_synthetic_term(self, script, monkeypatch):
        monkeypatch.setitem(
            script.KNOWN_IDENTIFIER_HASHES,
            script.hash_term(FAKE_DENY_TERM),
            "synthetic fixture term",
        )
        return script

    def test_listed_term_is_flagged_case_insensitively(self, with_synthetic_term):
        script = with_synthetic_term
        assert script.find_known_identifiers(f"user {FAKE_DENY_TERM} owns it") == [
            FAKE_DENY_TERM
        ]
        assert script.find_known_identifiers(FAKE_DENY_TERM.upper()) != []

    def test_term_embedded_in_a_host_is_still_found(self, with_synthetic_term):
        script = with_synthetic_term
        url = f"https://mock.{FAKE_DENY_TERM}.chatgpt.site/x"
        assert FAKE_DENY_TERM in script.find_known_identifiers(url)

    def test_unlisted_tokens_are_not_flagged(self, with_synthetic_term):
        script = with_synthetic_term
        assert script.find_known_identifiers("ordinary prose about channels") == []

    def test_hash_term_is_stable_and_casefolded(self, script):
        assert script.hash_term("AbC") == script.hash_term(" abc ")
        assert len(script.hash_term("abc")) == 64

    @pytest.mark.parametrize("term", FAKE_SEPARATED_TERMS)
    def test_separator_bearing_account_is_one_identifier(
        self, script, monkeypatch, term
    ):
        monkeypatch.setitem(
            script.KNOWN_IDENTIFIER_HASHES,
            script.hash_term(term),
            "synthetic separated fixture",
        )
        assert script.find_known_identifiers(f"account={term}") == [term]

    def test_known_identifier_does_not_match_inside_a_larger_token(
        self, with_synthetic_term
    ):
        script = with_synthetic_term
        assert script.find_known_identifiers(f"prefix{FAKE_DENY_TERM}suffix") == []

    @pytest.mark.parametrize("separator", [".", "_", "-"])
    @pytest.mark.parametrize("side", ["leading", "trailing"])
    @pytest.mark.parametrize("run_length", [1, 2])
    def test_separator_boundary_does_not_poison_adjacent_known_identifier(
        self, with_synthetic_term, separator, side, run_length
    ):
        script = with_synthetic_term
        boundary = separator * run_length
        text = (
            boundary + FAKE_DENY_TERM
            if side == "leading"
            else FAKE_DENY_TERM + boundary
        )
        assert script.find_known_identifiers(text) == [FAKE_DENY_TERM]

    @pytest.mark.parametrize(
        "text",
        [f"prefix{FAKE_DENY_TERM}", f"{FAKE_DENY_TERM}suffix"],
    )
    def test_alphanumeric_boundary_still_prevents_substring_match(
        self, with_synthetic_term, text
    ):
        assert with_synthetic_term.find_known_identifiers(text) == []

    @pytest.mark.parametrize("term", ["two words", "leading-", ".leading", "a..b"])
    def test_hash_term_rejects_values_outside_the_scanner_grammar(self, script, term):
        with pytest.raises(ValueError, match="identifier token grammar"):
            script.hash_term(term)


# --- Beads JSONL field scoping ----------------------------------------------


def _bead_record(**overrides):
    record = {
        "_type": "issue",
        "id": "enhancedchannelmanager-fixture",
        "title": "A bead",
        "description": "Body text.",
        "status": "closed",
        "owner": FAKE_IDENTITY,
        "created_by": "Claude",
        "assignee": "Claude",
        "comments": [{"author": "Claude", "text": "A comment."}],
    }
    record.update(overrides)
    return json.dumps(record)


class TestBeadsJsonlScoping:
    def test_committer_identity_fields_are_not_scanned(self, script):
        # The single most important false-positive class: every bead row
        # carries owner/created_by/assignee/author. Scanning them would fail
        # every PR that creates a bead.
        line = _bead_record(
            owner=FAKE_EMAIL,
            created_by=FAKE_EMAIL,
            assignee=FAKE_EMAIL,
            comments=[{"author": FAKE_EMAIL, "text": "A comment."}],
        )
        assert script.scan_text(".beads/issues.jsonl", line, "beads-jsonl") == []

    def test_free_text_fields_are_scanned(self, script):
        line = _bead_record(description=f"Page {FAKE_EMAIL} until on-call exists.")
        violations = script.scan_text(".beads/issues.jsonl", line, "beads-jsonl")
        assert _rules(violations) == ["personal-email"]

    def test_nested_comment_text_is_scanned(self, script):
        line = _bead_record(
            comments=[{"author": "Claude", "text": f"Read {FAKE_HOME_PATH} for this."}]
        )
        violations = script.scan_text(".beads/issues.jsonl", line, "beads-jsonl")
        assert _rules(violations) == ["local-user-path"]

    def test_unparseable_line_fails_closed(self, script):
        # A malformed export must not become a silent hole in the guard.
        violations = script.scan_text(
            ".beads/issues.jsonl", "{not json at all " + FAKE_EMAIL, "beads-jsonl"
        )
        assert _rules(violations) == ["personal-email"]


# --- Markdown scoping -------------------------------------------------------


class TestMarkdownScoping:
    def test_code_blocks_are_not_exempt(self, script):
        # Deliberately unlike the em-dash guard: an address inside a fenced
        # block is still an address.
        text = "Example:\n\n```\nsmtp-to: " + FAKE_EMAIL + "\n```\n"
        violations = script.scan_text("docs/x.md", text, "markdown")
        assert _rules(violations) == ["personal-email"]
        assert violations[0].line == 4


# --- Masked reporting -------------------------------------------------------


class TestMasking:
    def test_redaction_is_constant_and_has_no_length_or_content_oracle(self, script):
        assert script.redact(FAKE_EMAIL) == "REDACTED"
        assert script.redact("x") == "REDACTED"


# --- Ratchet behaviour (real git repository) --------------------------------


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def fake_repo(script, tmp_path, monkeypatch):
    """A throwaway git repo whose base commit already carries one violation.

    Mirrors the real repository's shape: a doc with pre-existing PII debt, a
    clean doc, and a `.beads` export whose records carry committer identity.
    """
    repo = tmp_path
    _run_git(repo, "init", "-q", "-b", "main")
    _run_git(repo, "config", "user.email", "ci@example.com")
    _run_git(repo, "config", "user.name", "CI")

    (repo / "docs").mkdir()
    (repo / ".beads").mkdir()
    (repo / "docs" / "legacy.md").write_text(
        f"# Legacy\n\nRead from source at {FAKE_HOME_PATH} (branch newui).\n",
        encoding="utf-8",
    )
    (repo / "docs" / "clean.md").write_text(
        "# Clean\n\nNothing personal here.\n", encoding="utf-8"
    )
    (repo / ".beads" / "issues.jsonl").write_text(
        _bead_record(id="a", owner=FAKE_IDENTITY)
        + "\n"
        + _bead_record(id="b", owner=FAKE_IDENTITY)
        + "\n",
        encoding="utf-8",
    )
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "base")
    _run_git(repo, "branch", "base")

    monkeypatch.setattr(script, "REPO_ROOT", repo)
    return repo


def _ratchet(script, capsys):
    code = script.main(["--base-ref", "base"])
    return code, capsys.readouterr()


class TestRatchet:
    def test_clean_diff_passes(self, script, fake_repo, capsys):
        (fake_repo / "docs" / "clean.md").write_text(
            "# Clean\n\nStill nothing personal.\n", encoding="utf-8"
        )
        code, captured = _ratchet(script, capsys)
        assert code == 0, captured.err
        assert "PASS" in captured.out

    def test_pre_existing_violation_on_an_untouched_line_passes(
        self, script, fake_repo, capsys
    ):
        # The debt is real and the inventory reports it...
        inventory = script.scan_paths(["docs/legacy.md"])
        assert _rules(inventory) == ["local-user-path"]
        # ...but nothing changed, so the gate stays green.
        code, captured = _ratchet(script, capsys)
        assert code == 0, captured.err

    def test_added_violation_fails(self, script, fake_repo, capsys):
        (fake_repo / "docs" / "clean.md").write_text(
            f"# Clean\n\nPage {FAKE_EMAIL} until on-call exists.\n", encoding="utf-8"
        )
        code, captured = _ratchet(script, capsys)
        assert code == 1
        assert "docs/clean.md" in captured.err
        assert "personal-email" in captured.err

    def test_failure_report_never_prints_the_matched_value(
        self, script, fake_repo, capsys
    ):
        (fake_repo / "docs" / "clean.md").write_text(
            f"# Clean\n\nPage {FAKE_EMAIL} until on-call exists.\n", encoding="utf-8"
        )
        code, captured = _ratchet(script, capsys)
        assert code == 1
        combined = captured.out + captured.err
        assert "REDACTED" in combined
        assert "dana.reyes" not in combined
        assert "fictional-isp" not in combined
        assert all(FAKE_EMAIL[:length] not in combined for length in range(3, 9))
        assert all(FAKE_EMAIL[-length:] not in combined for length in range(3, 9))

    def test_brand_new_file_carrying_a_violation_fails(self, script, fake_repo, capsys):
        (fake_repo / "docs" / "added.md").write_text(
            f"# Added\n\nMockup at {FAKE_PRIVATE_URL}\n", encoding="utf-8"
        )
        code, captured = _ratchet(script, capsys)
        assert code == 1
        assert "docs/added.md" in captured.err
        assert "private-host-url" in captured.err

    def test_renamed_file_with_added_identifier_fails(
        self, script, fake_repo, capsys
    ):
        _run_git(fake_repo, "mv", "docs/clean.md", "docs/renamed.md")
        (fake_repo / "docs" / "renamed.md").write_text(
            f"# Renamed\n\nPage {FAKE_EMAIL} until on-call exists.\n",
            encoding="utf-8",
        )
        code, captured = _ratchet(script, capsys)
        assert code == 1
        assert "docs/renamed.md" in captured.err
        assert "personal-email" in captured.err

    @pytest.mark.parametrize(
        ("name", "content"),
        [
            ("invalid-utf8.md", b"# Synthetic\n\xff\xfe\n"),
            ("nul.md", b"# Synthetic\ncontains\x00nul\n"),
        ],
    )
    def test_added_unscannable_file_fails_closed(
        self, script, fake_repo, capsys, name, content
    ):
        path = fake_repo / "docs" / name
        path.write_bytes(content)
        code, captured = _ratchet(script, capsys)
        assert code == 1
        assert f"docs/{name}" in captured.err
        assert "unscannable" in captured.err
        assert "contains" not in captured.err

    @pytest.mark.parametrize("content", [b"\xff\xfe", b"text\x00binary"])
    def test_modified_unscannable_file_fails_closed(
        self, script, fake_repo, capsys, content
    ):
        path = fake_repo / "docs" / "clean.md"
        path.write_bytes(content)
        code, captured = _ratchet(script, capsys)
        assert code == 1
        assert "docs/clean.md" in captured.err
        assert "unscannable" in captured.err

    def test_paths_mode_fails_closed_on_unscannable_file(
        self, script, fake_repo, capsys
    ):
        (fake_repo / "docs" / "clean.md").write_bytes(b"\xff\xfe")
        code = script.main(["--paths", "docs/clean.md"])
        captured = capsys.readouterr()
        assert code == 1
        assert "docs/clean.md" in captured.err
        assert "unscannable" in captured.err

    def test_inventory_mode_fails_closed_on_unscannable_file(
        self, script, fake_repo, capsys
    ):
        (fake_repo / "docs" / "clean.md").write_bytes(b"text\x00binary")
        code = script.main(["--all"])
        captured = capsys.readouterr()
        assert code == 1
        assert "docs/clean.md" in captured.err
        assert "unscannable" in captured.err

    def test_second_copy_of_a_pre_existing_violation_fails(
        self, script, fake_repo, capsys
    ):
        path = fake_repo / "docs" / "legacy.md"
        path.write_text(path.read_text(encoding="utf-8") * 2, encoding="utf-8")
        code, captured = _ratchet(script, capsys)
        assert code == 1
        assert "local-user-path" in captured.err

    def test_committer_identity_in_beads_passes(self, script, fake_repo, capsys):
        # A bead-creating PR appends a row whose owner/created_by/assignee are
        # all identities. That must not be a violation.
        path = fake_repo / ".beads" / "issues.jsonl"
        path.write_text(
            path.read_text(encoding="utf-8")
            + _bead_record(id="c", owner=FAKE_IDENTITY)
            + "\n",
            encoding="utf-8",
        )
        code, captured = _ratchet(script, capsys)
        assert code == 0, captured.err

    def test_bulk_rewrite_of_the_beads_export_passes(self, script, fake_repo, capsys):
        # `bd` rewrites the whole export (commit 845ead93: 2984 insertions,
        # 2509 deletions). Under a line-scoped ratchet every pre-existing
        # identifier would read as newly added. Reverse the record order,
        # changing no content, and the gate must stay green.
        path = fake_repo / ".beads" / "issues.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")
        code, captured = _ratchet(script, capsys)
        assert code == 0, captured.err

    def test_reflowing_debt_onto_an_added_line_is_checked_again(
        self, script, fake_repo, capsys
    ):
        path = fake_repo / "docs" / "legacy.md"
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("(branch newui).", "(branch rewritten)."), encoding="utf-8")
        code, captured = _ratchet(script, capsys)
        assert code == 1
        assert "local-user-path" in captured.err

    def test_paths_mode_reports_every_occurrence(self, script, fake_repo, capsys):
        code = script.main(["--paths", "docs/legacy.md"])
        captured = capsys.readouterr()
        assert code == 1
        assert "local-user-path" in captured.err

    def test_all_mode_never_fails_the_build(self, script, fake_repo, capsys):
        code = script.main(["--all"])
        captured = capsys.readouterr()
        assert code == 0
        assert "docs/legacy.md" in captured.out


# --- Real repository regression ---------------------------------------------


class TestRealRepository:
    def test_guard_passes_on_the_checked_out_tree(self, script):
        # Dogfooding: the guard must not red-flag the repository it ships in.
        # `--paths` on a doc that has never carried PII proves the scanner
        # runs clean against real content rather than a hand-built fixture.
        assert script.scan_paths(["docs/style_guide.md"]) == []
