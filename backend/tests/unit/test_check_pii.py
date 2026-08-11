"""Tests for ``scripts/check_pii.py`` (beads enhancedchannelmanager-il1xz,
enhancedchannelmanager-h9av3).

The script is the ratchet guard against NEW personal identifiers (arm 1,
il1xz) and NEW application credentials (arm 2, h9av3) in tracked text. It is
enforcement code on the CI critical path, so it ships with its own fixtures
per the "Enforcement Code Tests Itself" convention (the same convention bead
enhancedchannelmanager-yducf exists because of).

Five properties carry the guard's whole value, and each has a test class:

1. It flags the identifier classes the 2026-08-01 PII sweep actually found.
2. It flags this application's own credential shapes at real value length --
   the ones GitHub secret scanning cannot know about.
3. It does NOT flag the things that legitimately look like them: documentation
   sample addresses, placeholder home directories, the committer identity that
   every `.beads` record carries, and above all the credential FIELD NAMES,
   which appear in `backend/config.py`, in `docs/`, and in bead descriptions
   throughout this repository.
4. It scans only lines the branch ADDS, so pre-existing debt on untouched lines
   does not red-line CI and rewritten lines are checked again.
5. It never prints a matched value, because CI logs on this repository are
   public.

Property 3 is the one that decides whether the credential arm survives
contact with the repository, so it gets the most cases: a rule that fired on
the string `mcp_api_key` would be switched off within a day.

Every identifier and credential in this file is synthetic. Nothing here is a
real address, account, path, key, or token. The credential fixtures are
assembled from repeated or obviously patterned characters rather than written
as literals, so that they carry the right SHAPE for these rules without ever
resembling a real secret to a human reader or to a scanner.
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

# --- Synthetic credential fixtures (h9av3) ----------------------------------
#
# Shaped like the real formats, valued like nothing at all. Each carries the
# properties `_looks_secret` tests for -- length, character variety, a digit,
# a letter, no repeating unit, no word structure -- without being drawn from
# any real generator.
FAKE_VALUE_43 = "7Kq3Zm9X" + "b2Tf5Rw8" + "Nc4Yh6Jd" + "1Ls0Pv7G" + "u2Ei5Ao9Bt"
FAKE_HEX_VALUE_32 = "3f7a1c9e" + "5b2d8046" + "af13ce72" + "b95d604e"
FAKE_VALUE_20 = "6Hx2Vn8Qr4" + "Wz1Kt5Mb3P"
# Telegram issues `<bot id>:<exactly 35 characters>`.
FAKE_TELEGRAM_VALUE = "8123456789:" + "Ab3Cd6Ef9Gh2Jk5Lm" + "8Np1Qr4St7Uv0Wx3Yz"
# Discord issues `<17-20 digit id>/<68 character token>`. Assembled from parts
# so no full webhook-shaped literal exists anywhere in this file.
FAKE_DISCORD_WEBHOOK = (
    "https://discord.com/api/webhooks/" + "9" * 19 + "/" + "Zq7" * 22 + "Xw"
)


def _load_script_module():
    """Load check_pii.py as an ad-hoc module (it is not a package)."""
    spec = importlib.util.spec_from_file_location("check_pii", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_pii"] = module
    spec.loader.exec_module(module)
    return module


def script_placeholder_markers():
    """PLACEHOLDER_VALUE_MARKERS, read at collection time.

    Parametrising from the module's own list rather than a copy means a
    marker added later automatically gets a case, instead of silently
    joining the list untested.
    """
    return _load_script_module().PLACEHOLDER_VALUE_MARKERS


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


# --- Rule: credential-assignment (h9av3) ------------------------------------


class TestCredentialAssignmentRule:
    """The rule the credential arm lives or dies on.

    Its whole design claim is that it matches VALUES and not NAMES, so the
    negative cases below carry as much weight as the positive ones.
    """

    @pytest.mark.parametrize(
        ("label", "text"),
        [
            ("mcp key", f'"mcp_api_key": "{FAKE_VALUE_43}"'),
            ("mcp key in env form", f"ECM_MCP_API_KEY={FAKE_VALUE_43}"),
            ("mcp key via attribute", f"settings.mcp_api_key = '{FAKE_VALUE_43}'"),
            ("dispatcharr api key", f"dispatcharr_api_key: {FAKE_HEX_VALUE_32}"),
            ("dispatcharr password", f'dispatcharr_password="{FAKE_VALUE_20}"'),
            ("smtp password", f"smtp_password = {FAKE_VALUE_43}"),
            ("emby key", f'emby_api_key: "{FAKE_HEX_VALUE_32}"'),
            ("jellyfin key", f"jellyfin_api_key={FAKE_HEX_VALUE_32}"),
            ("plex token header", f"X-Plex-Token: {FAKE_VALUE_20}"),
            ("telegram field", f'telegram_bot_token: "{FAKE_VALUE_43}"'),
            ("signing key", f"secret_key = {FAKE_VALUE_43}"),
            ("session secret", f'"session_secret": "{FAKE_VALUE_43}"'),
        ],
    )
    def test_named_ecm_field_with_a_real_shaped_value_is_flagged(
        self, script, label, text
    ):
        assert script.find_credential_assignments(text) != [], label

    def test_mcp_key_in_a_url_query_parameter_is_flagged(self, script):
        # The h9av3 sweep's conclusion: anchor the MCP key to `api_key=` in a
        # URL rather than to the bare 43-character shape, which is
        # indistinguishable from an npm lockfile integrity hash.
        url = f"curl https://ecm.example.net/mcp/sse?api_key={FAKE_VALUE_43}"
        assert script.find_credential_assignments(url) != []
        assert script.find_credential_assignments(
            f"https://ecm.example.net/mcp/sse?v=1&api_key={FAKE_VALUE_43}"
        ) != []

    @pytest.mark.parametrize(
        ("label", "text"),
        [
            # The three contexts the field names genuinely appear in today.
            ("name in prose", "The mcp_api_key field is documented in docs/api.md."),
            ("config declaration", '    mcp_api_key: str = ""'),
            (
                "bead description",
                "Operators rotating the MCP key were copying it into api_key "
                "instead of mcp_api_key (bd-jmi1c).",
            ),
            ("code quoted in a bead", "settings.mcp_api_key = secrets.token_urlsafe(32)"),
            # Placeholder values the documentation actually uses.
            ("angle placeholder", "Set mcp_api_key to <your-generated-key> in the UI."),
            ("worded placeholder", "mcp_api_key: your-generated-key-here-please"),
            ("redaction sentinel", '"mcp_api_key": "***REDACTED***"'),
            (
                "throwaway doc key",
                "-e SECRET_KEY=baseline-test-key-not-for-prod-32chars",
            ),
            ("short value", "auth_method: password"),
            ("integer value", "refresh_token_expire_days: 7"),
            # Fabricated hex: 16 distinct characters, digits, letters, and
            # maximal Shannon entropy. Only the repetition test rejects it.
            ("fabricated hex", "emby_api_key = abcdef0123456789abcdef0123456789"),
        ],
    )
    def test_names_declarations_and_placeholders_are_not_flagged(
        self, script, label, text
    ):
        assert script.find_credential_assignments(text) == [], label

    @pytest.mark.parametrize("marker", script_placeholder_markers())
    def test_every_placeholder_marker_is_load_bearing(self, script, marker):
        # Each entry in PLACEHOLDER_VALUE_MARKERS is reached only by a value
        # that survives length, variety, digit, alphabet, repetition, and
        # wordiness. Without a case per marker the list looks tested but is
        # not: the first draft of this file exercised none of it, and the
        # sabotage run is what surfaced that. Building the fixture FROM the
        # module's own list means a marker added later cannot go untested.
        value = marker + "1234567890abcdefghij"
        text = f"mcp_api_key = {value}"
        assert script.find_credential_assignments(text) == [], marker
        # Discriminating half: identical value, marker list emptied, and the
        # rule now fires. Proves the marker is what suppressed it and not
        # some other test in _looks_secret.
        saved = script.PLACEHOLDER_VALUE_MARKERS
        try:
            script.PLACEHOLDER_VALUE_MARKERS = ()
            assert script.find_credential_assignments(text) != [], marker
        finally:
            script.PLACEHOLDER_VALUE_MARKERS = saved

    @pytest.mark.parametrize(
        ("label", "text"),
        [
            # These are detect-secrets' job. If this arm started matching
            # them it would be re-creating the generic rule the h9av3 sweep
            # measured at 274 hits for 1 true positive.
            ("bare api_key word", f"api_key = {FAKE_VALUE_43}"),
            ("generic password", f"password: {FAKE_VALUE_43}"),
            ("generic token", f"auth_token: {FAKE_VALUE_43}"),
            ("generic secret", f"client_secret={FAKE_VALUE_43}"),
            ("npm integrity hash", f'"integrity": "sha512-{FAKE_VALUE_43}"'),
        ],
    )
    def test_generic_credential_names_are_left_to_detect_secrets(
        self, script, label, text
    ):
        assert script.find_credential_assignments(text) == [], label

    @pytest.mark.parametrize(
        ("label", "text"),
        [
            ("sort key", 'sort_key = "channel_number_ascending_with_group"'),
            ("cache key", "cache_key: dispatcharr_channels_by_group_1234567890"),
            ("commit sha", "commit hash: 845ead93f21c4a8b9e0d3c7f5a1b2e6d8c4f0a91"),
            # These two carry HASH-SHAPED values, which is how cache keys are
            # normally built. They are the discriminating cases: the three
            # above are also rejected by `_looks_secret`, so on their own
            # they would stay green even if `key` were added to the field
            # list. The sabotage run is what exposed that.
            # Assemble these synthetic hash-shaped values from short pieces
            # so this test cannot grant itself an inline scanner exemption.
            ("hash-shaped cache key", 'cache_key = "' + "a7Kq3Zm9Xb2Tf5Rw" + "8Nc4Yh6Jd1Ls0Pv" + '"'),
            ("hash-shaped sort key", 'sort_key = "' + "b2Tf5Rw8Nc4Yh6Jd1" + "Ls0Pv7Gu2Ei5Ao9" + '"'),
        ],
    )
    def test_non_credential_key_names_are_not_flagged(self, script, label, text):
        # `key` is deliberately absent from ECM_CREDENTIAL_FIELDS. These are
        # the identifiers that would break if it were ever added.
        assert script.find_credential_assignments(text) == [], label


# --- Rule: discord-webhook-url (h9av3) --------------------------------------


class TestDiscordWebhookRule:
    @pytest.mark.parametrize(
        "host",
        [
            "discord.com",
            "discordapp.com",
            "canary.discord.com",
            "ptb.discord.com",
        ],
    )
    def test_every_backend_accepted_host_is_flagged(self, script, host):
        webhook = FAKE_DISCORD_WEBHOOK.replace("discord.com", host)
        assert script.find_discord_webhooks(
            f"discord_webhook_url = {webhook}"
        ) != []

    @pytest.mark.parametrize(
        "url",
        [
            FAKE_DISCORD_WEBHOOK.replace("https://", "http://"),
            FAKE_DISCORD_WEBHOOK.replace("discord.com", "discord.com.evil.example"),
            FAKE_DISCORD_WEBHOOK.replace("discord.com", "evildiscord.com"),
            "prefix" + FAKE_DISCORD_WEBHOOK,
        ],
    )
    def test_scheme_and_host_boundaries_reject_lookalikes(self, script, url):
        assert script.find_discord_webhooks(url) == []

    @pytest.mark.parametrize(
        "text",
        [
            # Both of these are live lines in the user guide and docs/api.md.
            "The URL looks like `https://discord.com/api/webhooks/<id>/<token>`.",
            '`{ "webhook_url": "https://discord.com/api/webhooks/..." }`',
            "https://discord.com/api/webhooks/123456/abcdefg",
        ],
    )
    def test_documentation_placeholders_are_not_flagged(self, script, text):
        assert script.find_discord_webhooks(text) == []


# --- Rule: telegram-bot-token (h9av3) ---------------------------------------


class TestTelegramBotTokenRule:
    def test_real_shaped_token_is_flagged(self, script):
        assert script.find_telegram_bot_tokens(
            f"telegram_bot_token = {FAKE_TELEGRAM_VALUE}"
        ) == [FAKE_TELEGRAM_VALUE]

    @pytest.mark.parametrize("delta", [-1, 1])
    def test_token_length_is_exact_not_a_floor(self, script, delta):
        # The lookarounds exist so a 34- or 36-character neighbour is
        # rejected outright rather than matching as a prefix of some longer
        # base64 blob. That exactness is what keeps this rule off
        # `.beads/issues.jsonl`.
        bot_id, token = FAKE_TELEGRAM_VALUE.split(":")
        token = token[:-1] if delta < 0 else token + "Q"
        assert script.find_telegram_bot_tokens(f"{bot_id}:{token}") == []

    @pytest.mark.parametrize(
        "text",
        [
            "telegram_chat_id: -1001234567890",
            "Set telegram_bot_token and telegram_chat_id in Settings.",
            "elapsed 12:34:56 over 8123456789 records",
        ],
    )
    def test_chat_ids_and_prose_are_not_flagged(self, script, text):
        # A chat id is a bare integer and is knowingly uncovered; see the
        # module docstring.
        assert script.find_telegram_bot_tokens(text) == []


# --- Rules delegated to detect-secrets (h9av3) ------------------------------


class TestDelegatedToGenericLayer:
    """Classes this arm deliberately does NOT implement.

    Both are covered precisely by `detect-secrets` 1.5.0 (JwtTokenDetector
    and PrivateKeyDetector, both verified to fire). Implementing them here
    too would produce two findings per value for no added coverage. These
    tests pin the decision so a future change has to be deliberate.
    """

    def test_no_rule_matches_a_jwt(self, script):
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
            ".eyJzdWIiOiJmaXh0dXJlIiwiaWF0IjoxfQ." + "Qs4Tv7" * 7
        )
        assert script.scan_text("docs/x.md", jwt, "markdown") == []

    def test_no_rule_matches_a_pem_header(self, script):
        # The armour header only, with no key material after it. Flagged by
        # detect-secrets' PrivateKeyDetector, which is the whole point of
        # this test: that detector is why this arm does not carry a PEM rule.
        header = "-----BEGIN " + "RSA PRIVATE KEY-----"
        assert script.scan_text("docs/x.md", header, "markdown") == []

    def test_rule_set_is_the_three_ecm_specific_rules(self, script):
        credential_rules = [
            name for name, _ in script.RULES if name in script.CREDENTIAL_RULE_NAMES
        ]
        assert credential_rules == [
            "credential-assignment",
            "discord-webhook-url",
            "telegram-bot-token",
        ]


# --- Credential-file surface (h9av3) ----------------------------------------


class TestCredentialFileSurface:
    @pytest.mark.parametrize(
        "rel_path",
        [
            ".env",
            ".env.local",
            "config/auth_settings.json",
            "auth_settings.json",
            ".claude/settings.json",
        ],
    )
    def test_credential_shaped_files_are_scanned_as_plaintext(self, script, rel_path):
        assert script._kind_for(rel_path) == "plaintext"

    @pytest.mark.parametrize(
        "rel_path",
        [
            ".env.example",
            ".env.sample",
            "tests/dbas-test-env/.env.template",
            "backend/config.py",
            "frontend/package-lock.json",
        ],
    )
    def test_samples_and_source_files_are_not_scanned(self, script, rel_path):
        # `.env.example` is tracked in this repository today and is meant to
        # be. Source trees stay out of scope; a hardcoded secret in
        # `backend/` is detect-secrets' job, not this guard's.
        assert script._kind_for(rel_path) is None


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


class TestCredentialRatchet:
    """The credential arm end to end, through the real ratchet and reporting.

    `fake_repo`'s base commit carries a pre-existing credential in
    `docs/legacy.md`, so these cover the same four ratchet properties the
    identifier arm does: added fails, pre-existing passes, clean passes, and
    the report never prints the value.
    """

    def test_added_credential_fails(self, script, fake_repo, capsys):
        (fake_repo / "docs" / "clean.md").write_text(
            f'# Clean\n\n    "mcp_api_key": "{FAKE_VALUE_43}"\n', encoding="utf-8"
        )
        code, captured = _ratchet(script, capsys)
        assert code == 1
        assert "docs/clean.md" in captured.err
        assert "credential-assignment" in captured.err

    def test_added_credential_report_never_prints_the_value(
        self, script, fake_repo, capsys
    ):
        (fake_repo / "docs" / "clean.md").write_text(
            f'# Clean\n\n    "mcp_api_key": "{FAKE_VALUE_43}"\n', encoding="utf-8"
        )
        code, captured = _ratchet(script, capsys)
        assert code == 1
        combined = captured.out + captured.err
        assert FAKE_VALUE_43 not in combined
        # Not even a substantial prefix of it.
        assert FAKE_VALUE_43[:12] not in combined

    def test_credential_failure_tells_the_operator_to_rotate(
        self, script, fake_repo, capsys
    ):
        # A credential that reached a branch is published. Redacting the text
        # does not un-publish it, so the report must say ROTATE, not just
        # redact.
        (fake_repo / "docs" / "clean.md").write_text(
            f"# Clean\n\nwebhook: {FAKE_DISCORD_WEBHOOK}\n", encoding="utf-8"
        )
        code, captured = _ratchet(script, capsys)
        assert code == 1
        assert "ROTATE" in captured.err

    def test_identifier_only_failure_does_not_mention_rotation(
        self, script, fake_repo, capsys
    ):
        # Discriminating counterpart to the test above: the rotate wording is
        # conditional on a credential rule, not printed unconditionally.
        (fake_repo / "docs" / "clean.md").write_text(
            f"# Clean\n\nPage {FAKE_EMAIL} until on-call exists.\n", encoding="utf-8"
        )
        code, captured = _ratchet(script, capsys)
        assert code == 1
        assert "ROTATE" not in captured.err

    def test_pre_existing_credential_on_an_untouched_line_passes(
        self, script, fake_repo, capsys
    ):
        path = fake_repo / "docs" / "legacy.md"
        path.write_text(
            path.read_text(encoding="utf-8")
            + f'\nsmtp_password = "{FAKE_VALUE_43}"\n',
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "-C", str(fake_repo), "add", "-A"], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(fake_repo), "commit", "-q", "-m", "debt"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(fake_repo), "branch", "-f", "base", "HEAD"],
            check=True,
            capture_output=True,
        )
        # The debt is real and the inventory reports it...
        assert _rules(script.scan_paths(["docs/legacy.md"])) == [
            "credential-assignment",
            "local-user-path",
        ]
        # ...but nothing changed since the base, so the gate stays green.
        code, captured = _ratchet(script, capsys)
        assert code == 0, captured.err

    def test_clean_diff_still_passes_with_the_credential_arm_active(
        self, script, fake_repo, capsys
    ):
        (fake_repo / "docs" / "clean.md").write_text(
            "# Clean\n\nDiscussion of mcp_api_key, smtp_password, and "
            "telegram_bot_token in prose.\n",
            encoding="utf-8",
        )
        code, captured = _ratchet(script, capsys)
        assert code == 0, captured.err

    def test_committed_env_file_is_scanned(self, script, fake_repo, capsys):
        # The credential-file surface. `.gitignore` excludes `.env` at every
        # depth, so this needs `git add -f` to happen at all -- but the cost
        # of the miss is a live credential in a public repository.
        (fake_repo / ".env").write_text(
            f"ECM_MCP_API_KEY={FAKE_VALUE_43}\n", encoding="utf-8"
        )
        code, captured = _ratchet(script, capsys)
        assert code == 1
        assert ".env" in captured.err
        assert FAKE_VALUE_43 not in captured.err

    def test_committed_env_example_file_is_not_scanned(self, script, fake_repo, capsys):
        # The value here is deliberately secret-SHAPED. An earlier version of
        # this test used a worded placeholder, which `_looks_secret` rejects
        # on its own -- so the test stayed green even with the `.env.example`
        # exemption deleted. The sabotage run caught that. With a value the
        # rule would otherwise flag, the exemption is the only thing keeping
        # this green, which is what the test is for.
        (fake_repo / ".env.example").write_text(
            f"ECM_MCP_API_KEY={FAKE_VALUE_43}\n", encoding="utf-8"
        )
        code, captured = _ratchet(script, capsys)
        assert code == 0, captured.err

    def test_unscannable_credential_file_fails_closed(
        self, script, fake_repo, capsys
    ):
        (fake_repo / ".env").write_bytes(b"synthetic\x00binary")
        code, captured = _ratchet(script, capsys)
        assert code == 1
        assert ".env" in captured.err
        assert "unscannable" in captured.err
        assert "synthetic" not in captured.err


# --- Real repository regression ---------------------------------------------


class TestRealRepository:
    def test_guard_passes_on_the_checked_out_tree(self, script):
        # Dogfooding: the guard must not red-flag the repository it ships in.
        # `--paths` on a doc that has never carried PII proves the scanner
        # runs clean against real content rather than a hand-built fixture.
        assert script.scan_paths(["docs/style_guide.md"]) == []
