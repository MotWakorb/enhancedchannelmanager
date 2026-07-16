"""Migration tests for ``event_sync_reviews`` (revision 0032).

Bead: ``enhancedchannelmanager-ti939.3.2`` (Event Sync ambiguous-band
review queue).

Covers:

* Fresh up from empty → table present with the full fingerprint column
  set, all three indexes (unique fingerprint index marked unique), the
  named status CHECK, and the FK to ``auto_creation_rules`` with
  ``ON DELETE CASCADE``.
* Round-trip: downgrade -1 removes the table + indexes; a second upgrade
  restores them (reversibility per docs/database_migrations.md).
* bd-5w6jz idempotency: re-running the migration on a DB where
  ``create_all()`` already materialised the post-0032 ORM shape is a
  no-op, not an ``OperationalError``.
* CHECK enforcement: ``status='bogus'`` raises IntegrityError.
* Fingerprint dedup invariant: a second row with the same
  ``(rule_id, provider_id, stream_name_hash, event_key)`` raises
  IntegrityError EVEN when the first row is in a terminal state — answered
  questions must never re-enter the queue as duplicates (the epic's
  keying constraint made DB-enforced).
* FK CASCADE: deleting the owning rule deletes its review rows.

All fixtures use synthetic identities — no production-derived values.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError

import database


TABLE = "event_sync_reviews"

EXPECTED_INDEXES = {
    "uq_event_sync_reviews_fingerprint": [
        "rule_id", "provider_id", "stream_name_hash", "event_key",
    ],
    "idx_event_sync_reviews_status_created": ["status", "created_at"],
    "idx_event_sync_reviews_rule_status": ["rule_id", "status"],
}

STATUS_CHECK = "ck_event_sync_reviews_status"


def _make_alembic_config(db_url: str):
    """Build an Alembic Config pinned to *db_url* (per-file helper, see
    the test-isolation convention note in
    ``test_pending_merges_migration.py``)."""
    from alembic.config import Config

    ini_path = Path(database.ALEMBIC_INI_PATH)
    assert ini_path.exists(), f"alembic.ini missing at {ini_path}"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _engine(db_url: str):
    """Engine with ``PRAGMA foreign_keys=ON`` on every connect — matches
    the production connect listener in ``database.py`` so FK assertions
    here test what the app actually enforces."""
    engine = create_engine(db_url, future=True)

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):  # pragma: no cover - trivial
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


def _index_map(engine) -> dict[str, list[str]]:
    return {
        idx["name"]: list(idx["column_names"])
        for idx in inspect(engine).get_indexes(TABLE)
    }


def _insert_rule(conn, rule_id: int) -> None:
    """Minimal raw-SQL auto_creation_rules row for FK targets."""
    conn.execute(text(
        "INSERT INTO auto_creation_rules "
        "(id, name, enabled, priority, conditions, actions, run_on_refresh, "
        " stop_on_first_match, probe_on_sort, quality_m3u_tie_break_enabled, "
        " skip_struck_streams, orphan_action, match_scope_target_group, "
        " allow_manual_channel_merge, fold_match_key, match_count, "
        " created_at, updated_at) "
        "VALUES (:id, :name, 1, 0, '[]', '[]', 0, 1, 0, 1, 0, 'delete', 1, "
        "0, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {"id": rule_id, "name": f"rule-{rule_id}"})


def _insert_review(conn, *, rule_id: int, provider_id: int = 7,
                   stream_hash: str = "a" * 64,
                   event_key: str = "fury vs usyk|2026-07-12T00:00:00+00:00",
                   status: str = "pending") -> None:
    conn.execute(text(
        f"INSERT INTO {TABLE} "
        "(rule_id, provider_id, stream_name_hash, event_key, status, "
        " created_at, last_seen_at, evidence) "
        "VALUES (:rule_id, :provider_id, :stream_hash, :event_key, :status, "
        "1752300000000, 1752300000000, '{}')"
    ), {
        "rule_id": rule_id, "provider_id": provider_id,
        "stream_hash": stream_hash, "event_key": event_key,
        "status": status,
    })


@pytest.mark.integration
class TestMigration0032:
    def test_fresh_upgrade_creates_table_indexes_check_and_fk(self, tmp_path):
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0032_fresh.db'}"
        command.upgrade(_make_alembic_config(db_url), "head")

        engine = _engine(db_url)
        try:
            assert TABLE in set(inspect(engine).get_table_names())

            cols = {c["name"]: c for c in inspect(engine).get_columns(TABLE)}
            assert set(cols) == {
                "id", "rule_id", "provider_id", "stream_name_hash",
                "event_key", "status", "created_at", "last_seen_at",
                "resolved_at", "resolution_source", "actor_token_id",
                "evidence",
            }
            # Fingerprint columns are the identity — all NOT NULL (SQLite
            # unique indexes treat NULLs as distinct; a nullable component
            # would silently break the dedup invariant).
            for col in ("rule_id", "provider_id", "stream_name_hash",
                        "event_key", "status", "created_at", "last_seen_at",
                        "evidence"):
                assert cols[col]["nullable"] is False, f"{col} must be NOT NULL"
            for col in ("resolved_at", "resolution_source", "actor_token_id"):
                assert cols[col]["nullable"] is True, f"{col} must be nullable"

            idx = _index_map(engine)
            for name, columns in EXPECTED_INDEXES.items():
                assert name in idx, f"missing index {name}: have {sorted(idx)}"
                assert idx[name] == columns
            uq = next(
                d for d in inspect(engine).get_indexes(TABLE)
                if d["name"] == "uq_event_sync_reviews_fingerprint"
            )
            assert bool(uq["unique"]), "fingerprint index must be UNIQUE"

            checks = {
                cc["name"]
                for cc in inspect(engine).get_check_constraints(TABLE)
                if cc.get("name")
            }
            assert STATUS_CHECK in checks

            fks = inspect(engine).get_foreign_keys(TABLE)
            assert len(fks) == 1
            assert fks[0]["referred_table"] == "auto_creation_rules"
            assert fks[0]["referred_columns"] == ["id"]
            assert fks[0]["options"].get("ondelete", "").upper() == "CASCADE"
        finally:
            engine.dispose()

    def test_downgrade_removes_table_and_upgrade_restores(self, tmp_path):
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0032_roundtrip.db'}"
        cfg = _make_alembic_config(db_url)
        # Target 0032 explicitly (not "head"/"-1") so this round-trip stays
        # pinned to the 0032 migration under test even as newer revisions
        # (0033+) land on top of it.
        command.upgrade(cfg, "0032")
        command.downgrade(cfg, "0031")

        engine = _engine(db_url)
        try:
            assert TABLE not in set(inspect(engine).get_table_names())
        finally:
            engine.dispose()

        command.upgrade(cfg, "0032")
        engine = _engine(db_url)
        try:
            assert TABLE in set(inspect(engine).get_table_names())
            assert set(_index_map(engine)) == set(EXPECTED_INDEXES)
        finally:
            engine.dispose()

    def test_rerun_after_create_all_drift_is_a_noop(self, tmp_path):
        """bd-5w6jz: create_all() already materialised the post-0032 shape
        (long-running install), then the migration replays — must not raise
        ``OperationalError: table event_sync_reviews already exists``."""
        from alembic import command
        import models

        db_url = f"sqlite:///{tmp_path / 'mig0032_drift.db'}"
        cfg = _make_alembic_config(db_url)
        # Upgrade to the revision BEFORE 0032, then let create_all() add the
        # post-0032 ORM shape (the drift).
        command.upgrade(cfg, "0031")
        engine = _engine(db_url)
        try:
            models.Base.metadata.create_all(engine)
            assert TABLE in set(inspect(engine).get_table_names())
        finally:
            engine.dispose()

        # Replaying 0032 over the drifted DB must be a guarded no-op.
        command.upgrade(cfg, "head")
        engine = _engine(db_url)
        try:
            assert set(_index_map(engine)) >= set(EXPECTED_INDEXES)
        finally:
            engine.dispose()

    def test_status_check_rejects_bogus_status(self, tmp_path):
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0032_check.db'}"
        command.upgrade(_make_alembic_config(db_url), "head")
        engine = _engine(db_url)
        try:
            with engine.begin() as conn:
                _insert_rule(conn, 1)
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    _insert_review(conn, rule_id=1, status="bogus")
        finally:
            engine.dispose()

    def test_fingerprint_unique_even_across_terminal_states(self, tmp_path):
        """Answered questions never re-enter as duplicates: the unique
        index is FULL (unlike pending_merges' partial index), so a second
        row with the same fingerprint raises even when the first row is
        'rejected'."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0032_uq.db'}"
        command.upgrade(_make_alembic_config(db_url), "head")
        engine = _engine(db_url)
        try:
            with engine.begin() as conn:
                _insert_rule(conn, 1)
                _insert_review(conn, rule_id=1, status="rejected")
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    _insert_review(conn, rule_id=1, status="pending")
            # A DIFFERENT fingerprint component (provider) inserts fine.
            with engine.begin() as conn:
                _insert_review(conn, rule_id=1, provider_id=8)
        finally:
            engine.dispose()

    def test_rule_delete_cascades_to_review_rows(self, tmp_path):
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0032_fk.db'}"
        command.upgrade(_make_alembic_config(db_url), "head")
        engine = _engine(db_url)
        try:
            with engine.begin() as conn:
                _insert_rule(conn, 1)
                _insert_review(conn, rule_id=1)
                conn.execute(text(
                    "DELETE FROM auto_creation_rules WHERE id = 1"
                ))
            with engine.connect() as conn:
                count = conn.execute(text(
                    f"SELECT COUNT(*) FROM {TABLE}"
                )).scalar()
            assert count == 0, "FK CASCADE must delete the rule's queue rows"
        finally:
            engine.dispose()
