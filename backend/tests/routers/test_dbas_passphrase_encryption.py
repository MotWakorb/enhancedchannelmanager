"""Integrated tests for the opt-in whole-artifact passphrase-encryption path
(ADR-012 D12, bead ``enhancedchannelmanager-u81kh``; threat model Addendum C §10).

These exercise the TWO seams end-to-end through the real
:mod:`dbas.artifact_crypto` primitive:

* the ``build_backup_artifact`` ENCRYPT stage (redact-then-encrypt, the
  ``include_credentials`` re-injection, the min-passphrase + ack hard gates), and
* the ``DbasRestoreTask`` DECRYPT-at-ingest gate (pre-decrypt version gate,
  missing-passphrase refusal, identical sanitized wrong-passphrase failure).

The named checklist tests from Addendum C §10:
  - test_redact_then_encrypt_no_plaintext
  - test_wrong_passphrase_and_corrupt_artifact_identical_exception (gate-level;
    the primitive-level structural proof lives in tests/dbas/test_artifact_crypto.py)
  - test_header_version_check_before_decrypt
  - test_chunk_reorder_or_truncate_rejected (primitive-level; see test_artifact_crypto.py)
  - test_min_passphrase_length_enforced
  - test_lost_passphrase_unrecoverable_ack_required
"""
import asyncio
import json
import struct
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dbas import artifact_crypto
from routers import backup as backup_mod
from routers.backup import build_backup_artifact

SECRET_M3U_PASSWORD = "M3U_SECRET_pw_ZZZ111"
SECRET_SMTP_PASSWORD = "SMTP_SECRET_pw_ZZZ222"
SECRET_DISPATCHARR_KEY = "DISPATCHARR_SECRET_key_ZZZ444"
SECRET_TELEGRAM_TOKEN = "TELEGRAM_SECRET_tok_ZZZ555"
GOOD_PASS = "a-strong-migration-passphrase"  # >= 12 chars


def _mock_settings_with_secrets():
    s = MagicMock()
    s.model_dump.return_value = {
        "url": "http://dispatcharr:9191",
        "username": "admin",
        "password": SECRET_M3U_PASSWORD,
        "dispatcharr_api_key": SECRET_DISPATCHARR_KEY,
        "api_key": SECRET_DISPATCHARR_KEY,
        "smtp_password": SECRET_SMTP_PASSWORD,
        "telegram_bot_token": SECRET_TELEGRAM_TOKEN,
        "mcp_api_key": "MCP_SECRET",
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
    import sqlite3
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE alert_methods (id INTEGER PRIMARY KEY, config TEXT)")
        conn.execute(
            "INSERT INTO alert_methods (id, config) VALUES (?, ?)",
            (1, json.dumps({"host": "smtp.example.com", "password": SECRET_SMTP_PASSWORD})),
        )
        conn.commit()
    finally:
        conn.close()


def _all_member_bytes(zip_path) -> bytes:
    """Concatenate every DECOMPRESSED member's bytes (a raw scan of the ZIP file
    is meaningless — DEFLATE would hide a plaintext secret)."""
    out = bytearray()
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            out += zf.read(name)
    return bytes(out)


def _build(tmp_path, **kwargs):
    """Run build_backup_artifact against a seeded temp CONFIG dir; pass through
    passphrase / include_credentials / acknowledge_unrecoverable kwargs."""
    config_dir = tmp_path
    journal = config_dir / "journal.db"
    _seed_journal_db(journal)
    (config_dir / "settings.json").write_text("{}")

    mock_client = MagicMock()
    mock_client.get_m3u_accounts = AsyncMock(
        return_value=[{"id": 1, "name": "prov", "password": SECRET_M3U_PASSWORD,
                       "server_url": "http://prov/get.php"}]
    )
    mock_client.get_epg_sources = AsyncMock(return_value=[])
    mock_client.get_channel_groups = AsyncMock(return_value=[])
    mock_client.get_channel_profiles = AsyncMock(return_value=[])
    mock_client.get_stream_profiles = AsyncMock(return_value=[])

    session = MagicMock()
    session.query.return_value.all.return_value = []
    session.query.return_value.filter_by.return_value.all.return_value = []
    session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = []
    session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

    with patch.object(backup_mod, "CONFIG_DIR", config_dir), \
         patch.object(backup_mod, "CONFIG_FILE", config_dir / "settings.json"), \
         patch.object(backup_mod, "JOURNAL_DB_FILE", journal), \
         patch.object(backup_mod, "get_engine", return_value=_mock_engine()), \
         patch.object(backup_mod, "get_settings", return_value=_mock_settings_with_secrets()), \
         patch.object(backup_mod, "get_session", return_value=session), \
         patch.object(backup_mod, "get_client", return_value=mock_client):
        return asyncio.get_event_loop().run_until_complete(
            build_backup_artifact(dest_dir=config_dir, **kwargs)
        )


# --- encrypt seam ----------------------------------------------------------

def test_encrypted_artifact_is_not_a_plain_zip(tmp_path):
    art = _build(tmp_path, passphrase=GOOD_PASS, acknowledge_unrecoverable=True)
    assert art.encrypted is True
    assert artifact_crypto.is_encrypted_artifact(art.zip_path)
    # The on-disk artifact is NOT a readable ZIP, and no secret survives in the
    # encrypted bytes.
    with pytest.raises(zipfile.BadZipFile):
        zipfile.ZipFile(art.zip_path).namelist()
    raw = art.zip_path.read_bytes()
    for secret in (SECRET_M3U_PASSWORD, SECRET_SMTP_PASSWORD, SECRET_DISPATCHARR_KEY,
                   SECRET_TELEGRAM_TOKEN):
        assert secret.encode() not in raw


def test_redact_then_encrypt_no_plaintext(tmp_path):
    """include_credentials=False + passphrase: decrypt -> no plaintext cred
    (redaction is NOT skipped inside the encrypt path; checklist 28)."""
    art = _build(tmp_path, passphrase=GOOD_PASS, include_credentials=False,
                 acknowledge_unrecoverable=True)
    dec = tmp_path / "plain.zip"
    artifact_crypto.decrypt_file(art.zip_path, GOOD_PASS, dec)

    members = _all_member_bytes(dec)
    for secret in (SECRET_M3U_PASSWORD, SECRET_SMTP_PASSWORD, SECRET_DISPATCHARR_KEY,
                   SECRET_TELEGRAM_TOKEN):
        assert secret.encode() not in members, "redaction was skipped under encryption"
    # The decrypted artifact IS a well-formed ZIP and the manifest marks it redacted.
    with zipfile.ZipFile(dec) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["redacted"] is True


def test_include_credentials_reinjects_settings_creds(tmp_path):
    """The opt-in cred-carrying path re-injects the approved migration creds
    (settings SMTP password) into the decrypted artifact (checklist 27)."""
    art = _build(tmp_path, passphrase=GOOD_PASS, include_credentials=True,
                 acknowledge_unrecoverable=True)
    dec = tmp_path / "plain.zip"
    artifact_crypto.decrypt_file(art.zip_path, GOOD_PASS, dec)
    members = _all_member_bytes(dec)
    # SMTP password is carried (it is ECM-held + recoverable) for migration.
    assert SECRET_SMTP_PASSWORD.encode() in members
    with zipfile.ZipFile(dec) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["redacted"] is False


def test_include_credentials_requires_passphrase(tmp_path):
    with pytest.raises(ValueError, match="requires a passphrase"):
        _build(tmp_path, include_credentials=True)


def test_lost_passphrase_unrecoverable_ack_required(tmp_path):
    """Producing an encrypted backup requires acknowledge_unrecoverable
    (checklist 34: lost passphrase = permanently unrecoverable)."""
    with pytest.raises(ValueError, match="acknowledge_unrecoverable"):
        _build(tmp_path, passphrase=GOOD_PASS, acknowledge_unrecoverable=False)


def test_min_passphrase_length_enforced(tmp_path):
    """An 11-char passphrase is rejected at the build boundary (checklist 29)."""
    assert artifact_crypto.MIN_PASSPHRASE_LENGTH == 12
    with pytest.raises(ValueError, match="at least 12 characters"):
        _build(tmp_path, passphrase="short-11-ch", acknowledge_unrecoverable=True)  # 11


def test_plain_backup_is_unencrypted_by_default(tmp_path):
    """No passphrase -> redact-by-default plain ZIP is preserved (checklist 27)."""
    art = _build(tmp_path)
    assert art.encrypted is False
    assert not artifact_crypto.is_encrypted_artifact(art.zip_path)
    with zipfile.ZipFile(art.zip_path) as zf:  # a real ZIP
        assert "manifest.json" in zf.namelist()


# --- pre-decrypt version gate ----------------------------------------------

def test_header_version_check_before_decrypt(tmp_path):
    """An unsupported envelope format_version is rejected reading the cleartext
    header WITHOUT a passphrase (checklist 30)."""
    art = _build(tmp_path, passphrase=GOOD_PASS, acknowledge_unrecoverable=True)
    raw = art.zip_path.read_bytes()
    (hlen,) = struct.unpack(">I", raw[8:12])
    header = json.loads(raw[12:12 + hlen])
    header["format_version"] = artifact_crypto.FORMAT_VERSION + 1
    new_header = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    art.zip_path.write_bytes(
        artifact_crypto.MAGIC + struct.pack(">I", len(new_header)) + new_header + raw[12 + hlen:]
    )
    with pytest.raises(artifact_crypto.UnsupportedArtifactFormatError):
        artifact_crypto.read_header(art.zip_path)


# --- restore decrypt-at-ingest gate ----------------------------------------

def _make_restore_task():
    from tasks.dbas_restore import DbasRestoreTask
    task = DbasRestoreTask()
    # _set_progress reaches notification callbacks that aren't wired in a bare
    # instance; stub it so the gate logic is what's under test.
    task._set_progress = MagicMock()
    return task


def _run_gate(task, started_at, path):
    return asyncio.get_event_loop().run_until_complete(task._decrypt_gate(started_at, path))


def test_restore_gate_passes_plain_artifact_through(tmp_path):
    from datetime import datetime, timezone
    art = _build(tmp_path)  # plain
    task = _make_restore_task()
    effective, decrypted, failure = _run_gate(task, datetime.now(timezone.utc), art.zip_path)
    assert failure is None
    assert decrypted is None
    assert effective == art.zip_path


def test_restore_gate_requires_passphrase_for_encrypted(tmp_path):
    from datetime import datetime, timezone
    art = _build(tmp_path, passphrase=GOOD_PASS, acknowledge_unrecoverable=True)
    task = _make_restore_task()  # no passphrase configured
    effective, decrypted, failure = _run_gate(task, datetime.now(timezone.utc), art.zip_path)
    assert decrypted is None
    assert failure is not None and failure.success is False
    assert "passphrase is required" in failure.message


def test_restore_gate_wrong_passphrase_sanitized_failure(tmp_path):
    from datetime import datetime, timezone
    art = _build(tmp_path, passphrase=GOOD_PASS, acknowledge_unrecoverable=True)
    task = _make_restore_task()
    task.passphrase = "totally-wrong-passphrase"
    effective, decrypted, failure = _run_gate(task, datetime.now(timezone.utc), art.zip_path)
    assert failure is not None and failure.success is False
    assert "wrong passphrase or corrupted artifact" in failure.message
    # Identical message for a corrupted artifact (no oracle).
    raw = bytearray(art.zip_path.read_bytes())
    raw[-1] ^= 0xFF
    corrupt = tmp_path / "corrupt.zip"
    corrupt.write_bytes(raw)
    task.passphrase = GOOD_PASS
    _, _, failure2 = _run_gate(task, datetime.now(timezone.utc), corrupt)
    assert failure2.message == failure.message


def test_restore_gate_decrypts_valid_artifact(tmp_path):
    from datetime import datetime, timezone
    art = _build(tmp_path, passphrase=GOOD_PASS, acknowledge_unrecoverable=True)
    task = _make_restore_task()
    task.passphrase = GOOD_PASS
    effective, decrypted, failure = _run_gate(task, datetime.now(timezone.utc), art.zip_path)
    assert failure is None
    assert decrypted is not None and decrypted == effective
    # The decrypted temp is a valid ZIP the importers can open.
    with zipfile.ZipFile(effective) as zf:
        assert "manifest.json" in zf.namelist()
    task._cleanup_decrypted(decrypted)
    assert not decrypted.exists()
