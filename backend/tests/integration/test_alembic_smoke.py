"""Alembic baseline up/down round-trip smoke test (bd-mcnj0 / bd-z7bfj / bd-ax3uj).

Migration 0003/0004 idempotency regression tests (bd-ax3uj)
---------------------------------------------------------------------------
Migrations 0003 (``rule_lint_findings`` table + indexes) and 0004
(``idx_journal_batch_id`` + ``idx_journal_entity`` on ``journal_entries``)
both create artifacts that ``Base.metadata.create_all()`` also emits from
the ORM models. On a long-running install the create_all path beat the
migrations to it — leaving the table/indexes physically present while
``alembic_version`` lagged behind. The next start re-ran the migrations
and SQLite raised ``OperationalError: ... already exists``, aborting
boot (post-bd-zaaey, bootstrap loud-fails). ``TestMigration0003`` and
``TestMigration0004`` lock in the inspect-then-skip fix.

Migration 0005 batch_alter_table + idempotency regression tests (bd-z7bfj)
---------------------------------------------------------------------------
PR #229 / bead m2k7p fixed two bugs in migration 0005
(``auto_creation_quality_m3u_tie_break``):

1. ``CircularDependencyError`` raised by ``batch_alter_table`` when both
   ``quality_tie_break_order`` and ``quality_m3u_tie_break_enabled`` were added
   in a single batch context.  The fix splits the adds into separate
   ``batch_alter_table`` calls.

2. Idempotency bug: long-running installs where ``database._run_migrations``
   (``create_all()`` fallback) had already added those columns while
   ``alembic_version`` stayed at ``0004``.  Alembic would then attempt to add
   them again and fail.  The fix inspects existing columns before each batch
   add and skips columns that are already present.

CI did not catch either because the in-memory SQLite test setup uses
``Base.metadata.create_all()`` + ``alembic stamp`` instead of running
``alembic upgrade``, so ``batch_alter_table`` on ``auto_creation_rules`` is
never exercised.  The tests in ``TestMigration0005`` close that gap.

---

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


# ---------------------------------------------------------------------------
# Migration 0005 — batch_alter_table + idempotency regression tests (bd-z7bfj)
# ---------------------------------------------------------------------------

def _column_names(engine, table_name: str) -> set[str]:
    """Return the set of column names for *table_name* using the given engine."""
    inspector = inspect(engine)
    return {c["name"] for c in inspector.get_columns(table_name)}


def _index_names(engine, table_name: str) -> set[str]:
    """Return the set of index names for *table_name* using the given engine."""
    inspector = inspect(engine)
    if not inspector.has_table(table_name):
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


# ---------------------------------------------------------------------------
# Migration 0003 — rule_lint_findings idempotency (bd-ax3uj)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMigration0003:
    """Regression lock for bd-ax3uj fix in migration 0003.

    A user reported ``sqlite3.OperationalError: table rule_lint_findings
    already exists`` during ``Running upgrade 0002 -> 0003``. Container
    startup aborted (post-bd-zaaey, bootstrap loud-fails), taking the
    prober and everything else with it.

    Root cause: ``init_db`` runs ``Base.metadata.create_all()`` after
    ``_bootstrap_alembic`` (and the older code path also ran it on the
    bootstrap-failure fallback). On a long-running install that path
    materialised the ``rule_lint_findings`` table from the
    ``RuleLintFinding`` ORM model — but ``alembic_version`` was still at
    ``0002``. The next start re-ran 0003's ``op.create_table`` over the
    existing table and exploded.

    The 0003 fix mirrors the 0005 pattern (bd-m2k7p / bd-z7bfj):
    inspect existing schema and skip ``create_table`` / ``create_index``
    for artifacts already present.
    """

    def test_fresh_sqlite_upgrade_through_0003(self, tmp_path):
        """Fresh empty DB: ``alembic upgrade 0003`` creates the table + indexes."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0003_fresh.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0003")

        engine = create_engine(db_url, future=True)
        try:
            inspector = inspect(engine)
            assert inspector.has_table("rule_lint_findings"), (
                "rule_lint_findings missing after upgrade 0003 — "
                "migration 0003 did not run correctly."
            )
            idx_names = _index_names(engine, "rule_lint_findings")
            assert "idx_rule_lint_finding_rule" in idx_names, (
                "idx_rule_lint_finding_rule missing after upgrade 0003"
            )
            assert "idx_rule_lint_finding_code" in idx_names, (
                "idx_rule_lint_finding_code missing after upgrade 0003"
            )
        finally:
            engine.dispose()

    def test_drifted_sqlite_upgrade_is_idempotent(self, tmp_path):
        """Drifted DB: upgrade head is idempotent when 0003 artifacts exist.

        Reproduces the user's scenario:
          - ``alembic upgrade 0002`` brings the schema through 0002.
          - ``Base.metadata.create_all()`` then materialises the
            ``rule_lint_findings`` table + both 0003 indexes from the ORM
            (same effect as the historical create_all-fallback path).
          - ``alembic_version`` stays at 0002.

        ``alembic upgrade head`` must NOT raise
        ``OperationalError: table rule_lint_findings already exists`` and
        must reach head with the table + indexes still present.
        """
        from alembic import command
        from models import Base, RuleLintFinding  # noqa: F401  (registers table)

        db_url = f"sqlite:///{tmp_path / 'mig0003_drifted.db'}"
        cfg = _make_alembic_config(db_url)

        # 1. Bring schema through 0002.
        command.upgrade(cfg, "0002")

        engine = create_engine(db_url, future=True)
        try:
            # Sanity: table must NOT exist yet at 0002.
            assert not inspect(engine).has_table("rule_lint_findings"), (
                "rule_lint_findings already present at 0002 — test setup is wrong."
            )

            # 2. Simulate create_all() drift: build the rule_lint_findings
            # table from the ORM only. ``checkfirst=True`` (default) means
            # other tables created by 0001/0002 are not touched.
            RuleLintFinding.__table__.create(bind=engine, checkfirst=True)

            # Confirm both the table AND its declared indexes landed —
            # SQLAlchemy create() emits the indexes from __table_args__.
            assert inspect(engine).has_table("rule_lint_findings")
            idx_names = _index_names(engine, "rule_lint_findings")
            assert "idx_rule_lint_finding_rule" in idx_names
            assert "idx_rule_lint_finding_code" in idx_names

            # alembic_version still at 0002.
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == "0002", (
                f"Expected alembic_version=0002 after drift setup, got {row}"
            )
        finally:
            engine.dispose()

        # 3. Upgrade to head — must be idempotent.
        # Pre-fix this raised:
        #   sqlalchemy.exc.OperationalError: (sqlite3.OperationalError)
        #   table rule_lint_findings already exists
        command.upgrade(cfg, "head")

        # 4. Assert head was reached and table/indexes survived.
        engine = create_engine(db_url, future=True)
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == database.get_alembic_head_revision(), (
                f"Expected head revision after idempotent upgrade, got {row}"
            )
            assert inspect(engine).has_table("rule_lint_findings"), (
                "rule_lint_findings dropped during idempotent upgrade — "
                "migration 0003 should be a no-op when the table exists."
            )
            idx_names = _index_names(engine, "rule_lint_findings")
            assert "idx_rule_lint_finding_rule" in idx_names
            assert "idx_rule_lint_finding_code" in idx_names
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# Migration 0004 — journal_entries index idempotency (bd-ax3uj)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMigration0004:
    """Regression lock for bd-ax3uj fix in migration 0004.

    Same disease vector as 0003: ``JournalEntry.__table_args__`` declares
    ``idx_journal_batch_id`` and ``idx_journal_entity`` in ``models.py``,
    so ``Base.metadata.create_all()`` materialises both indexes on the
    baseline ``journal_entries`` table. Migration 0004 then re-creates
    them and SQLite raises ``index ... already exists``.

    The 0004 fix inspects existing indexes and skips any already present.
    """

    def test_drifted_sqlite_upgrade_is_idempotent(self, tmp_path):
        """Drifted DB: upgrade head is idempotent when 0004 indexes exist.

        Setup: upgrade through 0003, then manually CREATE INDEX both 0004
        indexes (matching what create_all() does), leave alembic_version at
        0003, run upgrade head — must not raise "index already exists".
        """
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0004_drifted.db'}"
        cfg = _make_alembic_config(db_url)

        # 1. Upgrade through 0003.
        command.upgrade(cfg, "0003")

        engine = create_engine(db_url, future=True)
        try:
            existing = _index_names(engine, "journal_entries")
            assert "idx_journal_batch_id" not in existing, (
                "idx_journal_batch_id already present after 0003 — test setup wrong."
            )
            assert "idx_journal_entity" not in existing, (
                "idx_journal_entity already present after 0003 — test setup wrong."
            )

            # 2. Inject both indexes via raw SQL — matches what
            # create_all() emits from JournalEntry.__table_args__.
            with engine.begin() as conn:
                conn.execute(text(
                    "CREATE INDEX idx_journal_batch_id "
                    "ON journal_entries (batch_id)"
                ))
                conn.execute(text(
                    "CREATE INDEX idx_journal_entity "
                    "ON journal_entries (category, entity_id, timestamp DESC)"
                ))

            # alembic_version still at 0003.
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == "0003", (
                f"Expected alembic_version=0003 after drift setup, got {row}"
            )
        finally:
            engine.dispose()

        # 3. Upgrade to head — must be idempotent.
        # Pre-fix this raised:
        #   sqlalchemy.exc.OperationalError: (sqlite3.OperationalError)
        #   index idx_journal_batch_id already exists
        command.upgrade(cfg, "head")

        # 4. Both indexes present, alembic at head.
        engine = create_engine(db_url, future=True)
        try:
            existing = _index_names(engine, "journal_entries")
            assert "idx_journal_batch_id" in existing
            assert "idx_journal_entity" in existing
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == database.get_alembic_head_revision()
        finally:
            engine.dispose()


@pytest.mark.integration
class TestMigration0005:
    """Regression lock for PR #229 / bead m2k7p fixes in migration 0005.

    Two paths are exercised:

    1. **Fresh SQLite** — ``alembic upgrade head`` on a brand-new empty database
       must complete without raising ``CircularDependencyError`` from SQLite's
       ``batch_alter_table``.  The fix splits the two ``add_column`` calls into
       separate ``batch_alter_table`` contexts; one context per column prevents
       the circular-dependency in SQLAlchemy's column-reordering logic.

    2. **Drifted SQLite** — simulates a long-running install where
       ``database._run_migrations`` (the ``create_all()`` fallback path) had
       already added ``quality_tie_break_order`` and
       ``quality_m3u_tie_break_enabled`` to ``auto_creation_rules`` while
       ``alembic_version`` was still stamped at ``0004``.  ``alembic upgrade
       head`` must be idempotent — it must NOT raise a "duplicate column" error,
       and must stamp ``0005`` (and continue to head if 0005 is not yet head).
    """

    def test_fresh_sqlite_upgrade_head_completes(self, tmp_path):
        """Fresh empty DB: ``alembic upgrade head`` must reach 0005 without error.

        Specifically guards against ``CircularDependencyError`` in
        ``_adjust_self_columns_for_partial_reordering`` when both
        ``quality_tie_break_order`` and ``quality_m3u_tie_break_enabled`` are
        added inside a single ``batch_alter_table`` context (the pre-fix state).
        """
        from alembic import command

        db_file = tmp_path / "migration0005_fresh.db"
        db_url = f"sqlite:///{db_file}"
        cfg = _make_alembic_config(db_url)

        # Upgrade must complete without any exception.  If the CircularDependency
        # bug is present this will raise before reaching 0005.
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            # Both columns must exist in auto_creation_rules after 0005.
            col_names = _column_names(engine, "auto_creation_rules")
            assert "quality_tie_break_order" in col_names, (
                "quality_tie_break_order missing from auto_creation_rules after "
                "upgrade head — migration 0005 did not run correctly."
            )
            assert "quality_m3u_tie_break_enabled" in col_names, (
                "quality_m3u_tie_break_enabled missing from auto_creation_rules after "
                "upgrade head — migration 0005 did not run correctly."
            )

            # alembic_version must be at head.
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None, "alembic_version row missing after upgrade head"
            assert row[0] == database.get_alembic_head_revision(), (
                f"Expected head revision {database.get_alembic_head_revision()!r}, "
                f"got {row[0]!r}"
            )
        finally:
            engine.dispose()

    def test_drifted_sqlite_upgrade_is_idempotent(self, tmp_path):
        """Drifted DB: upgrade head is idempotent when 0005 columns already exist.

        Reproduces the scenario where ``database._run_migrations`` (the
        ``create_all()`` / pre-Alembic fallback) had already added the two
        tie-break columns to ``auto_creation_rules`` while ``alembic_version``
        remained at ``0004``.

        Setup:
          - Run ``alembic upgrade 0004`` to populate the schema through
            revision 0004 (all real tables, all columns up to but not including
            those added by 0005).
          - Manually ``ALTER TABLE auto_creation_rules ADD COLUMN ...`` to inject
            the two 0005 columns — exactly as ``_run_migrations`` would have done
            via direct SQL (``VARCHAR(4) DEFAULT 'desc'`` and
            ``BOOLEAN DEFAULT 1 NOT NULL``).
          - Leave ``alembic_version`` at ``0004`` (do NOT stamp).

        Assertion:
          - ``alembic upgrade head`` must complete without raising
            ``OperationalError: duplicate column name``.
          - After upgrade, ``alembic_version`` must be at head (0005 was applied
            idempotently — it detected the columns were already present and
            skipped the adds).
          - The columns must have been present throughout (not dropped and
            re-added), validated by checking their values survive the upgrade.
        """
        from alembic import command

        db_file = tmp_path / "migration0005_drifted.db"
        db_url = f"sqlite:///{db_file}"
        cfg = _make_alembic_config(db_url)

        # ---- 1. Bring the schema up through revision 0004 ----
        command.upgrade(cfg, "0004")

        engine = create_engine(db_url, future=True)
        try:
            # Sanity: 0005 columns must NOT exist yet (they come in 0005).
            col_names = _column_names(engine, "auto_creation_rules")
            assert "quality_tie_break_order" not in col_names, (
                "quality_tie_break_order already present after 0004 — "
                "test setup assumption is wrong."
            )
            assert "quality_m3u_tie_break_enabled" not in col_names, (
                "quality_m3u_tie_break_enabled already present after 0004 — "
                "test setup assumption is wrong."
            )

            # ---- 2. Simulate create_all() drift: inject the columns via raw
            # SQL exactly as database._run_migrations would have done. ----
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE auto_creation_rules "
                    "ADD COLUMN quality_tie_break_order VARCHAR(4) DEFAULT 'desc'"
                ))
                conn.execute(text(
                    "ALTER TABLE auto_creation_rules "
                    "ADD COLUMN quality_m3u_tie_break_enabled BOOLEAN DEFAULT 1 NOT NULL"
                ))

                # Insert a minimal valid row to prove the column values survive
                # the upgrade unmodified.  All NOT NULL columns without
                # server defaults must be supplied; see baseline migration 0001
                # for the full constraint list.
                conn.execute(text(
                    "INSERT INTO auto_creation_rules "
                    "(name, enabled, priority, conditions, actions, "
                    " run_on_refresh, stop_on_first_match, probe_on_sort, "
                    " skip_struck_streams, orphan_action, created_at, "
                    " updated_at, match_scope_target_group, "
                    " quality_tie_break_order, quality_m3u_tie_break_enabled) "
                    "VALUES ('drift-test-rule', 1, 0, '[]', '[]', "
                    "        0, 0, 0, 0, 'orphan', "
                    "        '2026-01-01T00:00:00', '2026-01-01T00:00:00', 0, "
                    "        'asc', 0)"
                ))

            # Verify alembic_version is still at 0004 (not yet 0005).
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == "0004", (
                f"Expected alembic_version=0004 after drift setup, got {row}"
            )

        finally:
            engine.dispose()

        # ---- 3. Upgrade to head — must be idempotent (no OperationalError) ----
        # If the idempotency fix is missing, SQLite raises:
        #   sqlalchemy.exc.OperationalError: (sqlite3.OperationalError)
        #   duplicate column name: quality_tie_break_order
        command.upgrade(cfg, "head")

        # ---- 4. Assert 0005 was stamped and columns are still present ----
        engine = create_engine(db_url, future=True)
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None, "alembic_version row missing after upgrade head"
            assert row[0] == database.get_alembic_head_revision(), (
                f"Expected head revision {database.get_alembic_head_revision()!r} "
                f"after idempotent upgrade, got {row[0]!r}"
            )

            col_names = _column_names(engine, "auto_creation_rules")
            assert "quality_tie_break_order" in col_names, (
                "quality_tie_break_order missing after idempotent upgrade — "
                "migration 0005 may have dropped and failed to re-add the column."
            )
            assert "quality_m3u_tie_break_enabled" in col_names, (
                "quality_m3u_tie_break_enabled missing after idempotent upgrade — "
                "migration 0005 may have dropped and failed to re-add the column."
            )

            # The pre-existing row's values must be preserved: the upgrade must
            # not wipe data by dropping and re-adding the column.
            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT quality_tie_break_order, quality_m3u_tie_break_enabled "
                        "FROM auto_creation_rules WHERE name='drift-test-rule'"
                    )
                ).fetchone()
            assert row is not None, (
                "drift-test-rule row missing after upgrade — "
                "migration 0005 may have dropped the table or truncated data."
            )
            assert row[0] == "asc", (
                f"quality_tie_break_order changed across idempotent upgrade: "
                f"expected 'asc', got {row[0]!r}"
            )
            # SQLite stores BOOLEAN as integer; 0 == False.
            assert int(row[1]) == 0, (
                f"quality_m3u_tie_break_enabled changed across idempotent upgrade: "
                f"expected 0, got {row[1]!r}"
            )
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# Migration 0008 — channel_watch_stats_v view up/down (bd-skqln.3 step (b))
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMigration0008:
    """Up/down round-trip for the ``channel_watch_stats_v`` SQL view.

    The view itself has no row data — it is a saved query over
    ``session_telemetry``. The semantic-equivalence regression test
    (matched-data view-vs-legacy comparison) lives in
    ``test_channel_watch_stats_view.py``; this class is the smoke test
    that the view exists at head, vanishes at the prior revision, and
    survives a round-trip with identical DDL.

    Bead: ``enhancedchannelmanager-skqln.3`` step (b).
    """

    VIEW_NAME = "channel_watch_stats_v"

    def _view_sql(self, engine) -> str | None:
        """Return the stored ``CREATE VIEW`` text for the view, or None."""
        with engine.connect() as conn:
            return conn.execute(text(
                "SELECT sql FROM sqlite_master "
                f"WHERE type='view' AND name='{self.VIEW_NAME}'"
            )).scalar()

    def test_view_exists_at_head(self, tmp_path):
        """``alembic upgrade head`` creates the view."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0008_head.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            assert self._view_sql(engine) is not None, (
                f"View {self.VIEW_NAME} missing after upgrade head — "
                "migration 0008 did not run correctly."
            )
        finally:
            engine.dispose()

    def test_view_gone_at_prior_revision(self, tmp_path):
        """Downgrading to 0007 drops the view."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0008_down.db'}"
        cfg = _make_alembic_config(db_url)

        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0007")

        engine = create_engine(db_url, future=True)
        try:
            assert self._view_sql(engine) is None, (
                f"View {self.VIEW_NAME} still present after downgrade to 0007 — "
                "migration 0008's downgrade() did not drop it."
            )
        finally:
            engine.dispose()

    def test_view_round_trip_ddl_is_stable(self, tmp_path):
        """Down → up cycle produces byte-identical ``CREATE VIEW`` SQL.

        SQLite preserves the exact CREATE statement in ``sqlite_master.sql``
        — if the migration recreates the view with semantically-identical
        but textually-different DDL, this test catches that drift.
        """
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0008_roundtrip.db'}"
        cfg = _make_alembic_config(db_url)

        command.upgrade(cfg, "head")
        engine = create_engine(db_url, future=True)
        try:
            pre_sql = self._view_sql(engine)
            assert pre_sql is not None
        finally:
            engine.dispose()

        command.downgrade(cfg, "0007")
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            post_sql = self._view_sql(engine)
            assert post_sql == pre_sql, (
                "channel_watch_stats_v DDL changed across down/up cycle:\n"
                f"  before: {pre_sql!r}\n  after:  {post_sql!r}"
            )
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# Migration 0010 — session_telemetry.stream_id + stream_name (bd-kh23e)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMigration0010:
    """Up/down round-trip for the ``session_telemetry.stream_id`` /
    ``stream_name`` column additions (bead ``enhancedchannelmanager-kh23e``).

    These two columns capture the active stream identity per poll row, so
    the read APIs (skqln.5 / skqln.16) can surface "which stream within the
    channel" on the UI — see PO directive 2026-05-14. Both are NULLABLE
    (consistent with ``provider_id`` design — resolution failures land NULL).

    Bead: ``enhancedchannelmanager-kh23e``.
    """

    TABLE = "session_telemetry"
    NEW_COLUMNS = {"stream_id", "stream_name"}

    def _column_map(self, engine) -> dict[str, dict]:
        """Return {column_name: SQLAlchemy inspector dict} for the table."""
        return {c["name"]: c for c in inspect(engine).get_columns(self.TABLE)}

    def test_columns_present_at_head(self, tmp_path):
        """``alembic upgrade head`` adds ``stream_id`` + ``stream_name``."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0010_head.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            cols = self._column_map(engine)
            for col in self.NEW_COLUMNS:
                assert col in cols, (
                    f"Column {col} missing on {self.TABLE} after upgrade head — "
                    "migration 0010 did not run correctly."
                )
            # stream_id is INTEGER NULL (no FK — consistent with provider_id
            # per skqln.2 docstring).
            assert "INT" in str(cols["stream_id"]["type"]).upper(), cols["stream_id"]["type"]
            assert cols["stream_id"]["nullable"] is True
            # stream_name is TEXT NULL.
            assert "TEXT" in str(cols["stream_name"]["type"]).upper(), cols["stream_name"]["type"]
            assert cols["stream_name"]["nullable"] is True
        finally:
            engine.dispose()

    def test_columns_gone_at_prior_revision(self, tmp_path):
        """Downgrading to 0009 drops both columns."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0010_down.db'}"
        cfg = _make_alembic_config(db_url)

        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0009")

        engine = create_engine(db_url, future=True)
        try:
            cols = self._column_map(engine)
            for col in self.NEW_COLUMNS:
                assert col not in cols, (
                    f"Column {col} still present after downgrade to 0009 — "
                    "migration 0010's downgrade() did not drop it."
                )
        finally:
            engine.dispose()

    def test_columns_round_trip_up_down_up(self, tmp_path):
        """Down → up cycle leaves the same columns present with the same types."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0010_roundtrip.db'}"
        cfg = _make_alembic_config(db_url)

        command.upgrade(cfg, "head")
        engine = create_engine(db_url, future=True)
        try:
            pre_cols = self._column_map(engine)
        finally:
            engine.dispose()

        command.downgrade(cfg, "0009")
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            post_cols = self._column_map(engine)
            for col in self.NEW_COLUMNS:
                assert col in post_cols, f"{col} missing after round-trip"
                # Types stay equivalent across the cycle.
                assert str(post_cols[col]["type"]) == str(pre_cols[col]["type"]), (
                    f"{col} type drifted: before={pre_cols[col]['type']!r} "
                    f"after={post_cols[col]['type']!r}"
                )
                assert post_cols[col]["nullable"] == pre_cols[col]["nullable"]
        finally:
            engine.dispose()
