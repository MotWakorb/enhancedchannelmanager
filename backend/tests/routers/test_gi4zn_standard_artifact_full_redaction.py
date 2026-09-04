"""The STANDARD DBAS artifact carries no identity or credential of any kind.

Bead ``enhancedchannelmanager-gi4zn``. PO decision 2026-08-05: the standard
(non-encrypted, default) backup is FULLY redacted.

THE PROPERTY THESE TESTS PIN
----------------------------

    A standard DBAS artifact contains no value that identifies or authenticates
    against a THIRD-PARTY service, AND no credential material of the operator's
    OWN — hashes included.

    Redaction FAILS CLOSED: if any part of the scrub cannot run or cannot parse
    its input, the affected data does not ship.

Not "the username field on an M3U account is redacted" — that is one example of
the property. The drill (``~/ecm/backup-restore-runs/2026-08-05-run3``, finding
F4) found the XC username in clear beside a correctly-redacted password, and the
project's recurring failure mode is fixing the demonstrated case and leaving the
class open (see ``CLAUDE.md`` -> "State review acceptance criteria as invariants,
not reproductions"). So the assertions below are written against the whole
artifact: every seeded identity/credential sentinel is scanned for across EVERY
decompressed member, including ``journal.db``, rather than against the two YAML
keys the drill happened to read.

The second clause was added on 2026-08-17 after an external security review
built a standard artifact and read the operator's own bcrypt admin hash, their
session and reset-token hashes, their administering IP and user agent, and their
OIDC subject and identifier straight out of the archived ``journal.db``. The
first clause alone did not cover the admin hash — it is the operator's own
credential, not a third party's — and the PO ruled it in scope anyway, because
the entire purpose of a redacted artifact is being safe to share. The third
clause was added at the same time: the scrubber's three error paths each shipped
the RAW database or the RAW row.

AVAILABILITY IS PART OF THE PROPERTY
------------------------------------

A restored instance nobody can log into is a worse outcome than the leak. See
``routers.backup._AUTH_IDENTITY_TABLES`` for why the account rows are DELETED
rather than masked, and the RESTORE BEHAVIOUR section at the foot of this file
for the two destination states and what the operator re-establishes in each.

WHY A USERNAME IS A CREDENTIAL HERE
-----------------------------------

For an Xtream Codes provider the username is half the credential pair and the
half that identifies the SUBSCRIPTION — ECM renders XC stream URLs containing
it. A standard artifact is the default artifact, it is what an operator attaches
to a support ticket, and the user guide describes it simply as "redacted".

WHAT MUST STILL SURVIVE, AND WHY EACH EXCEPTION IS NARROW
---------------------------------------------------------

* ``dispatcharr_users[].username`` — the operator's OWN Dispatcharr instance,
  not a third party, and the natural key
  ``dbas.importers.users`` creates and collision-checks on. Redacting it would
  delete a shipped restore path and protect nothing that leaves the operator's
  trust boundary. The exemption is one named category (see
  ``routers.backup._IDENTITY_EXEMPT_CATEGORIES``), and
  :func:`test_dispatcharr_users_username_survives` is what stops it widening.
* A URL with no embedded credential — the restore needs it to recreate the
  account/source at all.
* The ENCRYPTED (``include_credentials``) artifact — its whole value is that it
  carries secrets safely, and the migration card depends on it.
"""
import asyncio
import hashlib
import json
import sqlite3
import sys
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from dbas import artifact_crypto
from routers import backup as backup_mod
from routers.backup import build_backup_artifact

GOOD_PASS = "a-strong-migration-passphrase"  # >= MIN_PASSPHRASE_LENGTH

# --- Seeded third-party IDENTITY / CREDENTIAL sentinels --------------------
#
# Every one of these is a value that identifies or authenticates against a
# service outside the operator's own trust boundary. If ANY of them survives
# into a standard artifact, the property is broken.
#
# Shapes follow docs/pytest_conventions.md -> "Credential Fixtures in Security
# Tests": angle-bracket placeholders (never a scan candidate, because the
# ``SECRET`` regex's ``(?=\w+)`` lookahead rejects a leading ``<``) wherever the
# exact shape is not load-bearing, and split literals where it is (the URLs).
XC_USERNAME = "<xc-subscription-identity-QQQ111>"
EPG_USERNAME = "<epg-provider-identity-QQQ222>"
SMTP_USERNAME = "<smtp-relay-identity-QQQ333>"
CONNECTION_USERNAME = "<dispatcharr-connection-identity-QQQ444>"
PLEX_TOKEN = "<plex-account-bearer-QQQ555>"
EMBY_API_KEY = "<emby-admin-key-QQQ666>"
JELLYFIN_API_KEY = "<jellyfin-admin-key-QQQ777>"
TELEGRAM_CHAT_ID = "<telegram-chat-capability-QQQ888>"
XC_PASSWORD = "<xc-subscription-secret-QQQ999>"

# Credentials embedded in URL VALUES rather than in a credential-named key.
# The two halves are assembled from pieces and named for their POSITION rather
# than their role, so no line in this file pairs a denylisted keyword with a
# quoted value (``KeywordDetector`` matches per-line, and the inline pragma is
# deliberately disabled by the former ``scripts/check_secrets.py``).
_URL_LEFT_HALF = "urlident" + "QQQAAA"
_URL_RIGHT_HALF = "urlopaque" + "QQQBBB"
M3U_URL_WITH_QUERY_CREDS = (
    "http://provider.example.test/get.php?username="
    + _URL_LEFT_HALF
    + "&"
    + "pass"
    + "word="
    + _URL_RIGHT_HALF
)
EPG_URL_WITH_USERINFO = (
    "http://" + _URL_LEFT_HALF + ":" + _URL_RIGHT_HALF + "@epg.example.test/guide.xml"
)

# --- The operator's OWN credential material, and their third-party identity ---
#
# Findings A-1 / A-2 of the external security review, reproduced by building a
# standard artifact and reading these straight out of the archived ``journal.db``
# with sqlite3. A-1 is the operator's own credential rather than a third party's,
# and the PO ruled it in scope on 2026-08-17 for the reason the whole bead
# exists: the point of a redacted artifact is that it is safe to SHARE, and an
# offline-crackable bcrypt hash of the admin password is the single worst thing
# in the archive to hand to a support ticket.
#
# The hash keeps a real bcrypt PREFIX because the shape is load-bearing here —
# ``$2b$12$`` is what makes the value recognisable as crackable material to any
# scanner or reader — while the body is an obvious marker, never a real digest.
ADMIN_USERNAME = "local-admin"
ADMIN_EMAIL = "local-admin@operator.example"
ADMIN_PASSWORD_HASH = "$2b$12$" + "OFFLINECRACKMARKERQQQ901"
REFRESH_TOKEN_HASH = "<session-refresh-material-QQQ903>"
PRIOR_REFRESH_TOKEN_HASH = "<session-prior-refresh-material-QQQ904>"
RESET_TOKEN_HASH = "<account-recovery-material-QQQ906>"
SESSION_IP_ADDRESS = "203.0.113.77"  # TEST-NET-3, RFC 5737
SESSION_USER_AGENT = "SupportBrowser/1"
# A-2: the ECM admin correlated to an identity at a third-party IdP.
OIDC_EXTERNAL_ID = "<oidc-subject-QQQ902>"
OIDC_IDENTIFIER = "operator@thirdparty.example"

ALL_OPERATOR_ACCOUNT_VALUES = (
    ADMIN_USERNAME,
    ADMIN_EMAIL,
    ADMIN_PASSWORD_HASH,
    REFRESH_TOKEN_HASH,
    PRIOR_REFRESH_TOKEN_HASH,
    RESET_TOKEN_HASH,
    SESSION_IP_ADDRESS,
    SESSION_USER_AGENT,
    OIDC_EXTERNAL_ID,
    OIDC_IDENTIFIER,
)

# --- Round 3: what the THIRD review round found still leaking ---------------
#
# Every one of these lived in a table the enumerated purge list passed straight
# through. They are seeded here so the whole-archive scan covers them, but note
# that the fix is NOT "add four more tables to a list" — see
# :func:`test_the_shipped_journal_db_contains_only_permitted_tables`. These
# values are an EXAMPLE of the property; the allowlist is the specification.
TELEMETRY_EMBY_USER_NAME = "<emby-account-name-QQQ910>"
TELEMETRY_PLEX_USER_NAME = "<plex-account-name-QQQ911>"
TELEMETRY_JELLYFIN_USER_NAME = "<jellyfin-account-name-QQQ912>"
TELEMETRY_DISPATCHARR_USERNAME = "<dispatcharr-viewer-QQQ913>"
CLIENT_CONNECTION_IP = "203.0.113.144"  # TEST-NET-3, RFC 5737
CLIENT_CONNECTION_USERNAME = "<viewer-identity-QQQ914>"
DIGEST_EMAIL_RECIPIENT = "digest-recipient-QQQ915@operator.example"
# And three the round did NOT name, which the allowlist removes anyway — the
# point of inverting the direction is that they never had to be discovered.
JOURNAL_ENTRY_AFTER_VALUE = "<journal-after-value-QQQ916>"
STREAM_PROBE_ERROR = "<stream-probe-error-QQQ917>"
# A table NO current model declares. The pre-v0.13 health-monitor subsystem was
# removed but its tables persist in long-running installs, so no denylist
# maintained by reading models.py could ever have covered them.
VESTIGIAL_SERVICE_ENDPOINT = "<orphan-table-operator-endpoint-QQQ918>"

ALL_ROUND_THREE_VALUES = (
    TELEMETRY_EMBY_USER_NAME,
    TELEMETRY_PLEX_USER_NAME,
    TELEMETRY_JELLYFIN_USER_NAME,
    TELEMETRY_DISPATCHARR_USERNAME,
    CLIENT_CONNECTION_IP,
    CLIENT_CONNECTION_USERNAME,
    DIGEST_EMAIL_RECIPIENT,
    JOURNAL_ENTRY_AFTER_VALUE,
    STREAM_PROBE_ERROR,
    VESTIGIAL_SERVICE_ENDPOINT,
)

# --- Finding A-3: the redactor used to fail OPEN --------------------------
#
# A row whose ``config`` cannot be parsed, and one that parses to a JSON scalar
# rather than an object. Neither can be shown to be credential-free, and both
# used to ship byte-for-byte while VALID rows in the same database were correctly
# redacted.
#
# Angle-bracket placeholders per docs/pytest_conventions.md -> "Credential
# Fixtures in Security Tests": nothing about these values' shape is
# load-bearing (the test only needs a marker it can scan the archive for), and
# `KeywordDetector` has no word boundary, so BOTH the `SECRET` in these names
# and the `word` half of the split `pass`/`word` below make a bare literal a
# scan candidate. A value starting with `<` never becomes one.
_TRUNCATED_SECRET = "<smtp-relay-secret-QQQ905>"
TRUNCATED_ALERT_CONFIG = '{"pass' + 'word":"' + _TRUNCATED_SECRET + '"'  # no closing brace
NON_OBJECT_ALERT_SECRET = "<bare-scalar-marker-QQQ907>"

# The alert-method credential the DESTINATION instance already holds, which a
# fail-closed whole-blob sentinel must not destroy on restore. Same placeholder
# convention: `"password": "<...>"` is not a scan candidate, a bare word is.
DESTINATION_SMTP_SECRET = "<destination-smtp-relay-secret-QQQ909>"
NTFY_TOPIC = "private-ntfy-topic-QQQ920"
NTFY_TOKEN = "<destination-ntfy-token-QQQ921>"

# Everything a STANDARD artifact must not carry, in one tuple so a newly seeded
# value cannot be silently omitted from the whole-archive scan.
ALL_THIRD_PARTY_VALUES = (
    XC_USERNAME,
    EPG_USERNAME,
    SMTP_USERNAME,
    CONNECTION_USERNAME,
    PLEX_TOKEN,
    EMBY_API_KEY,
    JELLYFIN_API_KEY,
    TELEGRAM_CHAT_ID,
    XC_PASSWORD,
    _URL_LEFT_HALF,
    _URL_RIGHT_HALF,
    NTFY_TOPIC,
    NTFY_TOKEN,
)

# The settings fields this bead newly redacts, in one place so the producer
# scan and the restore-behaviour test cannot disagree about the list.
_NEWLY_REDACTED_SETTINGS = (
    "username",
    "smtp_user",
    "plex_token",
    "emby_api_key",
    "jellyfin_api_key",
)

# --- Values that MUST survive ---------------------------------------------
DISPATCHARR_OPERATOR_USERNAME = "dispatcharr-operator"
CLEAN_EPG_URL = "https://cdn.epg.example.test/us.xml.gz"
CLEAN_M3U_SERVER_URL = "https://provider.example.test"


def _mock_settings():
    """Settings whose dump carries a third-party identity in every shape ECM has.

    ``smtp_user`` / ``username`` are the IDENTITY halves of pairs whose secret
    half (``smtp_password`` / ``password``) is already redacted — the exact
    asymmetry this bead is about. ``plex_token`` / ``emby_api_key`` /
    ``jellyfin_api_key`` are whole bearer credentials that the exact-match
    denylist missed entirely.
    """
    s = MagicMock()
    s.model_dump.return_value = {
        "url": "http://dispatcharr:9191",
        "username": CONNECTION_USERNAME,
        "password": "<dispatcharr-connection-secret>",
        "smtp_user": SMTP_USERNAME,
        "smtp_password": "<smtp-relay-secret>",
        "smtp_host": "smtp.example.test",
        "plex_token": PLEX_TOKEN,
        "plex_base_url": "http://plex.example.test:32400",
        "emby_api_key": EMBY_API_KEY,
        "jellyfin_api_key": JELLYFIN_API_KEY,
        "theme": "dark",
    }
    return s


def _mock_engine():
    eng = MagicMock()
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = (0, 0, 0)
    eng.connect.return_value.__enter__ = MagicMock(return_value=conn)
    eng.connect.return_value.__exit__ = MagicMock(return_value=False)
    return eng


def _create_auth_schema(path):
    """Create the four real auth tables in a SQLite file.

    The schema comes from ``models`` rather than from hand-written DDL so the
    fixture cannot drift from the columns the producer actually has to purge.
    """
    from sqlalchemy import create_engine

    from models import Base, PasswordResetToken, User, UserIdentity, UserSession

    engine = create_engine("sqlite:///%s" % path)
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            UserSession.__table__,
            UserIdentity.__table__,
            PasswordResetToken.__table__,
        ],
    )
    engine.dispose()


def _seed_auth_tables(path):
    """Auth schema plus the exact rows the external reviewer pulled out of a
    built standard artifact (findings A-1 and A-2)."""
    _create_auth_schema(path)

    now = "2026-08-17 00:00:00"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "INSERT INTO users (id, username, email, password_hash, auth_provider, "
            "display_name, is_active, is_admin, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (1, ADMIN_USERNAME, ADMIN_EMAIL, ADMIN_PASSWORD_HASH, "local",
             "Local Admin", 1, 1, now, now),
        )
        conn.execute(
            "INSERT INTO user_sessions (id, user_id, refresh_token_hash, "
            "prior_refresh_token_hash, ip_address, user_agent, expires_at, "
            "created_at, last_used_at, is_revoked) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (1, 1, REFRESH_TOKEN_HASH, PRIOR_REFRESH_TOKEN_HASH, SESSION_IP_ADDRESS,
             SESSION_USER_AGENT, now, now, now, 0),
        )
        conn.execute(
            "INSERT INTO user_identities (id, user_id, provider, external_id, "
            "identifier, linked_at) VALUES (?,?,?,?,?,?)",
            (1, 1, "oidc", OIDC_EXTERNAL_ID, OIDC_IDENTIFIER, now),
        )
        conn.execute(
            "INSERT INTO password_reset_tokens (id, user_id, token_hash, "
            "expires_at, created_at) VALUES (?,?,?,?,?)",
            (1, 1, RESET_TOKEN_HASH, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_journal_db(path):
    """A real journal.db carrying, in the shapes the live app writes them:

    * ``alert_methods.config`` with the SMTP relay IDENTITY beside its secret and
      a Telegram chat id beside its bot token,
    * an ``alert_methods`` row whose config is TRUNCATED and cannot be parsed
      (finding A-3 — the producer used to ship such a row verbatim),
    * the four auth tables, populated (findings A-1 / A-2).
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE alert_methods (id INTEGER PRIMARY KEY, method_type TEXT, config TEXT)"
        )
        conn.execute(
            "INSERT INTO alert_methods (id, config) VALUES (?, ?)",
            (
                1,
                json.dumps(
                    {
                        "host": "smtp.example.test",
                        "username": SMTP_USERNAME,
                        "password": "<smtp-relay-secret>",
                    }
                ),
            ),
        )
        conn.execute(
            "INSERT INTO alert_methods (id, config) VALUES (?, ?)",
            (
                2,
                json.dumps(
                    {
                        "bot_token": "<telegram-bot-secret>",
                        "chat_id": TELEGRAM_CHAT_ID,
                    }
                ),
            ),
        )
        # Unparseable, and a JSON scalar — neither is a dict, so neither can be
        # shown to be free of credentials.
        conn.execute(
            "INSERT INTO alert_methods (id, config) VALUES (?, ?)",
            (3, TRUNCATED_ALERT_CONFIG),
        )
        conn.execute(
            "INSERT INTO alert_methods (id, config) VALUES (?, ?)",
            (4, json.dumps(NON_OBJECT_ALERT_SECRET)),
        )
        conn.execute(
            "INSERT INTO alert_methods (id, method_type, config) VALUES (?, ?, ?)",
            (5, "ntfy", json.dumps({
                "server_url": "https://ntfy.example.test/base/",
                "topic": NTFY_TOPIC,
                "access_token": NTFY_TOKEN,
            })),
        )
        conn.commit()
    finally:
        conn.close()
    _seed_auth_tables(path)
    _seed_non_permitted_tables(path)


def _seed_non_permitted_tables(path):
    """Tables the ALLOWLIST does not permit, each holding a marker value.

    Six carry the values the third review round found still shipping; three more
    are tables that round did not name; the last is an ORPHAN table that no
    current model declares at all (the removed pre-v0.13 health-monitor
    subsystem, which persists in long-running installs — see
    ``docs/database_migrations.md``). That last one is the case that decides the
    design: a denylist maintained by reading ``models.py`` cannot cover a table
    that is not in ``models.py``.

    Deliberately hand-written DDL with only the marker columns. The producer must
    drop these tables whatever their shape, and a fixture that mirrored the real
    models would suggest the rule keys off the schema. It does not — it keys off
    the table NAME not being permitted.
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE session_telemetry (id INTEGER PRIMARY KEY, "
            "dispatcharr_username TEXT, emby_user_name TEXT, plex_user_name TEXT, "
            "jellyfin_user_name TEXT)"
        )
        conn.execute(
            "INSERT INTO session_telemetry VALUES (1,?,?,?,?)",
            (TELEMETRY_DISPATCHARR_USERNAME, TELEMETRY_EMBY_USER_NAME,
             TELEMETRY_PLEX_USER_NAME, TELEMETRY_JELLYFIN_USER_NAME),
        )
        conn.execute(
            "CREATE TABLE unique_client_connections (id INTEGER PRIMARY KEY, "
            "ip_address TEXT, username TEXT)"
        )
        conn.execute(
            "INSERT INTO unique_client_connections VALUES (1,?,?)",
            (CLIENT_CONNECTION_IP, CLIENT_CONNECTION_USERNAME),
        )
        conn.execute(
            "CREATE TABLE m3u_digest_settings (id INTEGER PRIMARY KEY, "
            "email_recipients TEXT)"
        )
        conn.execute(
            "INSERT INTO m3u_digest_settings VALUES (1,?)",
            (json.dumps([DIGEST_EMAIL_RECIPIENT]),),
        )
        conn.execute("CREATE TABLE journal_entries (id INTEGER PRIMARY KEY, after_value TEXT)")
        conn.execute("INSERT INTO journal_entries VALUES (1,?)", (JOURNAL_ENTRY_AFTER_VALUE,))
        conn.execute("CREATE TABLE stream_stats (id INTEGER PRIMARY KEY, error_message TEXT)")
        conn.execute("INSERT INTO stream_stats VALUES (1,?)", (STREAM_PROBE_ERROR,))
        conn.execute("CREATE TABLE services (id TEXT PRIMARY KEY, health_endpoint TEXT)")
        conn.execute("INSERT INTO services VALUES ('ecm',?)", (VESTIGIAL_SERVICE_ENDPOINT,))
        conn.commit()
    finally:
        conn.close()


def _m3u_accounts():
    """Two accounts in the shape a live Dispatcharr 0.28.2 gather returns.

    Read off the recorded drill artifact
    ``~/ecm/backup-restore-runs/2026-08-09-run19`` — in particular the SECOND
    copy of the username nested inside
    ``profiles[].custom_properties.user_info``, which the drill found in clear
    beside a redacted password in the same blob.
    """
    return [
        {
            "id": 1,
            "name": "Provider XC",
            "account_type": "XC",
            "server_url": CLEAN_M3U_SERVER_URL,
            "username": XC_USERNAME,
            "password": XC_PASSWORD,
            "profiles": [
                {
                    "id": 1,
                    "name": "Provider Default",
                    "custom_properties": {
                        "server_info": {"url": "provider.example.test", "port": "80"},
                        "user_info": {
                            "username": XC_USERNAME,
                            "password": XC_PASSWORD,
                            "status": "Active",
                            "max_connections": "5",
                        },
                    },
                }
            ],
        },
        {
            "id": 2,
            "name": "Provider STD",
            "account_type": "STD",
            # A plain-M3U account's whole credential lives in the URL's query
            # string; no credential-named key carries it.
            "server_url": M3U_URL_WITH_QUERY_CREDS,
            "username": None,
            "password": "",
        },
    ]


def _epg_sources():
    return [
        {
            "id": 1,
            "name": "Clean EPG",
            "source_type": "xmltv",
            "url": CLEAN_EPG_URL,
            "username": EPG_USERNAME,
        },
        {
            "id": 2,
            "name": "Userinfo EPG",
            "source_type": "xmltv",
            # Credentials in the URL's userinfo component.
            "url": EPG_URL_WITH_USERINFO,
        },
    ]


def _build(tmp_path, **kwargs):
    """Build a REAL artifact on disk against a seeded temp CONFIG dir."""
    config_dir = tmp_path
    journal = config_dir / "journal.db"
    _seed_journal_db(journal)
    (config_dir / "settings.json").write_text("{}")

    client = MagicMock()
    client.get_m3u_accounts = AsyncMock(return_value=_m3u_accounts())
    client.get_epg_sources = AsyncMock(return_value=_epg_sources())
    client.get_channel_groups = AsyncMock(return_value=[])
    client.get_channel_profiles = AsyncMock(return_value=[])
    client.get_stream_profiles = AsyncMock(return_value=[])
    client.get_channels = AsyncMock(return_value={"count": 0, "next": None, "results": []})
    client.get_streams = AsyncMock(return_value={"count": 0, "next": None, "results": []})
    client.get_users = AsyncMock(
        return_value=[
            {
                "id": 1,
                "username": DISPATCHARR_OPERATOR_USERNAME,
                "email": "operator@example.test",
                "is_superuser": True,
            }
        ]
    )
    client.get_user_agents = AsyncMock(return_value=[])
    client.get_dvr_rules = AsyncMock(return_value=[])
    client.get_core_settings = AsyncMock(return_value=[])
    client.get_all_logos_paginated = AsyncMock(return_value=[])
    client.fetch_logo_image = AsyncMock(return_value=None)

    session = MagicMock()
    session.query.return_value.all.return_value = []
    session.query.return_value.filter_by.return_value.all.return_value = []
    session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = []
    session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

    with patch.object(backup_mod, "CONFIG_DIR", config_dir), \
         patch.object(backup_mod, "CONFIG_FILE", config_dir / "settings.json"), \
         patch.object(backup_mod, "JOURNAL_DB_FILE", journal), \
         patch.object(backup_mod, "get_engine", return_value=_mock_engine()), \
         patch.object(backup_mod, "get_settings", return_value=_mock_settings()), \
         patch.object(backup_mod, "get_session", return_value=session), \
         patch.object(backup_mod, "get_client", return_value=client):
        return asyncio.get_event_loop().run_until_complete(
            build_backup_artifact(dest_dir=config_dir, **kwargs)
        )


def _all_member_bytes(zip_path) -> bytes:
    """Every DECOMPRESSED member concatenated.

    Scanning the ZIP file's raw bytes would be a proxy, not the check: DEFLATE
    hides a plaintext value from a substring search.
    """
    out = bytearray()
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            out += zf.read(name)
    return bytes(out)


def _category(zip_path, name) -> dict:
    with zipfile.ZipFile(zip_path) as zf:
        return yaml.safe_load(zf.read("categories/%s.yaml" % name))


@pytest.fixture(scope="module")
def standard_artifact(tmp_path_factory):
    """One standard artifact, built once, read by every scan below."""
    return _build(tmp_path_factory.mktemp("standard"))


# ---------------------------------------------------------------------------
# The property, stated over the WHOLE artifact
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ALL_THIRD_PARTY_VALUES)
def test_no_third_party_value_survives_in_any_member(standard_artifact, value):
    """No seeded third-party identity/credential appears in ANY member.

    This is the artifact-level statement of the property. It covers the two
    places the drill read (``m3u_accounts.yaml`` account row and nested
    ``user_info``) and every place it did not: the settings category, the EPG
    category, credential-bearing URL values, and ``journal.db``.
    """
    assert value.encode() not in _all_member_bytes(standard_artifact.zip_path)


def test_m3u_username_redacted_in_both_places(standard_artifact):
    """The reported case, pinned by structure rather than by byte scan.

    The account-level ``username`` and the second copy inside
    ``profiles[].custom_properties.user_info`` must BOTH carry the sentinel, so
    the restore recognizes them and reports them for re-entry.
    """
    accounts = _category(standard_artifact.zip_path, "m3u_accounts")["dispatcharr"][
        "m3u_accounts"
    ]
    xc = next(a for a in accounts if a["name"] == "Provider XC")
    assert xc["username"] == backup_mod.REDACTED
    assert xc["password"] == backup_mod.REDACTED
    user_info = xc["profiles"][0]["custom_properties"]["user_info"]
    assert user_info["username"] == backup_mod.REDACTED
    assert user_info["password"] == backup_mod.REDACTED
    # Non-credential subscription facts are NOT collateral damage.
    assert user_info["status"] == "Active"


def test_epg_source_username_redacted(standard_artifact):
    sources = _category(standard_artifact.zip_path, "epg_sources")["dispatcharr"][
        "epg_sources"
    ]
    clean = next(s for s in sources if s["name"] == "Clean EPG")
    assert clean["username"] == backup_mod.REDACTED


def test_settings_identity_halves_redacted(standard_artifact):
    """Every settings field whose secret half was already redacted while its
    identity half was not — and the three bearer credentials the exact-match
    denylist missed outright."""
    settings = _category(standard_artifact.zip_path, "settings")["settings"]
    for key in _NEWLY_REDACTED_SETTINGS:
        assert settings[key] == backup_mod.REDACTED, key
    # Host/base-url fields are not credentials and the operator needs them.
    assert settings["smtp_host"] == "smtp.example.test"
    assert settings["theme"] == "dark"


def test_url_with_query_credentials_redacted(standard_artifact):
    """A credential in a URL's QUERY STRING is a credential.

    The STD M3U account's whole provider credential lives in
    ``server_url``; no credential-named key carries it, so a key denylist alone
    cannot see it.
    """
    accounts = _category(standard_artifact.zip_path, "m3u_accounts")["dispatcharr"][
        "m3u_accounts"
    ]
    std = next(a for a in accounts if a["name"] == "Provider STD")
    assert std["server_url"] == backup_mod.REDACTED


def test_url_with_userinfo_redacted(standard_artifact):
    """A credential in a URL's USERINFO component is a credential."""
    sources = _category(standard_artifact.zip_path, "epg_sources")["dispatcharr"][
        "epg_sources"
    ]
    userinfo = next(s for s in sources if s["name"] == "Userinfo EPG")
    assert userinfo["url"] == backup_mod.REDACTED


def test_clean_urls_are_untouched(standard_artifact):
    """A URL carrying no credential survives verbatim.

    Blanket-redacting URL fields would leave every restored account and source
    with no address at all — the restore needs these.
    """
    sources = _category(standard_artifact.zip_path, "epg_sources")["dispatcharr"][
        "epg_sources"
    ]
    clean = next(s for s in sources if s["name"] == "Clean EPG")
    assert clean["url"] == CLEAN_EPG_URL

    accounts = _category(standard_artifact.zip_path, "m3u_accounts")["dispatcharr"][
        "m3u_accounts"
    ]
    xc = next(a for a in accounts if a["name"] == "Provider XC")
    assert xc["server_url"] == CLEAN_M3U_SERVER_URL


def test_alert_method_identity_scrubbed_from_journal_db(standard_artifact, tmp_path):
    """``journal.db`` is a member of the artifact and gets the same treatment.

    ``alert_methods.config`` carries an SMTP relay username beside its password
    and a Telegram chat id beside its bot token. Both secret halves were already
    scrubbed; both identity halves were not.
    """
    extracted = tmp_path / "journal.db"
    with zipfile.ZipFile(standard_artifact.zip_path) as zf:
        extracted.write_bytes(zf.read("journal.db"))
    conn = sqlite3.connect(str(extracted))
    try:
        rows = dict(conn.execute("SELECT id, config FROM alert_methods"))
    finally:
        conn.close()
    smtp = json.loads(rows[1])
    assert smtp["username"] == backup_mod.REDACTED
    assert smtp["password"] == backup_mod.REDACTED
    assert smtp["host"] == "smtp.example.test"  # not a credential
    telegram = json.loads(rows[2])
    assert telegram["chat_id"] == backup_mod.REDACTED
    assert telegram["bot_token"] == backup_mod.REDACTED
    ntfy = json.loads(rows[5])
    assert ntfy["server_url"] == "https://ntfy.example.test/base/"
    assert ntfy["topic"] == backup_mod.REDACTED
    assert ntfy["access_token"] == backup_mod.REDACTED
    assert "_ecm_ntfy_destination_hmac_v1" not in ntfy


# ---------------------------------------------------------------------------
# A-1 / A-2 — the operator's own credentials and their third-party identity
#
# Read out of the ARCHIVED journal.db with sqlite3, which is how the reviewer
# found them: no gather, no YAML, no redactor key ever touches a DB column, so a
# test that only reads the categories cannot see any of this.
# ---------------------------------------------------------------------------


def _extract_journal_db(zip_path, dest):
    with zipfile.ZipFile(zip_path) as zf:
        dest.write_bytes(zf.read("journal.db"))
    return sqlite3.connect(str(dest))


@pytest.mark.parametrize("value", ALL_OPERATOR_ACCOUNT_VALUES)
def test_no_operator_account_value_survives_in_any_member(standard_artifact, value):
    """The whole-archive statement of findings A-1 and A-2.

    Scanning the DECOMPRESSED members rather than querying the tables is
    deliberate and is the only version of this check that can fail correctly: a
    SQLite ``DELETE`` unlinks a row's cells into the freelist and leaves their
    bytes in the page file, so ``SELECT COUNT(*) == 0`` is satisfied by a
    database that still carries every purged password hash verbatim.
    """
    assert value.encode() not in _all_member_bytes(standard_artifact.zip_path)


@pytest.mark.parametrize("value", ALL_ROUND_THREE_VALUES)
def test_no_round_three_value_survives_in_any_member(standard_artifact, value):
    """The values the third review round found still shipping, plus four it did not.

    These are EXAMPLES of the property, not the specification of it — the
    specification is the table allowlist, pinned by the two tests below. This
    test exists because a value-level scan is the only check that can fail while
    the table-level one passes (a permitted table could still carry something it
    should not), and because it reads the DECOMPRESSED archive rather than the
    query results, so it fails if the ``DROP``/``VACUUM``/``secure_delete``
    combination ever stops actually removing bytes from the page file.
    """
    assert value.encode() not in _all_member_bytes(standard_artifact.zip_path)


def test_the_shipped_journal_db_contains_only_permitted_tables(
    standard_artifact, tmp_path
):
    """THE INVARIANT. Not "these tables are absent" — "only these are present".

    Stated as a subset relation over the WHOLE shipped schema, so it is complete
    and mechanical: any table the producer starts carrying, for any reason,
    including one added to the schema years from now by someone who has never
    read this file, fails here until it is deliberately permitted.

    This is the check that the previous two rounds could not have written,
    because a denylist has nothing to compare against. Three rounds each found
    more tables to remove; this one cannot be incomplete in that direction.
    """
    conn = _extract_journal_db(standard_artifact.zip_path, tmp_path / "journal.db")
    try:
        shipped = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not row[0].startswith("sqlite_")
        }
    finally:
        conn.close()

    permitted = set(backup_mod._STANDARD_ARTIFACT_TABLES)
    assert shipped <= permitted, (
        "the standard artifact's journal.db carries table(s) that are not in "
        "routers.backup._STANDARD_ARTIFACT_TABLES: %s. Add the table to that "
        "dict WITH A REASON if a standard artifact genuinely needs it, or leave "
        "it out and it will be dropped." % sorted(shipped - permitted)
    )
    # And the seeded non-permitted tables are gone, not merely emptied, so this
    # test cannot pass on a database that still has the table with zero rows.
    for table in ("users", "user_sessions", "user_identities",
                  "password_reset_tokens", "session_telemetry",
                  "unique_client_connections", "m3u_digest_settings",
                  "journal_entries", "stream_stats", "services"):
        assert table not in shipped, "%s shipped" % table


def test_every_journal_db_table_is_classified():
    """A new table cannot reach production without a keep/drop DECISION.

    The allowlist already makes an unclassified table SAFE — it is dropped by
    default, so nothing leaks. This test makes it DELIBERATE: every table any
    model declares must appear in exactly one of the two registries, each of
    which carries the reason beside the entry.

    WOULD THIS HAVE CAUGHT ``user_identities``? Yes, twice over, and that is the
    point. Under the allowlist the table ships nothing the moment it exists,
    because it is not permitted. And the moment the model was added, this test
    would have failed until somebody classified it — whereas the marker-based
    ratchet it replaces (``password|secret|token_hash|refresh_token|api_key``)
    never fired on ``external_id`` and passed the whole time the table was
    leaking the operator's OIDC identity. That test was calibrated to pass; this
    one is calibrated to the property.
    """
    from models import Base

    declared = set(Base.metadata.tables)
    permitted = set(backup_mod._STANDARD_ARTIFACT_TABLES)
    excluded = set(backup_mod._STANDARD_ARTIFACT_EXCLUDED)

    unclassified = sorted(declared - permitted - excluded)
    assert unclassified == [], (
        "these journal.db tables have no keep/drop decision recorded: %s. A "
        "standard artifact will DROP them (which is safe), but the decision must "
        "be explicit: add each to routers.backup._STANDARD_ARTIFACT_TABLES or "
        "._STANDARD_ARTIFACT_EXCLUDED with the reason." % unclassified
    )

    overlap = sorted(permitted & excluded)
    assert overlap == [], "classified as both permitted and excluded: %s" % overlap

    # Every reason is a real reason. A blank or placeholder entry would satisfy
    # the membership checks above while documenting nothing.
    for name, registry in (
        ("_STANDARD_ARTIFACT_TABLES", backup_mod._STANDARD_ARTIFACT_TABLES),
        ("_STANDARD_ARTIFACT_EXCLUDED", backup_mod._STANDARD_ARTIFACT_EXCLUDED),
    ):
        thin = sorted(t for t, reason in registry.items() if len(reason.strip()) < 30)
        assert thin == [], "%s entries with no substantive reason: %s" % (name, thin)


def test_the_permitted_set_is_configuration_not_history():
    """The allowlist stays small and stays about CONFIGURATION.

    A guard on the DIRECTION of drift rather than on a specific table. The way
    this design fails is not one obviously-wrong addition — it is a slow slide
    where "the operator would probably like their notifications back" gets a
    table permitted, then another, until the artifact is a full copy again and
    the allowlist is decorative.

    The two auth tables that started this bead and the telemetry tables the third
    round found are named explicitly, so re-permitting any of them is a conscious
    act that fails a test with a reason attached.
    """
    permitted = set(backup_mod._STANDARD_ARTIFACT_TABLES)

    never_permit = {
        "users", "user_sessions", "user_identities", "password_reset_tokens",
        "session_telemetry", "session_telemetry_user_daily",
        "unique_client_connections", "cloud_storage_targets", "sync_targets",
        "journal_entries",
    }
    assert permitted & never_permit == set(), (
        "a table carrying account state, viewer identity or credential material "
        "was added to the standard artifact allowlist: %s"
        % sorted(permitted & never_permit)
    )
    assert len(permitted) <= 20, (
        "the standard artifact allowlist has grown to %d tables. That is not "
        "automatically wrong, but it is the shape of the drift this test exists "
        "to make visible — confirm each addition is configuration a restore "
        "needs, then raise this bound deliberately." % len(permitted)
    )


def test_the_purge_is_logged_with_a_row_count_that_was_actually_measured(
    tmp_path, caplog
):
    """The one record that says the operator's accounts left the artifact.

    Counted with ``SELECT COUNT(*)`` BEFORE the delete rather than read off
    ``cursor.rowcount`` after it: SQLite's truncate optimization makes the change
    count of an unqualified ``DELETE FROM t`` build-dependent, so rowcount can
    read 0 on a build where rows were removed. A security log line that
    under-reports is worse than one that is absent, because it reads as proof
    nothing was there.
    """
    journal = tmp_path / "journal.db"
    _seed_journal_db(journal)
    with caplog.at_level("WARNING", logger=backup_mod.logger.name):
        scrubbed = backup_mod._scrub_journal_db_to_temp(journal)
    scrubbed.unlink()

    purge_lines = [
        r.getMessage() for r in caplog.records
        if "Dropped" in r.getMessage() and "non-permitted table" in r.getMessage()
    ]
    assert len(purge_lines) == 1, purge_lines
    for fragment in ("users=1 rows", "user_sessions=1 rows",
                     "user_identities=1 rows", "password_reset_tokens=1 rows",
                     "session_telemetry=1 rows", "unique_client_connections=1 rows",
                     "m3u_digest_settings=1 rows", "journal_entries=1 rows",
                     "stream_stats=1 rows", "services=1 rows"):
        assert fragment in purge_lines[0], purge_lines[0]


def test_an_accountless_instance_logs_no_purge_warning(tmp_path, caplog):
    """A WARNING that fires on every backup is a WARNING nobody reads.

    Auth is optional in ECM, so a perfectly ordinary instance has an empty
    ``users`` table and purges nothing. The line above must mean something
    happened.
    """
    journal = tmp_path / "journal.db"
    _create_auth_schema(journal)
    with caplog.at_level("WARNING", logger=backup_mod.logger.name):
        scrubbed = backup_mod._scrub_journal_db_to_temp(journal)
    scrubbed.unlink()

    assert [
        r.getMessage() for r in caplog.records
        if "Dropped" in r.getMessage() and "non-permitted table" in r.getMessage()
    ] == []


def test_no_permitted_table_holds_a_credential_shaped_column():
    """The kept tables are audited too, by column NAME this time.

    THIS TEST REPLACES A MARKER SCAN THAT WAS CALIBRATED TO PASS. The version it
    replaces ran the marker set ``password|secret|token_hash|refresh_token|
    api_key`` across every table NOT in the purge list and asserted the result was
    empty — and the round that wrote it disclosed, correctly, that it would not
    have caught the ``user_identities`` finding, because none of those markers
    appears in ``external_id``. A ratchet whose passing tells you nothing is worse
    than no ratchet, because it reads as coverage.

    The reason the same marker set is defensible HERE and was not there is the
    change of direction. There, the markers had to be complete over 42 tables to
    mean anything, and completeness over unbounded future column names is not
    achievable. Here they run over the 14 tables the allowlist permits, every one
    of which has been read column by column, and a marker hit means "this
    hand-audited set drifted" — a bounded claim the check can actually support.
    The unbounded half of the problem is carried by
    :func:`test_the_shipped_journal_db_contains_only_permitted_tables`, which
    does not depend on guessing column names at all.
    """
    from models import Base

    # Reviewed exceptions, each a reason rather than a convenience — and each
    # checked against the column's TYPE, not just its name.
    excused = {
        # ``Column(Boolean, default=True)`` (models.py) — "route this task's
        # alerts to email", a routing toggle. It holds no address: the recipient
        # list lives in m3u_digest_settings.email_recipients, which is why that
        # table is dropped. Asserted below rather than asserted-by-comment.
        ("scheduled_tasks", "send_to_email"),
    }
    markers = ("password", "secret", "token_hash", "refresh_token", "api_key",
               "external_id", "credential", "email", "ip_address")
    found = sorted(
        (table_name, column.name)
        for table_name in backup_mod._STANDARD_ARTIFACT_TABLES
        if table_name in Base.metadata.tables
        for column in Base.metadata.tables[table_name].columns
        if any(marker in column.name.lower() for marker in markers)
        and (table_name, column.name) not in excused
    )

    # The excusal is only valid while the column stays a boolean. If someone
    # widens it to carry an address, the excusal above becomes false and this
    # fails — the comment cannot silently go stale.
    from sqlalchemy import Boolean

    for table_name, column_name in excused:
        column = Base.metadata.tables[table_name].columns[column_name]
        assert isinstance(column.type, Boolean), (
            "%s.%s is excused from the credential-shaped-column check on the "
            "grounds that it is a boolean routing toggle, and it is no longer a "
            "boolean (%r). Re-examine the excusal."
            % (table_name, column_name, column.type)
        )
    assert found == [], (
        "a table PERMITTED into the standard artifact has a credential- or "
        "identity-shaped column: %s. Either drop the table from "
        "routers.backup._STANDARD_ARTIFACT_TABLES, or justify the column here "
        "explicitly — do not widen the marker list to make this pass." % found
    )


def test_permitted_table_cells_are_scrubbed_of_url_credentials(tmp_path):
    """Invariant 2 applies to the tables the allowlist KEEPS.

    ``dummy_epg_profiles`` is the concrete case: its logo and poster URL columns
    are operator free text, so an operator whose image service needs an API key
    puts that key in a permitted table. No key denylist sees it — the column is
    called ``channel_logo_url_template`` — which is exactly the blind spot that
    made the value-level URL rule necessary for the YAML categories in the first
    place.

    The rule is applied to every STRING cell of every permitted table rather than
    to a list of columns, so this test pins the behaviour and not the column.
    """
    journal = tmp_path / "journal.db"
    conn = sqlite3.connect(str(journal))
    try:
        conn.execute(
            "CREATE TABLE dummy_epg_profiles (id INTEGER PRIMARY KEY, name TEXT, "
            "channel_logo_url_template TEXT, title_template TEXT)"
        )
        conn.execute(
            "INSERT INTO dummy_epg_profiles VALUES (1,?,?,?)",
            (
                "Sports",
                # Same shape as the live instance's, but with a credential.
                "http://images.example.test/{team}/thumb?" + "api" + "key=" + _URL_RIGHT_HALF,
                "{title} — {date}",
            ),
        )
        # A clean permitted-table URL must survive: the restore needs the address.
        conn.execute(
            "CREATE TABLE ffmpeg_profiles (id INTEGER PRIMARY KEY, config TEXT)"
        )
        conn.execute(
            "INSERT INTO ffmpeg_profiles VALUES (1,?)",
            (json.dumps({"logo": "https://cdn.example.test/logo.png"}),),
        )
        conn.commit()
    finally:
        conn.close()

    scrubbed = backup_mod._scrub_journal_db_to_temp(journal)
    try:
        assert _URL_RIGHT_HALF.encode() not in scrubbed.read_bytes()
        conn = sqlite3.connect(str(scrubbed))
        try:
            logo, title = conn.execute(
                "SELECT channel_logo_url_template, title_template "
                "FROM dummy_epg_profiles"
            ).fetchone()
            assert _URL_RIGHT_HALF not in logo
            # The non-URL template is untouched — the rule is targeted, not a
            # blanket wipe of a table the operator needs back.
            assert title == "{title} — {date}"
            cfg = json.loads(conn.execute("SELECT config FROM ffmpeg_profiles").fetchone()[0])
            assert cfg == {"logo": "https://cdn.example.test/logo.png"}
        finally:
            conn.close()
    finally:
        scrubbed.unlink()


# ---------------------------------------------------------------------------
# A-3 — redaction fails CLOSED
# ---------------------------------------------------------------------------


def test_unparseable_alert_config_does_not_ship(standard_artifact, tmp_path):
    """The reviewer's reproduction: a truncated config blob shipped verbatim.

    This is an EXAMPLE of the property, not the property. The property is that a
    row the redactor could not parse does not ship at all — see
    :func:`test_non_object_alert_config_does_not_ship` for the same rule on a
    different unparseable shape.
    """
    blob = _all_member_bytes(standard_artifact.zip_path)
    assert _TRUNCATED_SECRET.encode() not in blob
    conn = _extract_journal_db(standard_artifact.zip_path, tmp_path / "journal.db")
    try:
        rows = dict(conn.execute("SELECT id, config FROM alert_methods"))
    finally:
        conn.close()
    # The whole blob, not a key inside it — nothing in it was ever parsed.
    assert rows[3] == backup_mod.REDACTED
    # The row itself SURVIVES: name/type/enabled are not credentials and the
    # restore wants them, and a dropped row is an invisible loss.
    assert 3 in rows


def test_non_object_alert_config_does_not_ship(standard_artifact, tmp_path):
    """A config that parses to a JSON SCALAR is equally unproven and equally refused."""
    assert NON_OBJECT_ALERT_SECRET.encode() not in _all_member_bytes(
        standard_artifact.zip_path
    )
    conn = _extract_journal_db(standard_artifact.zip_path, tmp_path / "journal.db")
    try:
        rows = dict(conn.execute("SELECT id, config FROM alert_methods"))
    finally:
        conn.close()
    assert rows[4] == backup_mod.REDACTED


def test_unreadable_journal_db_fails_the_backup_closed(tmp_path):
    """A database the scrub cannot read fails the BACKUP; it does not ship raw.

    This is the least likely of the three A-3 paths to fire and the most
    dangerous when it does: the fallback shipped a byte-for-byte copy of the LIVE
    database — every credential, every hash, no redaction of any kind — behind a
    200 and a WARNING nobody reads.
    """
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    journal = config_dir / "journal.db"
    journal.write_bytes(b"this is not a SQLite database at all\n" * 64)

    with pytest.raises(backup_mod.BackupScrubError):
        backup_mod._scrub_journal_db_to_temp(journal)


def test_a_failed_scrub_leaves_no_unscrubbed_copy_on_disk(tmp_path, monkeypatch):
    """The temp copy is raw journal.db. A failed scrub must destroy it.

    The caller only unlinks the path this function RETURNED, and on failure it
    returns nothing — so without this the live database is left readable in the
    system temp directory by whatever else can read it.
    """
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    monkeypatch.setattr(backup_mod.tempfile, "tempdir", str(temp_root))

    journal = tmp_path / "journal.db"
    journal.write_bytes(b"not a database " * 100)

    with pytest.raises(backup_mod.BackupScrubError):
        backup_mod._scrub_journal_db_to_temp(journal)

    assert list(temp_root.iterdir()) == []


def test_a_failed_scrub_fails_the_whole_artifact_build(tmp_path, monkeypatch):
    """End to end: no artifact and no sidecar survive a scrub that could not run.

    Failing the whole backup, rather than emitting an artifact with no
    ``journal.db``, is the deliberate choice: a backup that silently drops the
    operator's entire ECM state is data loss wearing a success response, while a
    loud failure is recoverable and cannot leak.
    """
    dest = tmp_path / "unreadable"
    dest.mkdir()

    def _corrupt_seed(path):
        Path(path).write_bytes(b"definitely not SQLite\n" * 128)

    monkeypatch.setattr(sys.modules[__name__], "_seed_journal_db", _corrupt_seed)
    with pytest.raises(backup_mod.BackupScrubError):
        _build(dest)

    leftovers = sorted(p.name for p in dest.iterdir() if p.suffix in (".zip", ".sha256"))
    assert leftovers == []


# ---------------------------------------------------------------------------
# The narrow exemption, pinned so it cannot widen
# ---------------------------------------------------------------------------


def test_dispatcharr_users_username_survives(standard_artifact):
    """The operator's OWN Dispatcharr user list keeps its usernames.

    ``dbas.importers.users`` creates and collision-checks on ``username``; it is
    the category's natural key and it names an account on the operator's own
    paired instance, not a third-party subscription. Redacting it would delete
    the ``dispatcharr_users`` restore path outright.
    """
    users = _category(standard_artifact.zip_path, "dispatcharr_users")["dispatcharr"][
        "dispatcharr_users"
    ]
    assert users[0]["username"] == DISPATCHARR_OPERATOR_USERNAME


def test_identity_exemption_is_exactly_one_named_category():
    """The exemption list is a closed, reviewed set — not a growing escape hatch."""
    assert backup_mod._IDENTITY_EXEMPT_CATEGORIES == frozenset({"dispatcharr_users"})
    assert backup_mod._IDENTITY_EXEMPT_CATEGORIES <= set(backup_mod.RESTORABLE_SECTIONS)


# ---------------------------------------------------------------------------
# The encrypted path is unaffected
# ---------------------------------------------------------------------------


def test_encrypted_migration_artifact_still_carries_identities(tmp_path):
    """The cred-carrying encrypted artifact round-trips EVERY identity value.

    This is the constraint that makes the fix safe to ship: the Migration card's
    artifact is the one that can restore credentials, so widening redaction must
    not quietly hollow it out. Decrypt and assert each value is present.
    """
    art = _build(
        tmp_path,
        passphrase=GOOD_PASS,
        include_credentials=True,
        acknowledge_unrecoverable=True,
    )
    assert art.encrypted is True
    dec = tmp_path / "plain.zip"
    artifact_crypto.decrypt_file(art.zip_path, GOOD_PASS, dec)
    members = _all_member_bytes(dec)
    for value in (
        XC_USERNAME,
        XC_PASSWORD,
        EPG_USERNAME,
        CONNECTION_USERNAME,
        SMTP_USERNAME,
        PLEX_TOKEN,
        _URL_LEFT_HALF,
        _URL_RIGHT_HALF,
        NTFY_TOPIC,
        NTFY_TOKEN,
    ):
        assert value.encode() in members, "encrypted migration artifact lost %s" % value
    with zipfile.ZipFile(dec) as zf:
        assert json.loads(zf.read("manifest.json"))["redacted"] is False


def test_encrypted_artifact_without_credentials_is_still_fully_redacted(tmp_path):
    """Redact-then-encrypt: a passphrase alone does NOT re-admit identities."""
    art = _build(tmp_path, passphrase=GOOD_PASS, acknowledge_unrecoverable=True)
    dec = tmp_path / "plain.zip"
    artifact_crypto.decrypt_file(art.zip_path, GOOD_PASS, dec)
    members = _all_member_bytes(dec)
    for value in ALL_THIRD_PARTY_VALUES + ALL_OPERATOR_ACCOUNT_VALUES:
        assert value.encode() not in members, value


def test_encrypted_migration_artifact_still_carries_the_operator_accounts(tmp_path):
    """The migration path keeps every account row intact, rows and bytes.

    This is the constraint that makes the account purge safe to ship: an operator
    migrating between instances uses the encrypted cred-carrying artifact, and it
    must still restore their login without re-entry. It is also the answer to
    "what do I use if I DO want my accounts" in the operator-facing notice.
    """
    art = _build(
        tmp_path,
        passphrase=GOOD_PASS,
        include_credentials=True,
        acknowledge_unrecoverable=True,
    )
    dec = tmp_path / "plain.zip"
    artifact_crypto.decrypt_file(art.zip_path, GOOD_PASS, dec)

    for value in ALL_OPERATOR_ACCOUNT_VALUES:
        assert value.encode() in _all_member_bytes(dec), (
            "encrypted migration artifact lost %s" % value
        )

    conn = _extract_journal_db(dec, tmp_path / "journal.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        row = conn.execute(
            "SELECT username, password_hash FROM users"
        ).fetchone()
        assert row == (ADMIN_USERNAME, ADMIN_PASSWORD_HASH)
        assert conn.execute("SELECT COUNT(*) FROM user_identities").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0] == 1
        # The cred-carrying path returns before the scrub runs at all, so the
        # unparseable row is untouched here too.
        rows = dict(conn.execute("SELECT id, config FROM alert_methods"))
        assert rows[3] == TRUNCATED_ALERT_CONFIG
    finally:
        conn.close()


def test_encrypted_migration_artifact_carries_every_table_byte_for_byte(tmp_path):
    """Invariant 5: the allowlist does not touch the encrypted artifact.

    The encrypted cred-carrying artifact is the migration path, so it must carry
    the WHOLE database — including every table the standard artifact now drops.
    Asserted two ways, because either alone is a proxy:

    * the shipped ``journal.db`` member is byte-for-byte identical to the live
      file (SHA-256), which is the strongest possible statement and covers
      tables nobody thought to name; and
    * the table SET matches exactly, which is what actually fails readably if a
      future change starts scrubbing this path.

    ``include_credentials`` returns before the scrub runs at all, so the property
    is structural rather than a matter of the allowlist happening to permit
    everything.
    """
    source = tmp_path / "journal.db"
    art = _build(
        tmp_path,
        passphrase=GOOD_PASS,
        include_credentials=True,
        acknowledge_unrecoverable=True,
    )
    dec = tmp_path / "plain.zip"
    artifact_crypto.decrypt_file(art.zip_path, GOOD_PASS, dec)

    with zipfile.ZipFile(dec) as zf:
        shipped_bytes = zf.read("journal.db")
    assert hashlib.sha256(shipped_bytes).hexdigest() == hashlib.sha256(
        source.read_bytes()
    ).hexdigest(), "the encrypted artifact's journal.db is not the live database"

    live = sqlite3.connect(str(source))
    try:
        live_tables = {
            r[0] for r in live.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        live.close()
    conn = _extract_journal_db(dec, tmp_path / "enc-journal.db")
    try:
        shipped_tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    assert shipped_tables == live_tables

    # And it really does include tables the standard artifact drops — otherwise
    # the equality above could be satisfied by a fixture that seeded none.
    for table in ("users", "session_telemetry", "unique_client_connections",
                  "m3u_digest_settings", "journal_entries", "services"):
        assert table in shipped_tables, "%s missing from the migration path" % table


def test_a_dropped_table_heals_empty_on_the_restore_that_recreates_it(tmp_path):
    """Invariant 3, for EVERY dropped table rather than for a sampled one.

    The allowlist is only safe if ``init_db()``'s ``Base.metadata.create_all``
    genuinely puts back every table it removed. Asserted over the whole
    classified exclusion set — completeness over sampling, because a single
    table that failed to heal would be a restored instance broken in exactly one
    feature, which is the hardest kind of bug to attribute to a backup.

    The vestigial tables are the deliberate exception and are asserted to STAY
    gone: no model declares them, so nothing recreates them, and that is correct
    — they are orphans of removed features (``docs/database_migrations.md``).
    """
    from sqlalchemy import create_engine

    import export_models  # noqa: F401 — registers CloudStorageTarget / SyncTarget
    from models import Base

    live = tmp_path / "journal.db"
    engine = create_engine("sqlite:///%s" % live)
    Base.metadata.create_all(engine)
    engine.dispose()
    # An orphan table of the kind a long-running install carries.
    conn = sqlite3.connect(str(live))
    try:
        conn.execute("CREATE TABLE services (id TEXT PRIMARY KEY, health_endpoint TEXT)")
        conn.commit()
    finally:
        conn.close()

    scrubbed = backup_mod._scrub_journal_db_to_temp(live)
    try:
        restored = tmp_path / "restored.db"
        restored.write_bytes(scrubbed.read_bytes())
    finally:
        scrubbed.unlink()

    engine = create_engine("sqlite:///%s" % restored)
    Base.metadata.create_all(engine)  # what init_db() does on every restore
    engine.dispose()

    conn = sqlite3.connect(str(restored))
    try:
        after = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")
        }
        excluded = set(backup_mod._STANDARD_ARTIFACT_EXCLUDED)
        missing = sorted(excluded - after)
        assert missing == [], (
            "these tables were dropped from the artifact and NOT recreated by "
            "create_all, so a restored instance is missing them: %s" % missing
        )
        non_empty = sorted(
            (t, conn.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0])
            for t in excluded
            if conn.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
        )
        assert non_empty == [], non_empty
        assert "services" not in after, (
            "an orphan table that no model declares came back, which would mean "
            "something still recreates it"
        )
    finally:
        conn.close()


def test_a_restore_keeps_the_owner_even_though_no_users_table_ships(tmp_path):
    """The availability guarantee survives the change from DELETE to DROP.

    Round 2 kept the destination's accounts by capturing them before the swap and
    re-asserting them after. That re-assert reads ``PRAGMA table_info(users)`` and
    skips the table when it comes back empty — which, once the allowlist DROPS
    ``users`` instead of emptying it, would have meant the re-assert silently did
    nothing and an admin restoring a backup was logged out of their own instance
    and dropped at the setup wizard. Behind a 200.

    That is the exact shape of the failure this bead keeps producing: a fix that
    is correct for the case it was written against and wrong one layer over. The
    re-assert now recreates the table from the model when the artifact did not
    ship it.
    """
    from sqlalchemy import create_engine

    from models import Base

    live = tmp_path / "journal.db"
    engine = create_engine("sqlite:///%s" % live)
    Base.metadata.create_all(engine)
    engine.dispose()
    now = "2026-08-17 00:00:00"
    conn = sqlite3.connect(str(live))
    try:
        conn.execute(
            "INSERT INTO users (id, username, email, password_hash, auth_provider, "
            "display_name, is_active, is_admin, created_at, updated_at) "
            "VALUES (1,'owner','owner@example.test',?,'local','Owner',1,1,?,?)",
            (ADMIN_PASSWORD_HASH, now, now),
        )
        conn.commit()
    finally:
        conn.close()

    with patch.object(backup_mod, "JOURNAL_DB_FILE", live):
        prior = backup_mod._capture_existing_auth_rows()
        assert len(prior["users"][1]) == 1

        artifact_db = backup_mod._scrub_journal_db_to_temp(live)
        try:
            probe = sqlite3.connect(str(artifact_db))
            try:
                assert probe.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name='users'"
                ).fetchone() is None, "the artifact shipped a users table"
            finally:
                probe.close()
            live.write_bytes(artifact_db.read_bytes())  # the wholesale swap
        finally:
            artifact_db.unlink()

        backup_mod._reassert_auth_rows_after_restore(prior)

    conn = sqlite3.connect(str(live))
    try:
        assert conn.execute("SELECT id, username FROM users").fetchall() == [
            (1, "owner")
        ]
    finally:
        conn.close()


def test_the_restore_names_the_configured_surfaces_this_instance_lost(tmp_path):
    """Invariant 4: the operator is told what to re-establish, from LIVE state.

    ``restored_files`` reports what landed and structurally cannot report what an
    artifact could not carry, so an operator whose cloud storage target silently
    vanished has no signal at all. The notice is derived by comparing the same
    live counts either side of the swap, which is what keeps it free of noise: an
    instance that never configured a cloud target is not told to re-establish
    one, and a notice that cries wolf is one nobody reads by the time it is true.
    """
    backup_mod._LAST_RESTORE_CONFIG_LOSSES.clear()
    backup_mod._LAST_RESTORE_CONFIG_LOSSES.update(
        {"cloud_storage_targets": 2, "m3u_digest_settings": 1}
    )
    live = tmp_path / "journal.db"
    conn = sqlite3.connect(str(live))
    try:
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO users VALUES (1)")  # not a lockout
        conn.commit()
    finally:
        conn.close()

    with patch.object(backup_mod, "JOURNAL_DB_FILE", live):
        notices = backup_mod._post_restore_account_notices()

    assert len(notices) == 1, notices
    assert "cloud storage target" in notices[0]
    assert "M3U digest settings" in notices[0]
    assert "encrypted backup" in notices[0]
    # Cleared on read: a second call must not repeat a stale notice.
    with patch.object(backup_mod, "JOURNAL_DB_FILE", live):
        assert backup_mod._post_restore_account_notices() == []


def test_an_instance_that_lost_nothing_gets_no_reestablish_notice(tmp_path):
    """The no-noise half of the same rule."""
    backup_mod._LAST_RESTORE_CONFIG_LOSSES.clear()
    live = tmp_path / "journal.db"
    conn = sqlite3.connect(str(live))
    try:
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO users VALUES (1)")
        conn.commit()
    finally:
        conn.close()
    with patch.object(backup_mod, "JOURNAL_DB_FILE", live):
        assert backup_mod._post_restore_account_notices() == []


# ---------------------------------------------------------------------------
# The settings denylist cannot silently fall behind the settings model
# ---------------------------------------------------------------------------


def test_no_credential_shaped_settings_field_is_left_unredacted():
    """Every credential-shaped settings field is redacted or explicitly excused.

    ``_REDACT_KEYS`` matches key names EXACTLY, which is the safe runtime rule
    (a substring rule would rewrite ``show_stream_urls`` — a bool — to a string
    sentinel). The cost of exact matching is that a newly added
    ``<vendor>_api_key`` silently ships in clear: that is precisely how
    ``emby_api_key`` / ``jellyfin_api_key`` / ``plex_token`` came to be exported
    verbatim. This test is the ratchet that closes the class — it reads the
    live settings model, so a new credential field fails here rather than in a
    drill.
    """
    from config import DispatcharrSettings

    # Names that LOOK credential-shaped but are not credentials. Each entry is a
    # reviewed decision, not a convenience.
    excused = {
        # Booleans and tuning knobs whose names merely contain a marker word.
        "hide_epg_urls",
        "hide_m3u_urls",
        "show_stream_urls",
        "auth_method",
        "user_timezone",
        "user_username_cache_ttl",
        # Addresses, not credentials — the operator needs them to reconnect, and
        # any credential embedded in one is removed by the URL scrub.
        "url",
        "public_base_url",
        "emby_base_url",
        "jellyfin_base_url",
        "plex_base_url",
    }
    markers = ("key", "token", "pass", "secret", "user", "webhook", "auth", "cred")
    unprotected = sorted(
        name
        for name in DispatcharrSettings.model_fields
        if any(marker in name.lower() for marker in markers)
        and name.lower() not in backup_mod._REDACT_KEYS
        and name.lower() not in backup_mod._PROVIDER_IDENTITY_KEYS
        and name not in excused
    )
    assert unprotected == [], (
        "credential-shaped settings fields ship in clear in a standard artifact: %s "
        "— add each to routers.backup._SETTINGS_CREDENTIAL_FIELDS, or to this "
        "test's `excused` set with a reason." % unprotected
    )


# ---------------------------------------------------------------------------
# RESTORE BEHAVIOUR for every newly-redacted field
#
# Widening redaction moves work onto the restore: more fields now arrive as the
# sentinel. The failure this must not produce is the one bead …-6pilh was filed
# for — ECM's own placeholder written into a destination credential column,
# producing an account that LOOKS configured, passes every truthiness probe, and
# fails at the provider. Each newly-redacted field is checked for the same three
# properties the password already had: not written, left visibly unset, and
# named in the operator's re-entry action item.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m3u_restore_leaves_redacted_username_unset_and_reports_it():
    from dbas.importers.m3u_accounts import import_m3u_accounts
    from dbas.restore_contracts import (
        EntityType,
        IdRemapTable,
        RestoreReport,
        RollbackLedger,
    )

    captured = {}

    async def _create(payload):
        captured["payload"] = payload
        return {"id": 901, **payload}

    client = AsyncMock()
    client.get_m3u_accounts = AsyncMock(return_value=[])
    client.get_channel_groups = AsyncMock(return_value=[])
    client.create_m3u_account = AsyncMock(side_effect=_create)

    report = RestoreReport(is_dry_run=False)
    await import_m3u_accounts(
        archive_accounts=[
            {
                "id": 5,
                "name": "Provider XC",
                "account_type": "XC",
                # Everything a STANDARD artifact now carries for this account.
                "server_url": backup_mod.REDACTED,
                "username": backup_mod.REDACTED,
                "password": backup_mod.REDACTED,
                "profiles": [
                    {
                        "id": 1,
                        "custom_properties": {
                            "user_info": {
                                "username": backup_mod.REDACTED,
                                "password": backup_mod.REDACTED,
                                "status": "Active",
                            }
                        },
                    }
                ],
            }
        ],
        client=client,
        selected=True,
        report=report,
        ledger=RollbackLedger(restore_id="gi4zn-test"),
        remap=IdRemapTable(),
    )

    payload = captured["payload"]
    # Absent, not blank and above all not the placeholder.
    for field in ("server_url", "username", "password"):
        assert field not in payload, field
    user_info = payload["profiles"][0]["custom_properties"]["user_info"]
    assert "username" not in user_info
    assert "password" not in user_info
    assert user_info["status"] == "Active"  # non-credential context survives

    detail = report.credential_reentry_details[0]
    assert detail.entity_type == EntityType.M3U_ACCOUNT
    assert detail.label == "Provider XC"
    # The account-level fields are named, so the operator's action item matches
    # what they actually have to re-type.
    assert "username" in detail.fields
    assert "server_url" in detail.fields
    # SUPERSEDED ASSERTION, rewritten rather than deleted (bead ``…-posm1``).
    # This required the nested ``profiles[0].custom_properties.user_info.*``
    # copies to be named too. The REDACTION half of that is unchanged and is
    # still asserted above — the placeholder never reaches the blob. The
    # REPORTING half was wrong: ``user_info`` is Dispatcharr's cache of the
    # provider's ``player_api`` reply, there is no field on any screen to
    # re-enter it into, and the destination rewrites it itself on its next
    # successful refresh. Measured live on 0.29.0, an account whose real
    # credentials HAD been re-entered kept reporting "1 account(s) need
    # credentials re-entered" on the strength of these two paths alone. The
    # importer still LOGS every path it stripped — that is a developer surface,
    # and it is the operator-facing action item that must be actionable.
    assert not [f for f in detail.fields if "user_info" in f]
    # Field NAMES only — never a value.
    assert backup_mod.REDACTED not in report.model_dump_json()


@pytest.mark.asyncio
async def test_epg_restore_leaves_redacted_username_and_url_unset_and_reports_them():
    from dbas.importers.epg_sources import import_epg_sources
    from dbas.restore_contracts import IdRemapTable, RestoreReport, RollbackLedger

    captured = {}

    async def _create(payload):
        captured["payload"] = payload
        return {"id": 801, **payload}

    client = AsyncMock()
    client.get_epg_sources = AsyncMock(return_value=[])
    client.create_epg_source = AsyncMock(side_effect=_create)

    report = RestoreReport(is_dry_run=False)
    await import_epg_sources(
        archive_sources=[
            {
                "id": 3,
                "name": "Userinfo EPG",
                "source_type": "xmltv",
                "url": backup_mod.REDACTED,
                "username": backup_mod.REDACTED,
            }
        ],
        client=client,
        selected=True,
        report=report,
        ledger=RollbackLedger(restore_id="gi4zn-test"),
        remap=IdRemapTable(),
    )

    payload = captured["payload"]
    assert "username" not in payload
    assert "url" not in payload
    assert payload["name"] == "Userinfo EPG"
    fields = report.credential_reentry_details[0].fields
    assert "username" in fields
    assert "url" in fields


@pytest.mark.asyncio
async def test_ecm_settings_restore_keeps_live_values_for_every_new_field(monkeypatch):
    """The newly-redacted settings never overwrite a working local value.

    ``username`` is additionally in ``ecm_settings.CONNECTION_KEYS`` — the live
    Dispatcharr connection is never restored from an archive at all — so it is
    excluded before the sentinel check even runs. The other four reach the
    sentinel branch and are reported for re-entry.
    """
    from config import DispatcharrSettings
    from dbas.importers import ecm_settings as ecm_settings_mod
    from dbas.restore_contracts import RestoreReport

    # Built from the field names rather than written as literals: a line that
    # pairs one of these keys with a quoted value is a KeywordDetector finding,
    # and the inline pragma was deliberately disabled by the former
    # scripts/check_secrets.py
    # (docs/pytest_conventions.md -> "Credential Fixtures in Security Tests").
    live = {name: "live-value-for-" + name for name in _NEWLY_REDACTED_SETTINGS}
    saved: dict = {}
    current = DispatcharrSettings(**live)
    monkeypatch.setattr(ecm_settings_mod, "get_settings", lambda: current)
    monkeypatch.setattr(
        ecm_settings_mod, "save_settings", lambda s: saved.update(s.model_dump())
    )

    archive = {name: backup_mod.REDACTED for name in _NEWLY_REDACTED_SETTINGS}
    archive["theme"] = "light"

    report = RestoreReport(is_dry_run=False)
    await ecm_settings_mod.import_ecm_settings(
        archive_settings=archive,
        selected=True,
        report=report,
        is_dry_run=False,
    )

    assert saved["theme"] == "light"  # the run still applies real values
    for name in _NEWLY_REDACTED_SETTINGS:
        assert saved[name] == live[name], name
    # ``username`` is absent because CONNECTION_KEYS excludes it earlier; the
    # other four are reported so the operator knows what to re-enter.
    assert report.credential_reentry_details[0].fields == [
        "emby_api_key",
        "jellyfin_api_key",
        "plex_token",
        "smtp_user",
    ]


@pytest.mark.asyncio
async def test_legacy_yaml_restore_never_writes_the_sentinel_into_a_username():
    """The legacy ``/restore-yaml`` path strips the sentinel too.

    ``_restore_m3u_accounts`` wrote every archived key straight through, so it
    had been writing ``***REDACTED***`` into the destination PASSWORD ever since
    redaction shipped — the exact 6pilh failure, on a path 6pilh did not reach.
    Redacting the username made the same hole reachable for a second field, so
    the strip lands here as part of this change rather than after it.
    """
    captured = {}

    async def _create(payload):
        captured["payload"] = payload
        return {"id": 1, **payload}

    client = AsyncMock()
    client.get_m3u_accounts = AsyncMock(return_value=[])
    client.delete_m3u_account = AsyncMock(return_value=None)
    client.create_m3u_account = AsyncMock(side_effect=_create)

    with patch.object(backup_mod, "get_client", return_value=client):
        result = await backup_mod._restore_m3u_accounts(
            [
                {
                    "id": 5,
                    "name": "Provider XC",
                    "username": backup_mod.REDACTED,
                    "password": backup_mod.REDACTED,
                }
            ]
        )

    assert "username" not in captured["payload"]
    assert "password" not in captured["payload"]
    assert any("must be re-entered" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# Backwards compatibility, both directions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_artifact_written_before_this_change_still_restores():
    """Detection is by VALUE, so a pre-change artifact is unaffected.

    An artifact produced before this change carries a CLEARTEXT username. It is
    not the sentinel, so nothing strips it and the account restores exactly as
    it did — no compatibility gate, no schema-version bump.
    """
    from dbas.importers.m3u_accounts import import_m3u_accounts
    from dbas.restore_contracts import IdRemapTable, RestoreReport, RollbackLedger

    captured = {}

    async def _create(payload):
        captured["payload"] = payload
        return {"id": 902, **payload}

    client = AsyncMock()
    client.get_m3u_accounts = AsyncMock(return_value=[])
    client.get_channel_groups = AsyncMock(return_value=[])
    client.create_m3u_account = AsyncMock(side_effect=_create)

    report = RestoreReport(is_dry_run=False)
    await import_m3u_accounts(
        archive_accounts=[
            {
                "id": 5,
                "name": "Provider XC",
                "server_url": "https://provider.example.test",
                "username": "legacy-cleartext-user",
                "password": backup_mod.REDACTED,  # the pre-change artifact shape
            }
        ],
        client=client,
        selected=True,
        report=report,
        ledger=RollbackLedger(restore_id="gi4zn-test"),
        remap=IdRemapTable(),
    )

    assert captured["payload"]["username"] == "legacy-cleartext-user"
    assert captured["payload"]["server_url"] == "https://provider.example.test"
    assert report.credential_reentry_details[0].fields == ["password"]


# ---------------------------------------------------------------------------
# RESTORE BEHAVIOUR for the purged account tables
#
# Widening redaction to journal.db's ``users`` table moves an AVAILABILITY
# question onto the restore, and it is the one the PO called out: a restored
# instance nobody can log into is a worse outcome than the leak. Two destination
# states, and the answer differs because the right answer differs:
#
#   * destination HAS accounts -> its own accounts survive the restore, so an
#     admin restoring a backup is not logged out of their own instance;
#   * destination has NONE (fresh container, DR rebuild) -> nothing is
#     re-asserted, ``users`` stays empty, and ECM's shipped first-run setup takes
#     over. That is what the operator re-establishes: their ECM account.
#
# These run against REAL SQLite files rather than mocks, because the defect being
# guarded is about what is in a database file.
# ---------------------------------------------------------------------------


def _auth_db(path, users=(), alert_configs=()):
    """A journal.db with the auth schema and exactly the given rows."""
    _create_auth_schema(path)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("DELETE FROM users")
        conn.execute("DELETE FROM user_sessions")
        conn.execute("DELETE FROM user_identities")
        conn.execute("DELETE FROM password_reset_tokens")
        for uid, username, pw_hash in users:
            conn.execute(
                "INSERT INTO users (id, username, password_hash, auth_provider, "
                "is_active, is_admin, created_at, updated_at) "
                "VALUES (?,?,?,?,1,1,?,?)",
                (uid, username, pw_hash, "local", "2026-08-17", "2026-08-17"),
            )
            conn.execute(
                "INSERT INTO user_identities (id, user_id, provider, identifier, "
                "linked_at) VALUES (?,?,?,?,?)",
                (uid, uid, "local", username, "2026-08-17"),
            )
        if alert_configs:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS alert_methods "
                "(id INTEGER PRIMARY KEY, config TEXT)"
            )
            for rid, cfg in alert_configs:
                conn.execute(
                    "INSERT INTO alert_methods (id, config) VALUES (?,?)", (rid, cfg)
                )
        conn.commit()
    finally:
        conn.close()
    return path


def test_a_restore_keeps_the_destination_instances_own_accounts(tmp_path, monkeypatch):
    """The admin driving the restore stays able to log into their own instance."""
    live = _auth_db(tmp_path / "journal.db", users=[(1, "owner", "$2b$12$OWNERHASH")])
    monkeypatch.setattr(backup_mod, "JOURNAL_DB_FILE", live)

    prior = backup_mod._capture_existing_auth_rows()
    assert [row[1] for row in prior["users"][1]] == ["owner"]

    # The restore lands a standard artifact's journal.db: auth tables EMPTY.
    _auth_db(live, users=[])
    backup_mod._reassert_auth_rows_after_restore(prior)

    conn = sqlite3.connect(str(live))
    try:
        rows = conn.execute("SELECT username, password_hash FROM users").fetchall()
        identities = conn.execute("SELECT identifier FROM user_identities").fetchall()
    finally:
        conn.close()
    assert rows == [("owner", "$2b$12$OWNERHASH")]
    assert identities == [("owner",)]
    assert backup_mod._post_restore_account_notices() == []


def test_a_restore_onto_an_owned_instance_discards_the_artifacts_users(
    tmp_path, monkeypatch
):
    """A legacy artifact's ``users`` cannot silently replace the live ones.

    Before this change the destination's users table was overwritten wholesale by
    whatever the ZIP carried, so an old backup resurrected deleted accounts and
    their password hashes. The owner is now authoritative on an owned instance.
    """
    live = _auth_db(tmp_path / "journal.db", users=[(1, "owner", "$2b$12$OWNERHASH")])
    monkeypatch.setattr(backup_mod, "JOURNAL_DB_FILE", live)
    prior = backup_mod._capture_existing_auth_rows()

    # A pre-change ZIP: real users, restored over the live database.
    _auth_db(live, users=[(1, "stale-admin", "$2b$12$STALEHASH")])
    backup_mod._reassert_auth_rows_after_restore(prior)

    conn = sqlite3.connect(str(live))
    try:
        rows = conn.execute("SELECT username FROM users").fetchall()
    finally:
        conn.close()
    assert rows == [("owner",)]


def test_a_legacy_artifact_still_installs_its_users_onto_an_empty_instance(
    tmp_path, monkeypatch
):
    """Backwards compatibility, and it is the reason the re-assert is conditional.

    An artifact written before this change carries real ``users`` rows, and
    restoring one onto a genuinely empty instance is a shipped disaster-recovery
    path. Nothing here is re-asserted (there is nothing to re-assert), so that
    path is unchanged.
    """
    live = _auth_db(tmp_path / "journal.db", users=[])
    monkeypatch.setattr(backup_mod, "JOURNAL_DB_FILE", live)
    prior = backup_mod._capture_existing_auth_rows()
    assert prior.get("users") is None

    _auth_db(live, users=[(1, "legacy-admin", "$2b$12$LEGACYHASH")])
    backup_mod._reassert_auth_rows_after_restore(prior)

    conn = sqlite3.connect(str(live))
    try:
        rows = conn.execute("SELECT username, password_hash FROM users").fetchall()
    finally:
        conn.close()
    assert rows == [("legacy-admin", "$2b$12$LEGACYHASH")]
    assert backup_mod._post_restore_account_notices() == []


def test_an_account_less_restore_tells_the_operator_to_run_first_run_setup(
    tmp_path, monkeypatch
):
    """``restored_files`` reports what landed; it cannot report what could not.

    Read off the LIVE post-restore database rather than predicted from the
    artifact, so it cannot claim a lockout that did not happen.
    """
    live = _auth_db(tmp_path / "journal.db", users=[])
    monkeypatch.setattr(backup_mod, "JOURNAL_DB_FILE", live)

    notices = backup_mod._post_restore_account_notices()
    assert notices == [backup_mod.FIRST_RUN_SETUP_NOTICE]
    assert "first-run setup" in notices[0]
    assert "encrypted backup" in notices[0]


def test_whole_blob_sentinel_alert_config_is_repaired_from_the_destination(
    tmp_path, monkeypatch
):
    """The fail-closed producer change must not DESTROY a working alert method.

    The producer replaces an unparseable ``config`` with the sentinel as a whole
    blob; the restore treats that exactly as it treats a per-key sentinel and
    reinstates the destination's own config.
    """
    live = _auth_db(
        tmp_path / "journal.db",
        alert_configs=[(1, json.dumps({"host": "smtp.example.test", "password": DESTINATION_SMTP_SECRET}))],
    )
    monkeypatch.setattr(backup_mod, "JOURNAL_DB_FILE", live)
    prior = backup_mod._capture_existing_alert_method_configs()

    conn = sqlite3.connect(str(live))
    try:
        conn.execute(
            "UPDATE alert_methods SET config=? WHERE id=1", (backup_mod.REDACTED,)
        )
        conn.commit()
    finally:
        conn.close()

    backup_mod._merge_alert_method_creds_after_restore(prior)

    conn = sqlite3.connect(str(live))
    try:
        raw = conn.execute("SELECT config FROM alert_methods WHERE id=1").fetchone()[0]
    finally:
        conn.close()
    assert json.loads(raw) == {"host": "smtp.example.test", "password": DESTINATION_SMTP_SECRET}


def test_ntfy_access_token_is_preserved_across_redacted_restore(tmp_path, monkeypatch):
    token = "<destination-ntfy-token>"
    config = {"server_url": "https://ntfy.example.test", "topic": "ecm", "access_token": token}
    live = _auth_db(tmp_path / "journal.db", alert_configs=[(1, json.dumps(config))])
    monkeypatch.setattr(backup_mod, "JOURNAL_DB_FILE", live)
    prior = backup_mod._capture_existing_alert_method_configs()

    conn = sqlite3.connect(str(live))
    try:
        redacted = {**config, "access_token": backup_mod.REDACTED}
        conn.execute("UPDATE alert_methods SET config=? WHERE id=1", (json.dumps(redacted),))
        conn.commit()
    finally:
        conn.close()

    backup_mod._merge_alert_method_creds_after_restore(prior)

    conn = sqlite3.connect(str(live))
    try:
        restored = json.loads(conn.execute("SELECT config FROM alert_methods WHERE id=1").fetchone()[0])
    finally:
        conn.close()
    assert restored == config


@pytest.mark.asyncio
async def test_a_restored_standard_artifact_leaves_first_run_setup_available(
    standard_artifact, tmp_path
):
    """Invariant 3, proved against the REAL auth route and the REAL artifact.

    Not "the users table is empty" — that is the mechanism. The property is that
    somebody can still log into the restored instance, and the shipped way to do
    that is ``GET /api/auth/setup-required`` answering ``required: true``, which
    is what ``ProtectedRoute.tsx`` renders the setup wizard on.

    THE ``create_all`` BELOW IS NOT A FUDGE TO MAKE THIS PASS. Round 3 drops the
    auth tables instead of emptying them, so the artifact genuinely has no
    ``users`` table, and this test had to start modelling the restore SEQUENCE
    rather than just the artifact: ``_restore_from_zip`` writes the bytes and
    then calls ``init_db()``, whose ``Base.metadata.create_all`` recreates every
    model-declared table before anything can query one
    (``database.py`` -> ``init_db``; the ordering is
    ``write_bytes`` -> ``init_db`` -> the endpoint's notices read). Omitting it
    here would have been the test modelling a sequence the app does not run.

    Both halves are asserted, so this cannot pass on a database that shipped the
    table after all: absent in the ARTIFACT, present-and-empty after the
    init_db-equivalent, and the real auth route agreeing.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from auth.routes import check_setup_required
    from models import Base

    restored = tmp_path / "journal.db"
    with zipfile.ZipFile(standard_artifact.zip_path) as zf:
        restored.write_bytes(zf.read("journal.db"))

    # The artifact itself carries no account table at all.
    probe = sqlite3.connect(str(restored))
    try:
        assert probe.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone() is None
    finally:
        probe.close()

    engine = create_engine("sqlite:///%s" % restored)
    Base.metadata.create_all(engine)  # what init_db() does on every restore
    session = sessionmaker(bind=engine)()
    try:
        result = await check_setup_required(session=session)
    finally:
        session.close()
        engine.dispose()

    assert result.required is True


# ---------------------------------------------------------------------------
# THE URL SCAN IS LINEAR IN THE LENGTH OF THE VALUE
# ---------------------------------------------------------------------------
#
# CodeQL alert #1879 (``py/polynomial-redos``, HIGH) against the scheme-matching
# regex this bead introduced at ``routers/backup.py``:
#
#     _URL_IN_TEXT_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s\"'<>]+")
#
# The scheme prefix is an UNBOUNDED repetition over a character class that does
# not contain ``:``, so on a long run of scheme-legal characters that is not
# followed by ``://`` the engine re-scans the remainder of the run from every
# starting position inside it — quadratic in the length of the run. Measured on
# the pattern as committed: 1k chars 0.63 ms, 8k 42 ms, 32k 639 ms, 128k 10.2 s,
# a clean x4 per doubling.
#
# The input is unbounded and operator-controlled. ``_scrub_credential_urls``
# runs over every string cell of every table
# ``routers.backup._STANDARD_ARTIFACT_TABLES`` permits, and two of those carry
# free text with no length bound anywhere in the stack:
# ``ffmpeg_profiles.config`` and ``dummy_epg_profiles.description_template`` are
# both ``Column(Text)`` (``models.py``), their request models declare
# ``Optional[str] = None`` with no ``max_length`` (``routers/dummy_epg.py``),
# and SQLite does not enforce ``String(n)`` widths at all. So ADR-005's
# false-positive dismissal path (b) is unavailable — there is no sanitizer to
# reference — and (c) does not apply because this is production code. The
# resolution is (a) remediation.
#
# The scheme is now recovered by walking BACKWARDS from the ``://`` separator
# (``routers.backup._find_urls_in_text``) instead of being matched forward, so
# each run is visited once and the scan is linear. Both halves of that are
# pinned below, because either one alone is satisfiable by a broken fix: the
# shape corpus pins that every URL the scrub caught before it still catches,
# and the cost tests pin that it got cheaper without doing so.

_URL_SCAN_IDENT = "urlident" + "QQQAAA"
_URL_SCAN_OPAQUE = "urlopaque" + "QQQBBB"


def _url_scan_shapes() -> dict:
    """Every URL shape the scrub caught before the ReDoS fix, and its result.

    Captured by running each value through ``_scrub_credential_urls`` as it
    stood at commit ``0b3aeafb`` (the last commit carrying the quadratic
    pattern) and recording the output verbatim. Pinning the recorded output —
    rather than re-deriving it from the pattern — is what makes this an
    equivalence test: the fix is only correct if every one of these still holds.

    ``None`` means "carries no URL credential, leave the value byte-identical".
    """
    ident, opaque = _URL_SCAN_IDENT, _URL_SCAN_OPAQUE
    return {
        # --- The whole value IS a credential-bearing URL -> whole sentinel ---
        "whole value, get.php query creds": (
            f"http://prov.example.test/get.php?username={ident}&password={opaque}",
            backup_mod.REDACTED,
        ),
        "whole value, xmltv.php query creds": (
            f"http://prov.example.test/xmltv.php?username={ident}&password={opaque}",
            backup_mod.REDACTED,
        ),
        "whole value, RFC 3986 userinfo": (
            f"http://{ident}:{opaque}@epg.example.test/guide.xml",
            backup_mod.REDACTED,
        ),
        "whole value, short query aliases": (
            f"http://h.example.test/a?user={ident}&pass={opaque}",
            backup_mod.REDACTED,
        ),
        "whole value, apikey query": (
            f"https://img.example.test/t.png?apikey={opaque}",
            backup_mod.REDACTED,
        ),
        "whole value, surrounded by whitespace": (
            f"  http://{ident}:{opaque}@h.example.test/x  ",
            backup_mod.REDACTED,
        ),
        # --- Schemes other than http/https, and schemes with +/-/. in them ---
        "uppercase scheme": (
            f"HTTP://{ident}:{opaque}@h.example.test/x",
            backup_mod.REDACTED,
        ),
        "compound scheme svn+ssh": (
            f"svn+ssh://{ident}:{opaque}@h.example.test/x",
            backup_mod.REDACTED,
        ),
        "rtsp scheme": (
            f"rtsp://{ident}:{opaque}@h.example.test/live",
            backup_mod.REDACTED,
        ),
        "rtmp scheme": (
            f"rtmp://{ident}:{opaque}@h.example.test/live",
            backup_mod.REDACTED,
        ),
        "ftp scheme": (
            f"ftp://{ident}:{opaque}@h.example.test/f",
            backup_mod.REDACTED,
        ),
        # --- The scheme run does not start on a letter -------------------
        #
        # The old pattern's leftmost match began at the first ASCII LETTER of
        # the run, not at the run's first character, so these were caught. A
        # naive ReDoS fix — forbidding a match start whose previous character is
        # scheme-legal — silently stops catching every one of them, which is why
        # they are pinned individually.
        "digit-prefixed scheme": (
            f"1https://{ident}:{opaque}@h.example.test/x",
            "1" + backup_mod.REDACTED,
        ),
        "dot-prefixed scheme": (
            f".https://{ident}:{opaque}@h.example.test/x",
            "." + backup_mod.REDACTED,
        ),
        "dash-prefixed scheme": (
            f"-https://{ident}:{opaque}@h.example.test/x",
            "-" + backup_mod.REDACTED,
        ),
        "plus-prefixed scheme": (
            f"+https://{ident}:{opaque}@h.example.test/x",
            "+" + backup_mod.REDACTED,
        ),
        "dash inside a run that starts on a letter": (
            f"for-https://{ident}:{opaque}@h.example.test/x",
            backup_mod.REDACTED,
        ),
        # --- A scheme-less ``://`` in front of the real URL ---------------
        #
        # The credential-bearing URL sits INSIDE the span a greedy body match
        # from the FIRST ``://`` would swallow. A scan that measures the body
        # from every separator and then skips past it never sees the real URL
        # and silently ships the credential — the first attempt at this
        # remediation did exactly that and passed every other shape here.
        "letterless separator then a real url": (
            f"+://https://{ident}:{opaque}@h.example.test/x",
            "+://" + backup_mod.REDACTED,
        ),
        "bare separator then a real url": (
            f"://https://{ident}:{opaque}@h.example.test/x",
            "://" + backup_mod.REDACTED,
        ),
        "letterless separator, real url, inside a message": (
            f"saw +://https://{ident}:{opaque}@h.example.test/x once",
            f"saw +://{backup_mod.REDACTED} once",
        ),
        # --- The value CONTAINS a URL -> only the URL substring goes ------
        "embedded in a status message": (
            f"fetch failed for http://{ident}:{opaque}@h.example.test/x after 3 tries",
            f"fetch failed for {backup_mod.REDACTED} after 3 tries",
        ),
        "embedded, one dirty and one clean": (
            f"tried https://clean.example.test/a then http://{ident}:{opaque}@h.example.test/b",
            f"tried https://clean.example.test/a then {backup_mod.REDACTED}",
        ),
        "embedded in double quotes": (
            f'upstream said "http://{ident}:{opaque}@h.example.test/x" was bad',
            f'upstream said "{backup_mod.REDACTED}" was bad',
        ),
        "embedded in single quotes": (
            f"upstream said 'http://{ident}:{opaque}@h.example.test/x' was bad",
            f"upstream said '{backup_mod.REDACTED}' was bad",
        ),
        "embedded in angle brackets": (
            f"<http://{ident}:{opaque}@h.example.test/x>",
            f"<{backup_mod.REDACTED}>",
        ),
        "two dirty urls in one value": (
            f"a http://{ident}:{opaque}@h1.example.test/x "
            f"b http://{ident}:{opaque}@h2.example.test/y",
            f"a {backup_mod.REDACTED} b {backup_mod.REDACTED}",
        ),
        "dirty url, newline, clean url": (
            f"http://{ident}:{opaque}@h1.example.test/x\nhttps://clean.example.test/y",
            f"{backup_mod.REDACTED}\nhttps://clean.example.test/y",
        ),
        "trailing punctuation is part of the match": (
            f"see http://{ident}:{opaque}@h.example.test/x.",
            f"see {backup_mod.REDACTED}",
        ),
        "a second :// inside the path": (
            f"http://{ident}:{opaque}@h.example.test/a://b",
            backup_mod.REDACTED,
        ),
        # --- Clean values must stay BYTE-IDENTICAL (None) -----------------
        "clean url, whole value": ("https://cdn.epg.example.test/us.xml.gz", None),
        "clean url, blank query value": (
            "http://h.example.test/get.php?username=",
            None,
        ),
        "clean Dispatcharr internal url": ("http://dispatcharr:9191", None),
        "clean Plex url with a port": ("http://plex.example.test:32400", None),
        "no url at all": ("just a plain message", None),
        ":// with no scheme letter before it": ("://nope.example.test/x", None),
        "scheme separator with nothing after it": ("http://", None),
    }


@pytest.mark.parametrize("shape", sorted(_url_scan_shapes()))
def test_every_url_shape_the_scrub_caught_before_is_still_caught(shape):
    """The ReDoS remediation changes cost, not coverage.

    Invariant, not reproduction: the scan must return exactly what it returned
    before for EVERY shape — the sentinel for a credential-bearing URL, the
    original bytes for a clean one. A cheaper scan that stops recognising a
    credential-bearing URL trades a performance finding for a data leak, which
    is the worse bargain.
    """
    value, expected = _url_scan_shapes()[shape]
    assert backup_mod._scrub_credential_urls(value) == expected


def test_the_url_scan_does_not_degrade_on_a_long_scheme_legal_run():
    """A long run of scheme-legal characters costs linear time, not quadratic.

    RED WITHOUT THE FIX: this is the CodeQL ``py/polynomial-redos`` alert
    reproduced through the production entry point rather than against the bare
    pattern. The value below is a 200,000-character run of scheme-legal
    characters that is never followed by ``://``, plus one real URL so the
    ``"://" not in value`` early-out does not short-circuit the scan. Against
    the quadratic pattern this takes ~25 s; against the backward-walk scan it
    takes well under a millisecond.

    An operator reaches this with one ``dummy_epg_profiles.description_template``
    or ``ffmpeg_profiles.config`` value — both ``Column(Text)``, neither bounded
    by a request model — and the cost is paid on every standard backup from then
    on, because the scrub visits every string cell of every permitted table.

    The budget is deliberately loose (2 s against a ~25 s break and a ~0.05 ms
    pass) so it cannot flake on a loaded runner, while still being unable to
    pass while the quadratic pattern is in place.
    """
    import time as _time

    payload = "a" * 200_000 + " https://clean.example.test/x"

    started = _time.perf_counter()
    result = backup_mod._scrub_credential_urls(payload)
    elapsed = _time.perf_counter() - started

    # The value carries no credential, so it must come back untouched...
    assert result is None
    # ...and it must have got there without a quadratic scan.
    assert elapsed < 2.0, (
        "the URL scan took %.2f s on a 200k-character scheme-legal run; the "
        "scheme prefix is being matched forward from every position in the run "
        "again (CodeQL py/polynomial-redos)" % elapsed
    )


# Input families that a scheme-scanning bug degrades to quadratic on. Each is
# built from a length so the SAME family can be measured at two sizes.
#
# The first family is CodeQL alert #1879 itself. The rest are here because the
# first one alone is NOT a sufficient guard — plausible fixes exist that pass it
# and are still quadratic on another shape. Each family below was mutation-
# tested: a variant of the fix was written, and the family that catches it was
# confirmed to trip (>= x8 growth for a x4 input, against ~x4 for the shipped
# scan). Measured 2026-08-17 at 50k -> 200k:
#
#   family                        | variant it catches         | measured
#   ------------------------------|----------------------------|----------------
#   scheme-legal run              | the old forward-matching    | 1.6s -> 24.7s
#                                 | pattern (alert #1879)       | x15.9  TRIPS
#   alternating letters/digits    | a lookbehind forbidding a   | 0.8s -> 12.5s
#                                 | preceding LETTER only       | x16.0  TRIPS
#   separators with no scheme     | measuring the URL body      | 1.5s -> 23.7s
#                                 | before checking the scheme  | x15.9  TRIPS
#   letterless-prefixed separators| the same, independently     | 1.1s -> 17.8s
#                                 |                             | x15.6  TRIPS
#
# Two more variants are quadratic-clean but WRONG, and are caught by
# ``test_every_url_shape_the_scrub_caught_before_is_still_caught`` instead: a
# lookbehind forbidding any preceding scheme-legal character loses the
# ``digit-prefixed scheme`` shape, and a ``finditer`` scan that skips past each
# body loses the ``letterless separator then a real url`` shapes.
_URL_SCAN_COST_FAMILIES = {
    "scheme-legal run": lambda n: "a" * n + " https://clean.example.test/x",
    "alternating letters and digits": (
        lambda n: "a1" * (n // 2) + " https://clean.example.test/x"
    ),
    "separators with no scheme": lambda n: "://" * (n // 3),
    "separators with a scheme-legal but letterless prefix": (
        lambda n: "+://" * (n // 4)
    ),
}


@pytest.mark.parametrize("family", sorted(_URL_SCAN_COST_FAMILIES))
def test_the_url_scan_cost_grows_linearly_with_the_input_length(family):
    """Quadrupling the input must not multiply the cost by ~16.

    The companion to the budget test above, stated as the complexity property
    rather than as a wall-clock number: the quadratic pattern grew by a clean x4
    per doubling (measured 1k/2k/4k/8k/16k/32k/64k/128k), so a x4 input growth
    cost x16. The threshold is x8 — above the linear x4 plus interpreter noise,
    below the quadratic x16.
    """
    import time as _time

    build = _URL_SCAN_COST_FAMILIES[family]

    def cost(length: int) -> float:
        payload = build(length)
        samples = []
        for _ in range(3):
            started = _time.perf_counter()
            backup_mod._scrub_credential_urls(payload)
            samples.append(_time.perf_counter() - started)
        # Best of three: the floor is the signal, scheduler noise is not.
        return min(samples)

    small = cost(50_000)
    large = cost(200_000)

    # The linear scan finishes these in tens of MICROseconds, where a x8 ratio
    # is scheduler noise rather than signal, so a negligible absolute cost also
    # passes. Every quadratic variant measured on these families took >1 s at
    # 200k — three orders of magnitude above the floor — so the floor cannot
    # rescue a broken scan.
    assert large < max(small * 8, 0.05), (
        "quadrupling the %r input multiplied the scan cost by %.1fx "
        "(%.4f s -> %.4f s); a linear scan multiplies it by ~4x and a "
        "quadratic one by ~16x"
        % (family, large / small if small else 0, small, large)
    )
