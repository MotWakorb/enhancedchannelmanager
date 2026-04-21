"""Alembic baseline up/down round-trip smoke test (bd-mcnj0).

Purpose
-------
PR #81 (bd-c5wf5) landed the Alembic baseline revision 0001 with a hand-written
downgrade that drops every table + index. The landing tests
(`test_alembic_baseline.py`) cover the up path and FK enforcement, but NOT a
full round-trip. A silently-broken downgrade in the baseline would only
surface at the worst possible time — the first real rollback.

This integration test closes that gap with a first-usable-version smoke test
(not a framework): upgrade -> seed realistic FK-bearing data -> downgrade to
base -> re-upgrade -> assert schema identity and confirm re-seeding still
works on the regenerated schema.

Scope
-----
- SQLite only (the baseline is SQLite; PostgreSQL round-trip is out of scope
  until the engine changes).
- Realistic-shape data, not production-volume: >=100 rows spanning FK-bearing
  parent/child tables so ON DELETE CASCADE paths exist at downgrade time.
- No new migrations authored here. This test exercises the existing baseline
  only — if downgrade is broken, fail loudly; do NOT patch the baseline in
  this scope.

See ``docs/database_migrations.md`` for the migration authoring workflow.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

import database


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_alembic_config(db_url: str):
    """Build an Alembic Config pinned to the given SQLite URL.

    Mirrors the helper in ``tests/unit/test_alembic_baseline.py`` so the two
    files can diverge independently without shared-helper churn.
    """
    from alembic.config import Config

    ini_path = Path(database.ALEMBIC_INI_PATH)
    assert ini_path.exists(), f"alembic.ini missing at {ini_path}"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _snapshot_schema(engine) -> dict[str, Any]:
    """Capture a structural snapshot of the DB schema.

    We normalise to Alembic-level structure (tables, columns, indexes, FKs)
    rather than raw ``sqlite_master.sql`` strings because SQLite rewrites the
    CREATE TABLE text during ``op.batch_alter_table`` round-trips (quoting,
    column order in the serialised SQL, etc.) even when the logical schema
    is identical. Comparing at the Inspector level asserts semantic identity,
    which is what we actually care about.
    """
    inspector = inspect(engine)

    tables: dict[str, Any] = {}
    for table_name in sorted(inspector.get_table_names()):
        if table_name == "alembic_version":
            # Tracked separately — its presence/content is a cycle assertion.
            continue

        columns = [
            {
                "name": c["name"],
                "type": str(c["type"]),
                "nullable": c["nullable"],
                "primary_key": c.get("primary_key", 0),
                # default is intentionally omitted: SQLite renders server
                # defaults differently after a round-trip (e.g. "0" vs "'0'")
                # without changing semantics. The drift test in
                # tests/unit/test_alembic_baseline.py already guards defaults.
            }
            for c in inspector.get_columns(table_name)
        ]

        indexes = sorted(
            [
                {
                    "name": idx["name"],
                    "unique": idx["unique"],
                    "columns": list(idx["column_names"]),
                }
                for idx in inspector.get_indexes(table_name)
            ],
            key=lambda d: d["name"] or "",
        )

        foreign_keys = sorted(
            [
                {
                    "constrained_columns": list(fk["constrained_columns"]),
                    "referred_table": fk["referred_table"],
                    "referred_columns": list(fk["referred_columns"]),
                    "options": fk.get("options", {}),
                }
                for fk in inspector.get_foreign_keys(table_name)
            ],
            key=lambda d: (d["referred_table"], tuple(d["constrained_columns"])),
        )

        primary_key = inspector.get_pk_constraint(table_name)

        tables[table_name] = {
            "columns": columns,
            "indexes": indexes,
            "foreign_keys": foreign_keys,
            "primary_key_columns": list(primary_key.get("constrained_columns", [])),
        }

    return {"tables": tables}


def _count_app_tables(engine) -> int:
    """Return the number of non-alembic, non-sqlite-internal tables."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' "
                "AND name != 'alembic_version'"
            )
        ).fetchall()
    return len(rows)


def _seed_realistic_data(engine) -> dict[str, int]:
    """Seed >=100 rows across FK-bearing parent/child tables.

    Table mix chosen to exercise the baseline's ON DELETE CASCADE paths
    (TagGroup -> Tag, NormalizationRuleGroup -> NormalizationRule) plus a
    bulk of leaf-table rows (JournalEntry) to clear the >=100 row threshold
    without over-engineering the fixture.

    Returns row-counts-per-table so the test can assert inserts landed.
    """
    import models

    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = SessionLocal()
    counts: dict[str, int] = {}

    try:
        # Parent: TagGroup. Child: Tag (ON DELETE CASCADE). 5 groups * 10 tags.
        tag_group_ids: list[int] = []
        for i in range(5):
            g = models.TagGroup(
                name=f"smoke-tag-group-{i}",
                description=f"Smoke test tag group {i}",
                is_builtin=False,
            )
            session.add(g)
            session.flush()
            tag_group_ids.append(g.id)

            for j in range(10):
                session.add(
                    models.Tag(
                        group_id=g.id,
                        value=f"TAG-{i}-{j}",
                        case_sensitive=False,
                        enabled=True,
                        is_builtin=False,
                    )
                )
        session.commit()
        counts["tag_groups"] = 5
        counts["tags"] = 50

        # Parent: NormalizationRuleGroup. Child: NormalizationRule.
        # 3 groups * 5 rules = 15 rows, exercises SET NULL + CASCADE paths
        # depending on column (see models.py:609,810 for FK definitions).
        for i in range(3):
            rg = models.NormalizationRuleGroup(
                name=f"smoke-norm-group-{i}",
                description=f"Smoke norm group {i}",
                is_builtin=False,
                enabled=True,
                priority=i,
            )
            session.add(rg)
            session.flush()

            # Link the group to one of the tag groups to populate the
            # nullable FK column (tag_group_id → tag_groups.id ON DELETE
            # SET NULL) for at least some rows.
            tag_group_id = tag_group_ids[i] if i < len(tag_group_ids) else None

            for j in range(5):
                session.add(
                    models.NormalizationRule(
                        group_id=rg.id,
                        tag_group_id=tag_group_id,
                        name=f"rule-{i}-{j}",
                        description=f"Smoke rule {i}-{j}",
                        enabled=True,
                        priority=j,
                        condition_type="regex",
                        condition_value=f"^prefix-{j}",
                        case_sensitive=False,
                        condition_logic="AND",
                        action_type="remove",
                        action_value=None,
                        stop_processing=False,
                        is_builtin=False,
                    )
                )
        session.commit()
        counts["normalization_rule_groups"] = 3
        counts["normalization_rules"] = 15

        # Bulk leaf rows: JournalEntry. 60 rows → puts total well over 100.
        for i in range(60):
            session.add(
                models.JournalEntry(
                    timestamp=datetime.utcnow() - timedelta(minutes=i),
                    category="channel",
                    action_type="create",
                    entity_id=i,
                    entity_name=f"smoke-entity-{i}",
                    description=f"Smoke journal entry {i}",
                    before_value=None,
                    after_value=json.dumps({"id": i}),
                    user_initiated=False,
                    batch_id="smoke-batch-1",
                )
            )
        session.commit()
        counts["journal_entries"] = 60

        # AlertMethod: 2 rows to exercise a table with many indexed columns.
        for i in range(2):
            session.add(
                models.AlertMethod(
                    name=f"smoke-alert-{i}",
                    method_type="webhook",
                    enabled=True,
                    config=json.dumps({"url": f"https://example.invalid/{i}"}),
                    notify_info=True,
                    notify_success=True,
                    notify_warning=True,
                    notify_error=True,
                    alert_sources=None,
                    last_sent_at=None,
                )
            )
        session.commit()
        counts["alert_methods"] = 2

        # BandwidthDaily: 10 date-keyed rows, exercises the idx_bandwidth_daily_date
        # index that the downgrade explicitly drops.
        today = date.today()
        for i in range(10):
            session.add(
                models.BandwidthDaily(
                    date=today - timedelta(days=i),
                    bytes_transferred=1024 * (i + 1),
                    peak_channels=i,
                    peak_clients=i * 2,
                )
            )
        session.commit()
        counts["bandwidth_daily"] = 10

    finally:
        session.close()

    total = sum(counts.values())
    # Guard: if someone trims the seed helper, fail loudly rather than silently
    # under-exercise the smoke test.
    assert total >= 100, f"Seed helper produced {total} rows; expected >=100"
    return counts


# ---------------------------------------------------------------------------
# The smoke test
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestAlembicRoundTrip:
    """upgrade -> seed -> downgrade -> upgrade -> re-seed, with schema identity."""

    def test_upgrade_downgrade_upgrade_preserves_schema(self, tmp_path):
        """Full round-trip: baseline down/up cycle must leave schema identical.

        If this test fails with a DB error during downgrade, STOP — file a
        P1 bug bead with the repro. Do not attempt to patch the baseline
        revision in the scope of this smoke test (see bd-mcnj0 scope).
        """
        from alembic import command

        db_file = tmp_path / "alembic_smoke.db"
        db_url = f"sqlite:///{db_file}"
        cfg = _make_alembic_config(db_url)

        # ---- 1. upgrade head on a fresh DB ----
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            # Sanity: schema is populated, at head revision.
            assert _count_app_tables(engine) > 0, "No tables after initial upgrade"
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
                assert row is not None
                head_before = row[0]
                assert head_before == database.get_alembic_head_revision()

            # ---- 2. seed realistic FK-bearing data ----
            seeded_counts = _seed_realistic_data(engine)
            total_seeded = sum(seeded_counts.values())
            assert total_seeded >= 100, (
                f"Expected >=100 seed rows, got {total_seeded}: {seeded_counts}"
            )

            # ---- 3. snapshot schema pre-downgrade ----
            snapshot_before = _snapshot_schema(engine)
            assert snapshot_before["tables"], "Pre-downgrade snapshot is empty"

        finally:
            engine.dispose()

        # ---- 4. downgrade to base (fully unwinds the baseline) ----
        # If this raises, the baseline downgrade is broken — let the
        # exception propagate so the test fails loudly with the traceback,
        # which is the repro for the follow-up bug bead.
        command.downgrade(cfg, "base")

        engine = create_engine(db_url, future=True)
        try:
            # After downgrade to base the app tables must be gone.
            remaining = _count_app_tables(engine)
            assert remaining == 0, (
                f"After downgrade to base, {remaining} app tables remain. "
                "Baseline downgrade is incomplete — file a P1 bug bead with "
                "this test as the repro and do NOT patch in this scope."
            )
        finally:
            engine.dispose()

        # ---- 5. re-upgrade to head ----
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
                assert row is not None
                head_after = row[0]
                assert head_after == head_before, (
                    f"Head revision changed across cycle: {head_before} -> {head_after}"
                )

            # ---- 6. snapshot schema post-re-upgrade ----
            snapshot_after = _snapshot_schema(engine)

            # ---- 7. assert schema identity ----
            # Tables present.
            tables_before = set(snapshot_before["tables"].keys())
            tables_after = set(snapshot_after["tables"].keys())
            assert tables_before == tables_after, (
                "Tables differ across round-trip:\n"
                f"  missing after:  {sorted(tables_before - tables_after)}\n"
                f"  extra after:    {sorted(tables_after - tables_before)}"
            )

            # Per-table structure.
            for tname in sorted(tables_before):
                before = snapshot_before["tables"][tname]
                after = snapshot_after["tables"][tname]
                assert before == after, (
                    f"Schema for table {tname!r} differs across round-trip.\n"
                    f"  before: {before}\n"
                    f"  after:  {after}"
                )

            # ---- 8. re-seed a subset; inserts must still succeed on the
            # regenerated schema (catches the case where FK/index metadata
            # regenerates but column semantics drift). We reuse the same
            # helper — if the schema is truly identical, it will succeed
            # cleanly against the fresh post-downgrade DB.
            reseeded_counts = _seed_realistic_data(engine)
            assert reseeded_counts == seeded_counts, (
                f"Re-seed produced different counts: {reseeded_counts} vs {seeded_counts}"
            )
        finally:
            engine.dispose()

    def test_cascade_delete_survives_round_trip(self, tmp_path):
        """ON DELETE CASCADE must still fire on tables regenerated post-downgrade.

        Guards against the baseline downgrade dropping a FK constraint and
        the re-upgrade restoring the column without the ``ON DELETE CASCADE``
        clause — a silent data-integrity regression that schema-shape
        identity alone can miss.
        """
        from alembic import command

        db_file = tmp_path / "alembic_smoke_cascade.db"
        db_url = f"sqlite:///{db_file}"
        cfg = _make_alembic_config(db_url)

        # Round-trip the schema first so we're testing cascade behaviour on
        # the re-upgraded DB, not the pristine one.
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            # Re-attach the ``PRAGMA foreign_keys=ON`` listener. The
            # database module registers a global Engine connect listener,
            # but to keep this test self-contained we also set it on each
            # connection we open here — belt-and-braces.
            with engine.connect() as conn:
                conn.execute(text("PRAGMA foreign_keys=ON"))
                conn.commit()

            import models

            SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
            session = SessionLocal()
            try:
                # Parent + child.
                group = models.TagGroup(
                    name="cascade-smoke",
                    description="Round-trip cascade test",
                    is_builtin=False,
                )
                session.add(group)
                session.flush()

                tag = models.Tag(
                    group_id=group.id,
                    value="cascade-child",
                    case_sensitive=False,
                    enabled=True,
                    is_builtin=False,
                )
                session.add(tag)
                session.commit()
                child_id = tag.id
                assert child_id is not None

                # Ensure the connection this session is about to use for
                # DELETE has FK enforcement on. SQLAlchemy pools connections,
                # so set it on the session's bind before the cascade fires.
                session.execute(text("PRAGMA foreign_keys=ON"))
                session.commit()

                session.delete(group)
                session.commit()

                from sqlalchemy import select

                remaining = session.execute(
                    select(models.Tag).where(models.Tag.id == child_id)
                ).first()
                assert remaining is None, (
                    "ON DELETE CASCADE did not fire on the regenerated schema — "
                    "the baseline downgrade/re-upgrade cycle dropped the "
                    "cascade clause. File a P1 bug bead."
                )
            finally:
                session.close()
        finally:
            engine.dispose()
