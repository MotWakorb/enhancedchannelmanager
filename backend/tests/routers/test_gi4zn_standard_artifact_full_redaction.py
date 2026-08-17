"""The STANDARD DBAS artifact carries no third-party identity or credential.

Bead ``enhancedchannelmanager-gi4zn``. PO decision 2026-08-05: the standard
(non-encrypted, default) backup is FULLY redacted.

THE PROPERTY THESE TESTS PIN
----------------------------

    A standard DBAS artifact contains no value that identifies or authenticates
    against a THIRD-PARTY service.

Not "the username field on an M3U account is redacted" — that is one example of
the property. The drill (``~/ecm/backup-restore-runs/2026-08-05-run3``, finding
F4) found the XC username in clear beside a correctly-redacted password, and the
project's recurring failure mode is fixing the demonstrated case and leaving the
class open (see ``CLAUDE.md`` -> "State review acceptance criteria as invariants,
not reproductions"). So the assertions below are written against the whole
artifact: every seeded identity/credential sentinel is scanned for across EVERY
decompressed member, including ``journal.db``, rather than against the two YAML
keys the drill happened to read.

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
import json
import sqlite3
import zipfile
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
# disabled by ``scripts/check_secrets.py`` on purpose).
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


def _seed_journal_db(path):
    """A real journal.db whose alert_methods.config carries the SMTP relay
    IDENTITY beside its secret, and a Telegram chat id beside its bot token."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE alert_methods (id INTEGER PRIMARY KEY, config TEXT)")
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
    for value in ALL_THIRD_PARTY_VALUES:
        assert value.encode() not in members, value


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
    # The account-level pair AND the nested second copy are both named, so the
    # operator's action item matches what the artifact actually dropped.
    assert "username" in detail.fields
    assert "server_url" in detail.fields
    assert "profiles[0].custom_properties.user_info.username" in detail.fields
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
    # and the inline pragma is disabled by scripts/check_secrets.py on purpose
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
