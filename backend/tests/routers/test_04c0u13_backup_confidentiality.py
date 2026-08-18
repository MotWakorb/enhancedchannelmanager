"""Backup confidentiality policy (bead enhancedchannelmanager-04c0u.13).

The first group of tests pins the POLICY MECHANICS: what a plaintext legacy ZIP
may contain, and that every locally persisted artifact is owner-only.

The second group is the CONTENT PROOF the acceptance criteria ask for. It does
not read the producer's intent; it generates a real artifact through the real
producer against seeded identity, TLS and provider-credential state and then
enumerates what is actually inside the bytes on disk. Those assertions are the
executable form of the confidentiality table in
``docs/user_guide/backup-restore/backup-overview.md`` — if the artifact ever
starts carrying account state, TLS private keys or live credentials again, the
doc becomes false and these tests go red together.
"""
import io
import json
import sqlite3
import stat
import zipfile
from unittest.mock import MagicMock, patch

import pytest
from routers import backup as backup_mod
from tasks import yaml_backup
from tests.routers import test_0i2vt_backup_artifact as artifact_harness

# Credential-shaped fixtures, built as angle-bracket placeholders so the
# secrets ratchet never sees a scan candidate. See
# docs/pytest_conventions.md -> "Credential Fixtures in Security Tests".
FAKE_PASSWORD_HASH = "<synthetic-ecm-password-hash-ZZZ111>"
FAKE_SESSION_TOKEN_HASH = "<synthetic-refresh-session-hash-ZZZ222>"
FAKE_RESET_TOKEN_HASH = "<synthetic-password-reset-hash-ZZZ333>"
FAKE_TLS_PRIVATE_KEY = "<synthetic-tls-private-key-ZZZ444>"
FAKE_PROVIDER_URL = (
    "https://provider.example/live/"
    "<synthetic-provider-user>/<synthetic-provider-pass>/1.ts"
)
FAKE_STORAGE_SECRET = "<synthetic-cloud-storage-secret-ZZZ555>"

# Everything the confidentiality policy says a plaintext artifact must not
# carry, in one tuple so a byte scan cannot silently miss one.
FORBIDDEN_IN_PLAINTEXT_ARTIFACT = (
    FAKE_PASSWORD_HASH,
    FAKE_SESSION_TOKEN_HASH,
    FAKE_RESET_TOKEN_HASH,
    FAKE_TLS_PRIVATE_KEY,
    FAKE_PROVIDER_URL,
    FAKE_STORAGE_SECRET,
)

# Tables whose presence in a shipped journal.db would mean ECM account state,
# session state or a credential store rode along.
IDENTITY_TABLES = ("users", "refresh_sessions", "password_reset_tokens")


def _empty_db(path):
    sqlite3.connect(path).close()


def _seed_identity_journal(path):
    """Write a real journal.db carrying ECM account state, session/reset hashes,
    a credential store row, and one ALLOWLISTED table so the artifact is not
    trivially empty."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT)"
        )
        conn.execute(
            "INSERT INTO users VALUES (1, 'admin', ?)", (FAKE_PASSWORD_HASH,)
        )
        conn.execute(
            "CREATE TABLE refresh_sessions (id INTEGER PRIMARY KEY, token_hash TEXT)"
        )
        conn.execute(
            "INSERT INTO refresh_sessions VALUES (1, ?)", (FAKE_SESSION_TOKEN_HASH,)
        )
        conn.execute(
            "CREATE TABLE password_reset_tokens (id INTEGER PRIMARY KEY, token_hash TEXT)"
        )
        conn.execute(
            "INSERT INTO password_reset_tokens VALUES (1, ?)", (FAKE_RESET_TOKEN_HASH,)
        )
        conn.execute(
            "CREATE TABLE cloud_storage_targets (id INTEGER PRIMARY KEY, secret TEXT)"
        )
        conn.execute(
            "INSERT INTO cloud_storage_targets VALUES (1, ?)", (FAKE_STORAGE_SECRET,)
        )
        # Allowlisted table whose config JSON embeds a provider URL with
        # credentials in the path — the value-level rules must rewrite it.
        conn.execute("CREATE TABLE alert_methods (id INTEGER PRIMARY KEY, config TEXT)")
        conn.execute(
            "INSERT INTO alert_methods VALUES (1, ?)",
            (json.dumps({"host": "smtp.example.com", "url": FAKE_PROVIDER_URL}),),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_config_dir(root):
    """Lay out a /config tree carrying TLS private-key material and an uploaded
    playlist whose stream URL embeds provider credentials."""
    (root / "tls").mkdir()
    (root / "tls" / "server.key").write_text(FAKE_TLS_PRIVATE_KEY)
    (root / "m3u_uploads").mkdir()
    (root / "m3u_uploads" / "provider.m3u").write_text(FAKE_PROVIDER_URL)
    (root / "uploads" / "logos").mkdir(parents=True)
    (root / "uploads" / "logos" / "logo.png").write_bytes(b"logo")


def _archive_bytes(archive) -> bytes:
    if hasattr(archive, "getvalue"):
        return archive.getvalue()
    return archive.read_bytes()


def _tables_in_member(zf, member, tmp_path) -> set[str]:
    """Return the table names of a SQLite member, extracted to disk first so
    sqlite3 reads it exactly as a restore would."""
    extracted = tmp_path / "extracted-journal.db"
    extracted.write_bytes(zf.read(member))
    conn = sqlite3.connect(str(extracted))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


def _build_legacy_zip(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    journal = tmp_path / "journal.db"
    _seed_identity_journal(journal)
    _seed_config_dir(tmp_path)
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = MagicMock()

    with (
        patch.object(backup_mod, "CONFIG_DIR", tmp_path),
        patch.object(backup_mod, "CONFIG_FILE", settings),
        patch.object(backup_mod, "JOURNAL_DB_FILE", journal),
        patch.object(backup_mod, "get_engine", return_value=engine),
        patch.object(
            backup_mod,
            "get_settings",
            return_value=artifact_harness._mock_settings_with_secrets(),
        ),
    ):
        return backup_mod._create_backup_zip()


# --- policy mechanics ------------------------------------------------------

def test_plain_legacy_zip_omits_tls_and_uploaded_playlists(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    journal = tmp_path / "journal.db"
    _empty_db(journal)
    _seed_config_dir(tmp_path)
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = MagicMock()

    with (
        patch.object(backup_mod, "CONFIG_DIR", tmp_path),
        patch.object(backup_mod, "CONFIG_FILE", settings),
        patch.object(backup_mod, "JOURNAL_DB_FILE", journal),
        patch.object(backup_mod, "get_engine", return_value=engine),
    ):
        artifact = backup_mod._create_backup_zip()

    with zipfile.ZipFile(artifact) as zf:
        names = zf.namelist()
    assert "uploads/logos/logo.png" in names
    assert not any(name.startswith("tls/") for name in names)
    assert not any(name.startswith("m3u_uploads/") for name in names)


def test_legacy_restore_still_reinstates_material_from_older_artifacts():
    """Producing a plaintext copy of TLS/playlist material is the confidentiality
    question; restoring an operator's own older artifact is not. Narrowing
    BACKUP_DIRS must not silently drop those trees on the way back in."""
    assert backup_mod.BACKUP_DIRS == ["uploads/logos"]
    assert set(backup_mod.BACKUP_DIRS) <= set(backup_mod.LEGACY_RESTORE_DIRS)
    assert "tls" in backup_mod.LEGACY_RESTORE_DIRS
    assert "m3u_uploads" in backup_mod.LEGACY_RESTORE_DIRS


@pytest.mark.asyncio
async def test_saved_plain_backup_is_owner_only(tmp_path):
    payload = io.BytesIO(b"safe redacted archive")
    with (
        patch.object(backup_mod, "BACKUPS_DIR", tmp_path),
        patch.object(backup_mod, "_create_backup_zip", return_value=payload),
        patch.object(
            backup_mod,
            "_get_backup_filename",
            return_value="ecm-backup-2026-08-18_010203.zip",
        ),
    ):
        result = await backup_mod.save_backup()

    saved = tmp_path / result["filename"]
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_scheduled_yaml_backup_is_owner_only(tmp_path):
    task = yaml_backup.YamlBackupTask()
    with (
        patch.object(yaml_backup, "BACKUPS_DIR", tmp_path),
        patch.object(backup_mod, "build_yaml_export", return_value="settings: {}\n"),
    ):
        result = await task.execute()

    assert result.success
    saved = next(tmp_path.glob("ecm-backup-*.yaml"))
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600


# --- generated-artifact content proof --------------------------------------

def test_generated_legacy_zip_content_matches_documented_policy(tmp_path):
    """Enumerate a REAL legacy ZIP built from seeded secret-bearing state."""
    archive = _build_legacy_zip(tmp_path)
    raw = _archive_bytes(archive)

    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        tables = _tables_in_member(zf, "journal.db", tmp_path)

    # Structure: logos and the scrubbed database ship; the two credential- and
    # key-bearing trees do not.
    assert "journal.db" in names
    assert "settings.json" in names
    assert "uploads/logos/logo.png" in names
    assert not any(name.startswith("tls/") for name in names), names
    assert not any(name.startswith("m3u_uploads/") for name in names), names

    # Account state, session state and the credential store are gone by
    # construction (allowlist), not merely emptied.
    for table in IDENTITY_TABLES + ("cloud_storage_targets",):
        assert table not in tables, tables

    # Byte scan across the WHOLE archive, not just the members we expected to
    # be risky.
    for sentinel in FORBIDDEN_IN_PLAINTEXT_ARTIFACT:
        assert sentinel.encode() not in raw, sentinel
    for sentinel in artifact_harness.ALL_SECRET_SENTINELS:
        assert sentinel.encode() not in raw, sentinel


def test_generated_dbas_artifact_content_matches_documented_policy(tmp_path):
    """Enumerate a REAL DBAS artifact built from seeded secret-bearing state.

    ``_patched_build`` is the production builder driven against a temp
    ``/config``; only its journal seed is swapped for one that carries ECM
    accounts, session/reset hashes and a credential store.
    """
    with patch.object(artifact_harness, "_seed_journal_db", _seed_identity_journal):
        art = artifact_harness._patched_build(tmp_path, with_logos=True)

    raw = art.zip_path.read_bytes()
    with zipfile.ZipFile(art.zip_path) as zf:
        names = zf.namelist()
        tables = _tables_in_member(zf, "journal.db", tmp_path)

    assert not any(name.startswith("tls/") for name in names), names
    assert not any(name.startswith("m3u_uploads/") for name in names), names
    for table in IDENTITY_TABLES + ("cloud_storage_targets",):
        assert table not in tables, tables
    for sentinel in FORBIDDEN_IN_PLAINTEXT_ARTIFACT:
        assert sentinel.encode() not in raw, sentinel
    for sentinel in artifact_harness.ALL_SECRET_SENTINELS:
        assert sentinel.encode() not in raw, sentinel

    # The scheduled/unattended recovery story: this is the artifact a schedule
    # produces, it needs no key, and it is owner-only on disk.
    assert not art.encrypted
    assert stat.S_IMODE(art.zip_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(art.sidecar_path.stat().st_mode) == 0o600


def test_legacy_settings_json_still_carries_what_the_docs_say_it_carries(tmp_path):
    """Characterization, not approval.

    `backup-overview.md` tells the operator that the legacy `settings.json`
    masks credential-class fields BY NAME, so it keeps the Dispatcharr username,
    and that it predates the value-aware URL scrubber, so a credential embedded
    in a URL value survives. That warning has to describe the artifact ECM
    actually writes. When the legacy settings path gains the DBAS scrubber, this
    test goes red and the documentation is corrected with it rather than after
    it. See the note on this bead: the fix also needs
    `_merge_settings_preserving_redacted` to recognize an embedded sentinel, or
    a restore writes a broken URL over a working one.
    """
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    journal = tmp_path / "journal.db"
    _empty_db(journal)
    _seed_config_dir(tmp_path)
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = MagicMock()
    stub = MagicMock()
    stub.model_dump.return_value = {
        "url": "http://admin:" + "<synthetic-dispatcharr-pw-ZZZ666>" + "@dispatcharr:9191",
        "username": "dispatcharr-operator",
        "password": "<synthetic-dispatcharr-password>",
    }

    with (
        patch.object(backup_mod, "CONFIG_DIR", tmp_path),
        patch.object(backup_mod, "CONFIG_FILE", settings),
        patch.object(backup_mod, "JOURNAL_DB_FILE", journal),
        patch.object(backup_mod, "get_engine", return_value=engine),
        patch.object(backup_mod, "get_settings", return_value=stub),
    ):
        archive = backup_mod._create_backup_zip()

    with zipfile.ZipFile(io.BytesIO(_archive_bytes(archive))) as zf:
        written = json.loads(zf.read("settings.json"))

    assert written["password"] == backup_mod.REDACTED
    # Documented, and still true: the name-based mask keeps the username, and
    # a credential inside a URL value is not touched.
    assert written["username"] == "dispatcharr-operator"
    assert "<synthetic-dispatcharr-pw-ZZZ666>" in written["url"]


def test_content_scan_would_fail_if_the_journal_allowlist_regressed(tmp_path):
    """Instrument check for the two tests above: the same scan run against an
    UNSCRUBBED copy of the seeded database does find the account hash, so a
    clean result means the scrub happened rather than that the fixtures were
    never present."""
    journal = tmp_path / "journal.db"
    _seed_identity_journal(journal)
    raw = journal.read_bytes()
    assert FAKE_PASSWORD_HASH.encode() in raw
    assert FAKE_STORAGE_SECRET.encode() in raw
