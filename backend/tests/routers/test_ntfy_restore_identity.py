"""ntfy backup redaction and destination-local restore identity contract."""
import hashlib
import hmac
import json
import sqlite3

import pytest

from credential_sentinel import REDACTION_SENTINEL
from routers import backup as backup_mod


TOKEN = "<destination-ntfy-token>"
TOPIC = "private-topic"
SERVER = "https://ntfy.example.test/base/"
MARKER = "_ecm_ntfy_destination_hmac_v1"


def _db(path, rows):
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE alert_methods (id INTEGER PRIMARY KEY, name TEXT, "
            "method_type TEXT, config TEXT)"
        )
        for row_id, method_type, config in rows:
            conn.execute(
                "INSERT INTO alert_methods (id, name, method_type, config) VALUES (?,?,?,?)",
                (row_id, "same-name", method_type, config if isinstance(config, str) else json.dumps(config)),
            )
        conn.commit()
    finally:
        conn.close()
    return path


def _configs(path):
    conn = sqlite3.connect(str(path))
    try:
        return {
            row_id: json.loads(raw) if raw != REDACTION_SENTINEL else raw
            for row_id, raw in conn.execute("SELECT id, config FROM alert_methods")
        }
    finally:
        conn.close()


def _marker(server=SERVER, topic=TOPIC, token=TOKEN):
    message = b"ecm:ntfy-destination:v1\0" + server.rstrip("/").encode() + b"\0" + topic.encode()
    return hmac.new(token.encode(), message, hashlib.sha256).hexdigest()


def test_standard_producer_redacts_ntfy_destination_and_adds_keyed_verifier(tmp_path):
    path = _db(tmp_path / "journal.db", [(1, "ntfy", {
        "server_url": SERVER, "topic": TOPIC, "access_token": TOKEN,
    }), (2, "webhook", {"topic": "ordinary-topic"})])

    backup_mod._scrub_journal_db_in_place(path)

    configs = _configs(path)
    assert configs[1] == {
        "server_url": SERVER,
        "topic": REDACTION_SENTINEL,
        "access_token": REDACTION_SENTINEL,
        MARKER: _marker(),
    }
    assert configs[2]["topic"] == "ordinary-topic"
    decompressed_members = path.read_bytes()
    assert TOPIC.encode() not in decompressed_members
    assert TOKEN.encode() not in decompressed_members


def test_unauthenticated_standard_producer_has_no_verifier(tmp_path):
    path = _db(tmp_path / "journal.db", [(1, "ntfy", {
        "server_url": SERVER, "topic": TOPIC,
    })])
    backup_mod._scrub_journal_db_in_place(path)
    assert _configs(path)[1] == {
        "server_url": SERVER,
        "topic": REDACTION_SENTINEL,
    }


def test_encrypted_credential_copy_is_byte_preserving_and_marker_free(tmp_path):
    source = _db(tmp_path / "journal.db", [(1, "ntfy", {
        "server_url": SERVER, "topic": TOPIC, "access_token": TOKEN,
    })])
    before = source.read_bytes()
    copied = backup_mod._scrub_journal_db_to_temp(source, include_credentials=True)
    try:
        assert copied.read_bytes() == before
        assert MARKER.encode() not in copied.read_bytes()
        assert _configs(copied)[1]["topic"] == TOPIC
    finally:
        copied.unlink()


def _restore(tmp_path, prior_type, prior_config, archived_type, archived_config):
    live = _db(tmp_path / "live.db", [(1, prior_type, prior_config)])
    prior = backup_mod._capture_existing_alert_method_configs(live)
    live.unlink()
    _db(live, [(1, archived_type, archived_config)])
    backup_mod._merge_alert_method_creds_after_restore(prior, live)
    return _configs(live)[1]


@pytest.mark.parametrize("archived,expected_protected", [
    ({"server_url": SERVER, "topic": REDACTION_SENTINEL, "access_token": REDACTION_SENTINEL,
      MARKER: _marker()}, True),
    ({"server_url": "https://other.example.test", "topic": REDACTION_SENTINEL,
      "access_token": REDACTION_SENTINEL, MARKER: _marker()}, False),
    ({"server_url": SERVER, "topic": REDACTION_SENTINEL, "access_token": REDACTION_SENTINEL,
      MARKER: _marker(topic="different-topic")}, False),
    ({"server_url": SERVER, "topic": REDACTION_SENTINEL, "access_token": REDACTION_SENTINEL,
      MARKER: "0" * 64}, False),
    ({"server_url": SERVER, "topic": REDACTION_SENTINEL, "access_token": REDACTION_SENTINEL,
      MARKER: "malformed"}, False),
    ({"server_url": SERVER, "topic": REDACTION_SENTINEL, "access_token": REDACTION_SENTINEL,
      MARKER: _marker(token="<rotated-token>")}, False),
    ({"server_url": SERVER, "topic": TOPIC, "access_token": REDACTION_SENTINEL}, True),
    ({"server_url": SERVER, "topic": "other-topic", "access_token": REDACTION_SENTINEL}, False),
])
def test_authenticated_restore_requires_destination_identity_proof(
    tmp_path, archived, expected_protected
):
    restored = _restore(
        tmp_path,
        "ntfy",
        {"server_url": SERVER, "topic": TOPIC, "access_token": TOKEN},
        "ntfy",
        archived,
    )
    assert MARKER not in restored
    if expected_protected:
        assert restored["topic"] == TOPIC
        assert restored["access_token"] == TOKEN
    else:
        assert "topic" not in restored
        assert "access_token" not in restored


def test_unauthenticated_restore_may_preserve_only_local_topic(tmp_path):
    restored = _restore(
        tmp_path,
        "ntfy",
        {"server_url": SERVER, "topic": TOPIC},
        "ntfy",
        {"server_url": SERVER, "topic": REDACTION_SENTINEL},
    )
    assert restored == {"server_url": SERVER, "topic": TOPIC}


def test_verifier_absent_with_authenticated_local_preserves_nothing(tmp_path):
    restored = _restore(
        tmp_path,
        "ntfy",
        {"server_url": SERVER, "topic": TOPIC, "access_token": TOKEN},
        "ntfy",
        {"server_url": SERVER, "topic": REDACTION_SENTINEL,
         "access_token": REDACTION_SENTINEL},
    )
    assert restored == {"server_url": SERVER}


@pytest.mark.parametrize("prior_type,archived_type", [("smtp", "ntfy"), ("ntfy", "smtp")])
def test_cross_type_row_id_collision_never_transfers_ntfy_values(
    tmp_path, prior_type, archived_type
):
    prior_config = (
        {"server_url": SERVER, "topic": TOPIC, "access_token": TOKEN}
        if prior_type == "ntfy" else {"password": "<smtp-token>"}
    )
    archived_config = (
        {"server_url": SERVER, "topic": REDACTION_SENTINEL,
         "access_token": REDACTION_SENTINEL, MARKER: _marker()}
        if archived_type == "ntfy" else {"password": REDACTION_SENTINEL}
    )
    restored = _restore(tmp_path, prior_type, prior_config, archived_type, archived_config)
    assert TOPIC not in restored.values()
    assert TOKEN not in restored.values()
    assert MARKER not in restored


def test_ntfy_whole_blob_sentinel_does_not_transplant_local_destination(tmp_path):
    restored = _restore(
        tmp_path,
        "ntfy",
        {"server_url": SERVER, "topic": TOPIC, "access_token": TOKEN},
        "ntfy",
        REDACTION_SENTINEL,
    )
    assert restored == REDACTION_SENTINEL


def test_non_ntfy_restore_semantics_are_unchanged(tmp_path):
    restored = _restore(
        tmp_path,
        "smtp",
        {"host": "smtp.local", "password": "<smtp-token>"},
        "smtp",
        {"host": "smtp.archive", "password": REDACTION_SENTINEL},
    )
    assert restored == {"host": "smtp.archive", "password": "<smtp-token>"}


def test_legacy_ntfy_backup_with_raw_credentials_is_unchanged(tmp_path):
    archived = {"server_url": "https://archive.example", "topic": "archive-topic",
                "access_token": "<archive-token>"}
    assert _restore(
        tmp_path,
        "ntfy",
        {"server_url": SERVER, "topic": TOPIC, "access_token": TOKEN},
        "ntfy",
        archived,
    ) == archived
