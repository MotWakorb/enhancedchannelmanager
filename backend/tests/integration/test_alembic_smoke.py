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


# ---------------------------------------------------------------------------
# Migrations 0006-0010 — bd-5w6jz idempotency regression tests
# ---------------------------------------------------------------------------
#
# Same disease vector as bd-ax3uj (which fixed 0003/0004): on a long-running
# install ``init_db``'s ``Base.metadata.create_all()`` materialises ORM-
# declared tables/columns/indexes from ``models.py`` ahead of the migrations.
# Post-bd-zaaey the bootstrap loud-fails on ``OperationalError: ... already
# exists`` so any unguarded ``op.create_*`` / ``op.add_column`` aborts
# startup. Each class below reproduces the drift state for one migration and
# asserts ``alembic upgrade head`` is idempotent.


@pytest.mark.integration
class TestMigration0006Idempotent:
    """Regression lock for bd-5w6jz fix in migration 0006.

    User reported ``sqlite3.OperationalError: table session_telemetry
    already exists`` during ``Running upgrade 0005 -> 0006``. Container
    startup aborted (post-bd-zaaey, bootstrap loud-fails). Root cause is the
    bd-ax3uj class on a different migration: ``SessionTelemetry`` and its 5
    indexes are declared on the ORM model in ``models.py``, so a long-
    running install where ``create_all()`` ran before Alembic caught up has
    the table physically present while ``alembic_version`` lagged at 0005.
    """

    TABLE = "session_telemetry"
    INDEXES = (
        "idx_session_telemetry_observed_at",
        "idx_session_telemetry_user_observed",
        "idx_session_telemetry_provider_observed",
        "idx_session_telemetry_session_id",
        "idx_session_telemetry_provider_channel_observed_bytes",
    )

    def test_fresh_sqlite_upgrade_through_0006(self, tmp_path):
        """Fresh empty DB: ``alembic upgrade 0006`` creates the table + 5 indexes."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0006_fresh.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0006")

        engine = create_engine(db_url, future=True)
        try:
            assert inspect(engine).has_table(self.TABLE), (
                f"{self.TABLE} missing after upgrade 0006 — "
                "migration 0006 did not run correctly."
            )
            idx_names = _index_names(engine, self.TABLE)
            for idx in self.INDEXES:
                assert idx in idx_names, (
                    f"{idx} missing after upgrade 0006 — "
                    "migration 0006 did not run correctly."
                )
        finally:
            engine.dispose()

    def test_drifted_sqlite_upgrade_is_idempotent(self, tmp_path):
        """Drifted DB: upgrade head is idempotent when 0006 artifacts exist.

        Reproduces the user's scenario:
          - ``alembic upgrade 0005`` brings the schema through 0005.
          - ``Base.metadata.create_all(tables=[SessionTelemetry.__table__])``
            materialises ``session_telemetry`` + all 5 indexes from the ORM.
          - ``alembic_version`` stays at 0005.

        ``alembic upgrade head`` must NOT raise
        ``OperationalError: table session_telemetry already exists`` and
        must reach head with the table + indexes still present.
        """
        from alembic import command
        from models import SessionTelemetry  # noqa: F401  (registers table)

        db_url = f"sqlite:///{tmp_path / 'mig0006_drifted.db'}"
        cfg = _make_alembic_config(db_url)

        command.upgrade(cfg, "0005")

        engine = create_engine(db_url, future=True)
        try:
            assert not inspect(engine).has_table(self.TABLE), (
                f"{self.TABLE} already present at 0005 — test setup is wrong."
            )

            # Materialise the table + its indexes from the ORM. ``checkfirst``
            # default keeps other tables created by 0001-0005 untouched.
            SessionTelemetry.__table__.create(bind=engine, checkfirst=True)

            assert inspect(engine).has_table(self.TABLE)
            idx_names = _index_names(engine, self.TABLE)
            for idx in self.INDEXES:
                assert idx in idx_names

            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == "0005", (
                f"Expected alembic_version=0005 after drift setup, got {row}"
            )
        finally:
            engine.dispose()

        # Pre-fix this raised:
        #   sqlalchemy.exc.OperationalError: (sqlite3.OperationalError)
        #   table session_telemetry already exists
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == database.get_alembic_head_revision()
            assert inspect(engine).has_table(self.TABLE)
            idx_names = _index_names(engine, self.TABLE)
            for idx in self.INDEXES:
                assert idx in idx_names
        finally:
            engine.dispose()


@pytest.mark.integration
class TestMigration0007Idempotent:
    """Regression lock for bd-5w6jz fix in migration 0007.

    Migration 0007 alters ``session_telemetry.channel_id`` from INTEGER NULL
    to VARCHAR(64) NOT NULL. On a drift install where ``create_all()``
    materialised the post-0007 ORM shape (``String(64) NOT NULL``) ahead of
    Alembic, the alter is unnecessary — and worse, in some SQLite batch-mode
    rebuild paths an alter that targets the column's existing shape can
    still trigger an expensive table-copy (or, with bad luck and a partially
    drifted FK / check-constraint, fail outright).

    The fix inspects ``channel_id`` and skips the alter if the column is
    already at the target shape.
    """

    def test_drifted_sqlite_upgrade_is_idempotent(self, tmp_path):
        """Drifted DB: 0007 is a no-op when channel_id is already String(64) NOT NULL.

        Setup:
          - upgrade through 0006 (creates session_telemetry with
            channel_id INTEGER NULL).
          - drop the table and recreate it with the post-0007 shape
            (channel_id VARCHAR(64) NOT NULL) — same effect as create_all()
            running against the post-0007 ORM model on a drift install.
          - leave alembic_version at 0006.

        ``alembic upgrade head`` must reach head without raising. Verify
        ``channel_id`` is still the target type after upgrade.
        """
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0007_drifted.db'}"
        cfg = _make_alembic_config(db_url)

        command.upgrade(cfg, "0006")

        engine = create_engine(db_url, future=True)
        try:
            with engine.begin() as conn:
                # Drop and recreate session_telemetry with the post-0007
                # ORM shape directly. SQLite has no "DROP COLUMN" before
                # recent versions; we drop the whole table since 0006 just
                # created it and there's no row data to preserve.
                conn.execute(text("DROP TABLE session_telemetry"))
                conn.execute(text(
                    "CREATE TABLE session_telemetry ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "session_id TEXT NOT NULL, "
                    "observed_at INTEGER NOT NULL, "
                    "user_id INTEGER, "
                    "provider_id INTEGER, "
                    "channel_id VARCHAR(64) NOT NULL, "
                    "bytes_delta BIGINT NOT NULL, "
                    "buffer_event_count INTEGER NOT NULL DEFAULT 0, "
                    "poll_interval_ms INTEGER NOT NULL, "
                    "CONSTRAINT ck_session_telemetry_bytes_delta_non_negative "
                    "CHECK (bytes_delta >= 0), "
                    "FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL"
                    ")"
                ))

            # Sanity: channel_id is already VARCHAR(64) NOT NULL.
            cols = {c["name"]: c for c in inspect(engine).get_columns("session_telemetry")}
            assert "VARCHAR" in str(cols["channel_id"]["type"]).upper() or \
                   "STRING" in str(cols["channel_id"]["type"]).upper(), (
                f"channel_id type unexpected: {cols['channel_id']['type']!r}"
            )
            assert cols["channel_id"]["nullable"] is False
        finally:
            engine.dispose()

        # Pre-fix this would either raise (CHECK constraint name conflict on
        # SQLite versions that derive batch-mode names from existing DDL) or
        # silently incur a full table-copy. With the bd-5w6jz guard in place
        # 0007 is a no-op.
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            cols = {c["name"]: c for c in inspect(engine).get_columns("session_telemetry")}
            # Column still at the target shape after upgrade — both the
            # NOT NULL constraint AND the VARCHAR(64) type. A regression that
            # silently changed the column type (e.g. degraded to TEXT or to
            # a different length) would slip past a nullability-only check.
            assert cols["channel_id"]["nullable"] is False
            channel_id_type = cols["channel_id"]["type"]
            type_str = str(channel_id_type).upper()
            assert "VARCHAR" in type_str or "STRING" in type_str, (
                f"channel_id type changed shape after upgrade: {channel_id_type!r}"
            )
            assert getattr(channel_id_type, "length", None) == 64, (
                f"channel_id length changed after upgrade: "
                f"{getattr(channel_id_type, 'length', None)!r}"
            )
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == database.get_alembic_head_revision()
        finally:
            engine.dispose()


@pytest.mark.integration
class TestMigration0009Idempotent:
    """Regression lock for bd-5w6jz fix in migration 0009.

    Migration 0009 creates 3 rollup tables (``session_telemetry_user_daily``,
    ``session_telemetry_provider_daily``, ``telemetry_rollup_state``) plus 3
    indexes. All three tables are declared on ORM models in ``models.py``, so
    a long-running install with ``create_all()`` drift has them physically
    present while ``alembic_version`` lagged at 0008.
    """

    TABLES = (
        "session_telemetry_user_daily",
        "session_telemetry_provider_daily",
        "telemetry_rollup_state",
    )
    INDEXES = (
        ("session_telemetry_user_daily", "idx_session_telemetry_user_daily_day"),
        ("session_telemetry_provider_daily", "idx_session_telemetry_provider_daily_provider_day"),
        ("session_telemetry_provider_daily", "idx_session_telemetry_provider_daily_day"),
    )

    def test_drifted_sqlite_upgrade_is_idempotent(self, tmp_path):
        """Drifted DB: upgrade head is idempotent when all 3 rollup tables exist."""
        from alembic import command
        from models import (  # noqa: F401  (registers tables)
            SessionTelemetryUserDaily,
            SessionTelemetryProviderDaily,
            TelemetryRollupState,
        )

        db_url = f"sqlite:///{tmp_path / 'mig0009_drifted.db'}"
        cfg = _make_alembic_config(db_url)

        command.upgrade(cfg, "0008")

        engine = create_engine(db_url, future=True)
        try:
            for table in self.TABLES:
                assert not inspect(engine).has_table(table), (
                    f"{table} already present at 0008 — test setup is wrong."
                )

            # Materialise all three tables (and their indexes) from the ORM.
            SessionTelemetryUserDaily.__table__.create(bind=engine, checkfirst=True)
            SessionTelemetryProviderDaily.__table__.create(bind=engine, checkfirst=True)
            TelemetryRollupState.__table__.create(bind=engine, checkfirst=True)

            for table in self.TABLES:
                assert inspect(engine).has_table(table)
            for table, idx in self.INDEXES:
                assert idx in _index_names(engine, table)

            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == "0008"
        finally:
            engine.dispose()

        # Pre-fix this raised:
        #   sqlalchemy.exc.OperationalError: (sqlite3.OperationalError)
        #   table session_telemetry_user_daily already exists
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == database.get_alembic_head_revision()
            for table in self.TABLES:
                assert inspect(engine).has_table(table)
            for table, idx in self.INDEXES:
                assert idx in _index_names(engine, table)
        finally:
            engine.dispose()


@pytest.mark.integration
class TestMigration0010Idempotent:
    """Regression lock for bd-5w6jz fix in migration 0010.

    Migration 0010 adds ``stream_id`` and ``stream_name`` to
    ``session_telemetry``. Both columns are declared on the
    ``SessionTelemetry`` ORM model in ``models.py``, so a long-running
    install where ``create_all()`` materialised the post-0010 column shape
    ahead of Alembic has both columns physically present while
    ``alembic_version`` lagged at 0009.
    """

    NEW_COLUMNS = ("stream_id", "stream_name")

    def test_drifted_sqlite_upgrade_is_idempotent(self, tmp_path):
        """Drifted DB: 0010 is a no-op when stream_id + stream_name already exist."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0010_drifted.db'}"
        cfg = _make_alembic_config(db_url)

        command.upgrade(cfg, "0009")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            assert "stream_id" not in cols
            assert "stream_name" not in cols

            # Inject both columns via raw SQL — matches what create_all()
            # would emit from the ``SessionTelemetry`` ORM model.
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE session_telemetry ADD COLUMN stream_id INTEGER"
                ))
                conn.execute(text(
                    "ALTER TABLE session_telemetry ADD COLUMN stream_name TEXT"
                ))

            cols = _column_names(engine, "session_telemetry")
            assert "stream_id" in cols
            assert "stream_name" in cols

            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == "0009"
        finally:
            engine.dispose()

        # Pre-fix this raised:
        #   sqlalchemy.exc.OperationalError: (sqlite3.OperationalError)
        #   duplicate column name: stream_id
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            assert "stream_id" in cols
            assert "stream_name" in cols
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == database.get_alembic_head_revision()
        finally:
            engine.dispose()


@pytest.mark.integration
class TestMigration0008IfNotExists:
    """Regression lock for bd-5w6jz fix in migration 0008.

    Migration 0008 creates the ``channel_watch_stats_v`` SQL view.
    ``CREATE VIEW`` (without ``IF NOT EXISTS``) raises on a partial-rerun
    where the view was already committed by a prior aborted upgrade. The
    fix changes the DDL to ``CREATE VIEW IF NOT EXISTS``.
    """

    VIEW_NAME = "channel_watch_stats_v"

    def _view_exists(self, engine) -> bool:
        with engine.connect() as conn:
            return conn.execute(text(
                "SELECT 1 FROM sqlite_master "
                f"WHERE type='view' AND name='{self.VIEW_NAME}'"
            )).scalar() is not None

    def test_drifted_sqlite_upgrade_is_idempotent(self, tmp_path):
        """Drifted DB: 0008 is a no-op when the view already exists.

        Setup:
          - upgrade through 0007.
          - manually CREATE the view (matching what a partial-rerun
            committed before aborting on a later step).
          - leave alembic_version at 0007.

        ``alembic upgrade head`` must reach head without raising
        ``OperationalError: view channel_watch_stats_v already exists``.
        """
        import importlib.util

        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0008_drifted.db'}"
        cfg = _make_alembic_config(db_url)

        command.upgrade(cfg, "0007")

        engine = create_engine(db_url, future=True)
        try:
            assert not self._view_exists(engine)

            # Read the view DDL from the migration module itself — keeps
            # the test in lock-step with the source of truth (no risk of
            # the test SQL drifting from the migration's SQL).
            mig_path = (
                Path(database.__file__).resolve().parent
                / "alembic" / "versions"
                / "20260513_1130_0008_channel_watch_stats_v.py"
            )
            spec = importlib.util.spec_from_file_location("mig_0008", mig_path)
            mig_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mig_module)
            view_sql = mig_module.CHANNEL_WATCH_STATS_V_SQL

            with engine.begin() as conn:
                conn.execute(text(view_sql))
            assert self._view_exists(engine)

            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == "0007"
        finally:
            engine.dispose()

        # Pre-fix (without ``IF NOT EXISTS``) this raised:
        #   sqlalchemy.exc.OperationalError: (sqlite3.OperationalError)
        #   view channel_watch_stats_v already exists
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            assert self._view_exists(engine)
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == database.get_alembic_head_revision()
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# bd-5w6jz smart-bootstrap fast-path
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSmartBootstrapFastPath:
    """Regression lock for the bd-5w6jz smart-bootstrap fast-path.

    The strategic half of the bd-5w6jz fix: when ``alembic_version`` lags
    head but ``Base.metadata.create_all()`` has already materialised every
    table + column the model declares, ``_bootstrap_alembic`` stamps forward
    instead of running ``upgrade head``. This ends the whack-a-mole
    architecturally — the per-migration idempotency guards on 0003/0004 +
    0006-0010 cover the next-rev case, and the fast-path covers the steady-
    state case where every future migration has columns/tables/indexes
    declared on the ORM that ``create_all()`` will keep racing into the DB
    ahead of the migration timeline.

    Two key invariants:

    1. The fast-path MUST trigger when alembic lags head AND schema matches.
       Without it, the lone unguarded migration in any future release crashes
       startup again.

    2. The fast-path MUST NOT trigger on fresh installs (``current_rev`` is
       unset). A fresh DB needs the actual ``upgrade head`` to create
       everything; stamping at head without running migrations would leave a
       0-table install marked as up-to-date.
    """

    def test_fast_path_stamps_forward_when_schema_matches(self, tmp_path):
        """alembic at 0005 + schema fully materialised → stamp head, no upgrade run."""
        from unittest.mock import patch

        from alembic import command

        db_file = tmp_path / "fast_path.db"
        db_url = f"sqlite:///{db_file}"
        cfg = _make_alembic_config(db_url)

        # 1. Fresh upgrade partway through — alembic_version=0005.
        command.upgrade(cfg, "0005")

        # 2. Bring the DB to full head shape via Base.metadata.create_all() —
        # mirrors what init_db's create_all() does on a long-running install
        # where the ORM model has run ahead of the migration timeline.
        engine = create_engine(db_url, future=True)
        try:
            Base = database.Base
            Base.metadata.create_all(bind=engine)

            # Sanity: alembic_version is still at 0005 (create_all does not
            # touch the version row), but every model table is now present.
            with engine.connect() as conn:
                rev = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()[0]
            assert rev == "0005"

            head = database.get_alembic_head_revision()
            assert head != "0005", "test premise: head must be > 0005"

            # 3. Call _bootstrap_alembic. Patch alembic.command.upgrade so we
            # can assert the fast-path skipped it — if upgrade ran, the
            # patch's call_count would be >= 1.
            with patch("alembic.command.upgrade") as mock_upgrade:
                database._bootstrap_alembic(engine)

            # 4. Fast-path stamped to head; no upgrade was run.
            assert mock_upgrade.call_count == 0, (
                f"upgrade head was called {mock_upgrade.call_count}× — "
                "fast-path should have skipped it (schema already matches)."
            )

            with engine.connect() as conn:
                rev = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()[0]
            assert rev == head, (
                f"alembic_version not stamped to head after fast-path: {rev}"
            )
        finally:
            engine.dispose()

    def test_fast_path_does_not_trigger_on_fresh_install(self, tmp_path):
        """Empty DB (current_rev=None) → fast-path skipped, upgrade head runs.

        Guards the "stamping a fresh install at head leaves it 0-table"
        regression. The fast-path's ``current_rev and current_rev != head``
        guard must hold.
        """
        from unittest.mock import patch

        db_file = tmp_path / "fresh_install.db"
        db_url = f"sqlite:///{db_file}"
        engine = create_engine(db_url, future=True)
        try:
            # Spy on command.upgrade to verify it WAS called (the fresh path
            # needs it; the fast-path must not steal that call).
            with patch(
                "alembic.command.upgrade",
                wraps=__import__("alembic.command", fromlist=["upgrade"]).upgrade,
            ) as mock_upgrade:
                database._bootstrap_alembic(engine)

            assert mock_upgrade.call_count >= 1, (
                "upgrade head was not called on a fresh install — the "
                "fast-path incorrectly triggered when current_rev was None."
            )

            head = database.get_alembic_head_revision()
            with engine.connect() as conn:
                rev = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()[0]
            assert rev == head
            # Spot-check a head-revision artifact lands on disk.
            assert inspect(engine).has_table("session_telemetry"), (
                "session_telemetry missing on fresh install — fast-path "
                "stamped without running upgrade head."
            )
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# bd-gsn3r — migration 0011: dispatcharr_username + drop ECM users FK
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMigration0011:
    """Migration 0011 — denormalize Dispatcharr username + drop ECM users FK.

    Stats v2 architectural fix for the namespace-collision bug bd-uqbob
    worked around with an env-var exclude filter. Migration 0011 adds
    ``dispatcharr_username TEXT NULL`` to ``session_telemetry`` and drops
    the FK constraint on ``user_id`` → ``users.id``. Both changes ride
    in one migration because they touch the same column space and SQLite
    batch-mode rebuilds the entire table either way.

    Coverage:
      - Fresh upgrade through 0011 — column exists, FK is gone.
      - Fresh downgrade — column is gone, FK is back.
      - Idempotency on the column-add path (mirrors bd-5w6jz pattern):
        long-running install with ``create_all()`` already added the
        column from the post-0011 ORM model.
      - Idempotency on the FK-drop path: long-running install where the
        smart-bootstrap fast-path stamped through 0011 without dropping
        the FK; re-running the migration must not raise.
    """

    NEW_COLUMN = "dispatcharr_username"

    @staticmethod
    def _user_id_fk(engine) -> list[dict]:
        """Return the FK descriptors on ``session_telemetry.user_id``."""
        fks = inspect(engine).get_foreign_keys("session_telemetry")
        return [
            fk
            for fk in fks
            if "user_id" in (fk.get("constrained_columns") or [])
        ]

    def test_fresh_sqlite_upgrade_through_0011(self, tmp_path):
        """Fresh DB: ``alembic upgrade 0011`` adds the column and drops the FK."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0011_fresh.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0011")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            assert self.NEW_COLUMN in cols, (
                f"{self.NEW_COLUMN} missing after upgrade 0011"
            )
            assert self._user_id_fk(engine) == [], (
                "session_telemetry.user_id still has a FK after upgrade 0011 — "
                "the namespace-collision fix did not drop the FK as expected."
            )
        finally:
            engine.dispose()

    def test_fresh_sqlite_downgrade_from_0011(self, tmp_path):
        """Downgrade 0011 -> 0010: column gone, FK restored."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0011_downgrade.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0011")

        engine = create_engine(db_url, future=True)
        try:
            assert self.NEW_COLUMN in _column_names(engine, "session_telemetry")
            assert self._user_id_fk(engine) == []
        finally:
            engine.dispose()

        command.downgrade(cfg, "0010")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            assert self.NEW_COLUMN not in cols, (
                f"{self.NEW_COLUMN} still present after downgrade — "
                "0011's downgrade() did not drop the column."
            )
            fks = self._user_id_fk(engine)
            assert len(fks) == 1, (
                "Downgrade did not restore the user_id FK — "
                f"expected exactly one FK on user_id, got {fks!r}."
            )
            # Also verify the FK target is users.id (not some other
            # accidental reference).
            assert fks[0].get("referred_table") == "users"
        finally:
            engine.dispose()


@pytest.mark.integration
class TestMigration0011Idempotent:
    """Regression lock for bd-5w6jz idempotency on migration 0011.

    Two drift scenarios:

    1. ``dispatcharr_username`` already added by ``create_all()`` from the
       post-0011 ORM model (long-running install, alembic_version still at
       0010). The column-add half must skip rather than raise
       ``OperationalError: duplicate column name: dispatcharr_username``.

    2. The smart-bootstrap fast-path stamped past 0011 without ever
       running the FK drop (because every model column was already
       present and ``_schema_matches_head`` only checks columns, not
       constraints). A subsequent forced re-run of 0011 must skip the
       column-add but still drop the FK. Conversely if 0011 is re-run
       on a DB where the FK is already absent (e.g. from a previous
       successful run that the alembic_version row didn't capture due
       to a crash mid-stamp), the FK-drop half must skip.

    The migration is structured so that re-running it is safe in any of
    these states — column add and FK drop are independently guarded.
    """

    NEW_COLUMN = "dispatcharr_username"

    def test_drifted_column_already_added(self, tmp_path):
        """create_all()-style drift: column already present pre-0011."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0011_col_drift.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0010")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            assert self.NEW_COLUMN not in cols, (
                "test setup is wrong — dispatcharr_username already at 0010"
            )
            # Inject the column via raw SQL — matches what create_all()
            # would emit from the post-0011 ORM model.
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE session_telemetry ADD COLUMN "
                    "dispatcharr_username TEXT"
                ))
            assert self.NEW_COLUMN in _column_names(engine, "session_telemetry")
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == "0010"
        finally:
            engine.dispose()

        # Pre-fix this would raise:
        #   OperationalError: duplicate column name: dispatcharr_username
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            assert self.NEW_COLUMN in _column_names(engine, "session_telemetry")
            # Even on the drift path the FK must be dropped — that's the
            # second half of the migration and it doesn't depend on the
            # column-add half.
            fks = inspect(engine).get_foreign_keys("session_telemetry")
            user_id_fks = [
                fk
                for fk in fks
                if "user_id" in (fk.get("constrained_columns") or [])
            ]
            assert user_id_fks == [], (
                "user_id FK still present after upgrade head — the FK-drop "
                "half must run even when the column-add half is a no-op."
            )
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == database.get_alembic_head_revision()
        finally:
            engine.dispose()

    def test_drifted_fk_already_dropped(self, tmp_path):
        """Re-running 0011 against a DB where the FK is already absent.

        Reproduces the smart-bootstrap fast-path stamping past 0011 and
        then a subsequent forced re-run of the migration. The migration
        must skip both halves cleanly without raising.
        """
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0011_fk_drift.db'}"
        cfg = _make_alembic_config(db_url)
        # Run all the way through 0011 to drop the FK + add the column.
        command.upgrade(cfg, "0011")

        engine = create_engine(db_url, future=True)
        try:
            assert self.NEW_COLUMN in _column_names(engine, "session_telemetry")
            fks = inspect(engine).get_foreign_keys("session_telemetry")
            user_id_fks = [
                fk
                for fk in fks
                if "user_id" in (fk.get("constrained_columns") or [])
            ]
            assert user_id_fks == [], "test setup is wrong — FK still present"
            # Roll the alembic_version row back to 0010 to simulate the
            # version-row-out-of-sync-with-schema state. Schema stays as
            # it is (column present, FK absent).
            with engine.begin() as conn:
                conn.execute(text(
                    "UPDATE alembic_version SET version_num = '0010'"
                ))
        finally:
            engine.dispose()

        # Re-run the migration. Both halves must no-op cleanly.
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            assert self.NEW_COLUMN in _column_names(engine, "session_telemetry")
            fks = inspect(engine).get_foreign_keys("session_telemetry")
            user_id_fks = [
                fk
                for fk in fks
                if "user_id" in (fk.get("constrained_columns") or [])
            ]
            assert user_id_fks == [], (
                "FK reappeared on re-run of 0011 — the FK-drop guard "
                "should have detected the FK is already absent and "
                "skipped the batch rebuild."
            )
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == database.get_alembic_head_revision()
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# Migration 0012 — task_schedules CHECK ck_task_schedules_interval_positive (bd-lbkck)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMigration0012:
    """Migration 0012 — CHECK constraint preventing task_schedules interval/0.

    Defense in depth for the bd-p5b8i scheduling subsystem regression.
    The placeholder bug Bundle H (bd-1weac) fixes wrote ``task_schedules``
    rows with ``schedule_type='interval'`` and ``interval_seconds=0``/``NULL``;
    this migration adds ``ck_task_schedules_interval_positive`` so SQLite
    rejects the bad shape at write time and the bug cannot recur via
    backup-restore or a future code path.

    Coverage:
      - Fresh upgrade through 0012 — constraint exists in the DDL and
        rejects interval/0 INSERTs with IntegrityError.
      - Drift (pre-existing interval/0 rows): the migration's pre-flight
        DELETE repairs the violating rows before the constraint is
        added, so the batch rebuild doesn't fail with IntegrityError on
        bad data.
      - Idempotency (bd-5w6jz pattern): re-running the migration is a
        no-op when the constraint is already present.
    """

    CONSTRAINT_NAME = "ck_task_schedules_interval_positive"

    @staticmethod
    def _constraint_in_ddl(engine) -> bool:
        """True if the constraint name appears in task_schedules' DDL."""
        with engine.connect() as conn:
            ddl = conn.execute(text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='task_schedules'"
            )).scalar()
        return "ck_task_schedules_interval_positive" in (ddl or "")

    def test_fresh_sqlite_upgrade_through_0012(self, tmp_path):
        """Fresh DB: ``alembic upgrade 0012`` adds the constraint."""
        from alembic import command
        from sqlalchemy.exc import IntegrityError

        db_url = f"sqlite:///{tmp_path / 'mig0012_fresh.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0012")

        engine = create_engine(db_url, future=True)
        try:
            assert self._constraint_in_ddl(engine), (
                "ck_task_schedules_interval_positive missing from DDL "
                "after upgrade 0012 — migration did not add the constraint."
            )
            # SQLite must enforce the constraint at write time. We use a
            # raw connection to bypass ORM defaults and inject the
            # placeholder shape directly.
            with engine.begin() as conn:
                with pytest.raises(IntegrityError):
                    conn.execute(text(
                        "INSERT INTO task_schedules "
                        "(task_id, enabled, schedule_type, interval_seconds, "
                        " timezone, created_at, updated_at) "
                        "VALUES ('cleanup', 1, 'interval', 0, 'UTC', "
                        " '2026-05-15 19:00:00', '2026-05-15 19:00:00')"
                    ))
            # NULL interval_seconds with interval type also blocked.
            with engine.begin() as conn:
                with pytest.raises(IntegrityError):
                    conn.execute(text(
                        "INSERT INTO task_schedules "
                        "(task_id, enabled, schedule_type, interval_seconds, "
                        " timezone, created_at, updated_at) "
                        "VALUES ('cleanup', 1, 'interval', NULL, 'UTC', "
                        " '2026-05-15 19:00:00', '2026-05-15 19:00:00')"
                    ))
            # daily/weekly rows with NULL interval_seconds MUST succeed —
            # the constraint scopes the check to schedule_type='interval'.
            with engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO task_schedules "
                    "(task_id, enabled, schedule_type, interval_seconds, "
                    " schedule_time, timezone, created_at, updated_at) "
                    "VALUES ('cleanup', 1, 'daily', NULL, '02:00', 'UTC', "
                    " '2026-05-15 19:00:00', '2026-05-15 19:00:00')"
                ))
            # interval rows with positive interval_seconds MUST succeed.
            with engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO task_schedules "
                    "(task_id, enabled, schedule_type, interval_seconds, "
                    " timezone, created_at, updated_at) "
                    "VALUES ('cleanup', 1, 'interval', 3600, 'UTC', "
                    " '2026-05-15 19:00:00', '2026-05-15 19:00:00')"
                ))
        finally:
            engine.dispose()

    def test_drifted_preexisting_interval_zero_row_is_repaired(self, tmp_path):
        """Drift path: pre-existing interval/0 rows repaired pre-add.

        Reproduces the bd-p5b8i scenario where the placeholder bug Bundle H
        is fixing has already written the bad shape into the DB. The
        migration's pre-flight DELETE must remove the violating rows before
        the constraint is added; otherwise the batch rebuild fails with
        IntegrityError on the copy-rows step.
        """
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0012_drift.db'}"
        cfg = _make_alembic_config(db_url)
        # Upgrade to 0011 — task_schedules exists, no constraint yet.
        command.upgrade(cfg, "0011")

        engine = create_engine(db_url, future=True)
        try:
            assert not self._constraint_in_ddl(engine), (
                "test setup is wrong — constraint already present at 0011"
            )
            # Inject the placeholder bug rows directly via raw SQL —
            # matches what the pre-Bundle-H code wrote.
            with engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO task_schedules "
                    "(task_id, enabled, schedule_type, interval_seconds, "
                    " timezone, created_at, updated_at) "
                    "VALUES ('cleanup', 1, 'interval', 0, 'UTC', "
                    " '2026-05-15 19:00:00', '2026-05-15 19:00:00')"
                ))
                # Also a NULL variant — same bug, slightly different shape.
                conn.execute(text(
                    "INSERT INTO task_schedules "
                    "(task_id, enabled, schedule_type, interval_seconds, "
                    " timezone, created_at, updated_at) "
                    "VALUES ('stream_probe', 1, 'interval', NULL, 'UTC', "
                    " '2026-05-15 19:00:00', '2026-05-15 19:00:00')"
                ))
                # A good interval row that MUST survive — defense against
                # an over-aggressive WHERE clause in the pre-flight DELETE.
                conn.execute(text(
                    "INSERT INTO task_schedules "
                    "(task_id, enabled, schedule_type, interval_seconds, "
                    " timezone, created_at, updated_at) "
                    "VALUES ('m3u_refresh', 1, 'interval', 3600, 'UTC', "
                    " '2026-05-15 19:00:00', '2026-05-15 19:00:00')"
                ))
                # A daily row with NULL interval_seconds — also MUST
                # survive (the constraint scopes the check to interval).
                conn.execute(text(
                    "INSERT INTO task_schedules "
                    "(task_id, enabled, schedule_type, interval_seconds, "
                    " schedule_time, timezone, created_at, updated_at) "
                    "VALUES ('epg_refresh', 1, 'daily', NULL, '02:00', 'UTC', "
                    " '2026-05-15 19:00:00', '2026-05-15 19:00:00')"
                ))
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == "0011"
        finally:
            engine.dispose()

        # Pre-fix this would raise IntegrityError on the batch rebuild's
        # row-copy step. The pre-flight DELETE in 0012's upgrade() must
        # remove the two violating rows first.
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            assert self._constraint_in_ddl(engine), (
                "constraint missing after upgrade head on drifted DB — "
                "the migration did not run correctly."
            )
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT task_id, schedule_type, interval_seconds "
                    "FROM task_schedules ORDER BY task_id"
                )).fetchall()
            # Survivors: m3u_refresh interval/3600 + epg_refresh daily/NULL.
            # Casualties: cleanup interval/0 + stream_probe interval/NULL.
            surviving_ids = {r[0] for r in rows}
            assert surviving_ids == {"epg_refresh", "m3u_refresh"}, (
                f"unexpected surviving rows: {rows!r} — the pre-flight "
                "DELETE should have removed only the two interval/0|NULL "
                "rows and left the good rows alone."
            )
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == database.get_alembic_head_revision()
        finally:
            engine.dispose()


@pytest.mark.integration
class TestMigration0012Idempotent:
    """Regression lock for bd-5w6jz idempotency on migration 0012.

    Re-running the migration against a DB where the constraint is
    already present must be a no-op rather than raising or silently
    duplicating the CHECK in the rebuilt table DDL. Reproduces the
    smart-bootstrap fast-path scenario where the alembic_version row
    gets rolled back but the schema state survives.
    """

    def test_idempotent_when_constraint_already_present(self, tmp_path):
        """Re-running 0012 against an already-constrained DB is a no-op."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0012_idemp.db'}"
        cfg = _make_alembic_config(db_url)
        # Bring schema all the way through 0012 — constraint is now present.
        command.upgrade(cfg, "0012")

        engine = create_engine(db_url, future=True)
        try:
            assert TestMigration0012._constraint_in_ddl(engine), (
                "test setup is wrong — constraint missing after upgrade 0012"
            )
            # Roll alembic_version back to 0011 to simulate the
            # version-row-out-of-sync-with-schema state (smart-bootstrap
            # fast-path crashed mid-stamp, partial rollback, etc.).
            with engine.begin() as conn:
                conn.execute(text(
                    "UPDATE alembic_version SET version_num = '0011'"
                ))
        finally:
            engine.dispose()

        # Re-run the migration. Must no-op cleanly.
        # Pre-fix the batch rebuild would either raise or duplicate the
        # constraint silently in the rebuilt table definition.
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            assert TestMigration0012._constraint_in_ddl(engine), (
                "constraint disappeared after re-running 0012 — "
                "the idempotency guard should have skipped the batch "
                "rebuild entirely, leaving the existing constraint intact."
            )
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == database.get_alembic_head_revision()
            # And the constraint must appear EXACTLY ONCE in the DDL —
            # a buggy re-run that duplicates the constraint via the
            # rebuild would have it appearing twice.
            with engine.connect() as conn:
                ddl = conn.execute(text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='table' AND name='task_schedules'"
                )).scalar()
            assert (ddl or "").count("ck_task_schedules_interval_positive") == 1, (
                "constraint appears more than once in the DDL after "
                "re-running 0012 — the idempotency guard let the batch "
                "rebuild add a duplicate constraint."
            )
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# Migration 0013 — session_telemetry per-type channel-event counters (bd-ov5vb)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMigration0013:
    """Migration 0013 — per-type channel-event counters on session_telemetry.

    bd-ov5vb broadens the Stats v2 channel-event ingest to cover every
    event type Dispatcharr emits for channel health (not just
    ``channel_buffering``). The migration adds three INTEGER NOT NULL
    DEFAULT 0 columns:

      - ``reconnect_event_count``
      - ``error_event_count``
      - ``switch_event_count``

    Pre-existing ``buffer_event_count`` is preserved verbatim — pre-bd-ov5vb
    read paths keep working unchanged (the column collapses to 0 on
    installs whose Dispatcharr does not emit ``channel_buffering``,
    which is the truthful posture; that event is rare on real installs).

    Coverage:
      - Fresh upgrade through 0013 — all three new columns exist with
        DEFAULT 0; ``buffer_event_count`` is untouched.
      - Fresh downgrade — the three new columns are gone;
        ``buffer_event_count`` is still present.

    Revision-id history: this migration was originally drafted as 0012
    but bumped to 0013 after rebase to avoid colliding with the
    bd-lbkck ``task_schedules_interval_positive`` migration that landed
    on dev concurrently (also 0012).
    """

    NEW_COLUMNS = ("reconnect_event_count", "error_event_count", "switch_event_count")

    def test_fresh_sqlite_upgrade_through_0013(self, tmp_path):
        """Fresh DB: ``alembic upgrade 0013`` adds the three new columns."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0013_fresh.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0013")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols, (
                    f"{new_col} missing after upgrade 0013 — migration "
                    f"0013 did not run correctly."
                )
            # buffer_event_count is preserved — pre-bd-ov5vb back-compat.
            assert "buffer_event_count" in cols, (
                "buffer_event_count must be preserved by 0013 for "
                "pre-bd-ov5vb read-path back-compat."
            )
            # Verify NOT NULL DEFAULT 0 via PRAGMA.
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "PRAGMA table_info(session_telemetry)"
                )).fetchall()
            col_info = {r[1]: r for r in rows}
            for new_col in self.NEW_COLUMNS:
                _, name, _type, notnull, dflt, _pk = col_info[new_col]
                assert notnull == 1, (
                    f"{name} must be NOT NULL; got notnull={notnull}"
                )
                # SQLite returns DEFAULT as a string literal "0".
                assert dflt == "0", (
                    f"{name} must have DEFAULT 0; got dflt={dflt!r}"
                )
        finally:
            engine.dispose()

    def test_fresh_sqlite_downgrade_from_0013(self, tmp_path):
        """Downgrade 0013 -> 0012: the three new columns are dropped."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0013_downgrade.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0013")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols, (
                    f"test setup is wrong — {new_col} missing post-upgrade"
                )
        finally:
            engine.dispose()

        command.downgrade(cfg, "0012")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col not in cols, (
                    f"{new_col} still present after downgrade — 0013's "
                    f"downgrade() did not drop the column."
                )
            # buffer_event_count must survive the downgrade.
            assert "buffer_event_count" in cols, (
                "buffer_event_count must survive downgrade — it was never "
                "added or dropped by 0013."
            )
        finally:
            engine.dispose()


@pytest.mark.integration
class TestMigration0013Idempotent:
    """Regression lock for bd-5w6jz idempotency on migration 0013.

    Three drift scenarios:

    1. All three new columns already present via ``create_all()`` from
       the post-0013 ORM model (long-running install, alembic_version
       still at 0012). The upgrade must early-return without raising
       ``OperationalError: duplicate column name``.

    2. ONE of the three columns already present (partial drift —
       e.g. the migration crashed mid-run after one ADD COLUMN
       succeeded). The upgrade must add the remaining two without
       raising on the already-present one.

    3. TWO of the three columns already present (alternate partial
       drift). The upgrade must add the third without raising on the
       other two.

    Each column's ADD is independently guarded so any subset of
    drifted columns is safe to re-encounter.
    """

    NEW_COLUMNS = ("reconnect_event_count", "error_event_count", "switch_event_count")

    def test_drifted_all_three_columns_already_added(self, tmp_path):
        """create_all()-style drift: all three columns present pre-0013."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0013_full_drift.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0012")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col not in cols, (
                    f"test setup is wrong — {new_col} already at 0012"
                )
            # Inject all three columns via raw SQL — matches what
            # create_all() would emit from the post-0013 ORM model.
            with engine.begin() as conn:
                for new_col in self.NEW_COLUMNS:
                    conn.execute(text(
                        f"ALTER TABLE session_telemetry ADD COLUMN "
                        f"{new_col} INTEGER NOT NULL DEFAULT 0"
                    ))
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == "0012"
        finally:
            engine.dispose()

        # Pre-fix this would raise:
        #   OperationalError: duplicate column name: reconnect_event_count
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == database.get_alembic_head_revision()
        finally:
            engine.dispose()

    def test_drifted_partial_one_column_present(self, tmp_path):
        """Partial drift: only ``reconnect_event_count`` already present."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0013_partial_one.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0012")

        engine = create_engine(db_url, future=True)
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE session_telemetry ADD COLUMN "
                    "reconnect_event_count INTEGER NOT NULL DEFAULT 0"
                ))
        finally:
            engine.dispose()

        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols, (
                    f"{new_col} missing after partial-drift upgrade — "
                    f"the per-column guard must add absent columns even "
                    f"when other targets are already present."
                )
        finally:
            engine.dispose()

    def test_drifted_partial_two_columns_present(self, tmp_path):
        """Partial drift: ``error_event_count`` and ``switch_event_count``
        already present; only ``reconnect_event_count`` is missing.
        """
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0013_partial_two.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0012")

        engine = create_engine(db_url, future=True)
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE session_telemetry ADD COLUMN "
                    "error_event_count INTEGER NOT NULL DEFAULT 0"
                ))
                conn.execute(text(
                    "ALTER TABLE session_telemetry ADD COLUMN "
                    "switch_event_count INTEGER NOT NULL DEFAULT 0"
                ))
        finally:
            engine.dispose()

        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols, (
                    f"{new_col} missing after partial-drift upgrade"
                )
        finally:
            engine.dispose()


# Migration 0015 — session_telemetry_provider_daily event counter columns (bd-d0ha9)

@pytest.mark.integration
class TestMigration0015:
    """Migration 0015 — reconnect/error/switch event counters on the rollup table.

    bd-d0ha9 extends the nightly rollup to surface the three per-type
    channel-event counters that migration 0013 (bd-ov5vb) added to
    ``session_telemetry`` but did not carry forward into the rollup table
    ``session_telemetry_provider_daily``.

    Three new INTEGER NOT NULL DEFAULT 0 columns:

      - ``reconnect_event_count``
      - ``error_event_count``
      - ``switch_event_count``

    Pre-existing columns (``buffer_event_count``, ``bytes_delta_sum``,
    ``watch_seconds``) are preserved verbatim — this migration is additive only.

    Coverage:
      - Fresh upgrade through 0014 — all three new columns exist with
        DEFAULT 0 on ``session_telemetry_provider_daily``.
      - Fresh downgrade — the three new columns are gone;
        ``buffer_event_count`` is still present.
    """

    NEW_COLUMNS = ("reconnect_event_count", "error_event_count", "switch_event_count")

    def test_fresh_sqlite_upgrade_through_0015(self, tmp_path):
        """Fresh DB: ``alembic upgrade 0014`` adds three new columns to the
        rollup table."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0015_fresh.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0015")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry_provider_daily")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols, (
                    f"{new_col} missing after upgrade 0014 — migration "
                    f"0014 did not run correctly."
                )
            # buffer_event_count is preserved — pre-bd-d0ha9 back-compat.
            assert "buffer_event_count" in cols, (
                "buffer_event_count must be preserved by 0014 for "
                "pre-bd-d0ha9 read-path back-compat."
            )
            # Verify NOT NULL DEFAULT 0 via PRAGMA.
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "PRAGMA table_info(session_telemetry_provider_daily)"
                )).fetchall()
            col_info = {r[1]: r for r in rows}
            for new_col in self.NEW_COLUMNS:
                _, name, _type, notnull, dflt, _pk = col_info[new_col]
                assert notnull == 1, (
                    f"{name} must be NOT NULL; got notnull={notnull}"
                )
                # SQLite returns DEFAULT as a string literal "0".
                assert dflt == "0", (
                    f"{name} must have DEFAULT 0; got dflt={dflt!r}"
                )
        finally:
            engine.dispose()

    def test_fresh_sqlite_downgrade_from_0015(self, tmp_path):
        """Downgrade 0014 -> 0013: the three new columns are dropped from
        the rollup table."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0015_downgrade.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0015")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry_provider_daily")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols, (
                    f"test setup is wrong — {new_col} missing post-upgrade"
                )
        finally:
            engine.dispose()

        command.downgrade(cfg, "0013")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry_provider_daily")
            for new_col in self.NEW_COLUMNS:
                assert new_col not in cols, (
                    f"{new_col} still present after downgrade — 0014's "
                    f"downgrade() did not drop the column."
                )
            # buffer_event_count must survive the downgrade.
            assert "buffer_event_count" in cols, (
                "buffer_event_count must survive downgrade — it was never "
                "added or dropped by 0014."
            )
        finally:
            engine.dispose()


@pytest.mark.integration
class TestMigration0015Idempotent:
    """Regression lock for bd-5w6jz idempotency on migration 0015.

    Three drift scenarios mirror the 0013 idempotency tests:

    1. All three new rollup columns already present via ``create_all()``
       from the post-0014 ORM model (long-running install, alembic_version
       still at 0013). The upgrade must early-return without raising
       ``OperationalError: duplicate column name``.

    2. ONE of the three columns already present (partial drift).
       The upgrade must add the remaining two.

    3. TWO of the three columns already present (alternate partial drift).
       The upgrade must add the third.
    """

    NEW_COLUMNS = ("reconnect_event_count", "error_event_count", "switch_event_count")

    def test_drifted_all_three_columns_already_added(self, tmp_path):
        """create_all()-style drift: all three columns present pre-0014."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0015_full_drift.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0013")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry_provider_daily")
            for new_col in self.NEW_COLUMNS:
                assert new_col not in cols, (
                    f"test setup is wrong — {new_col} already at 0013"
                )
            # Inject all three columns via raw SQL — matches what
            # create_all() would emit from the post-0014 ORM model.
            with engine.begin() as conn:
                for new_col in self.NEW_COLUMNS:
                    conn.execute(text(
                        f"ALTER TABLE session_telemetry_provider_daily "
                        f"ADD COLUMN {new_col} INTEGER NOT NULL DEFAULT 0"
                    ))
            cols = _column_names(engine, "session_telemetry_provider_daily")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == "0013"
        finally:
            engine.dispose()

        # Pre-fix this would raise:
        #   OperationalError: duplicate column name: reconnect_event_count
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry_provider_daily")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == database.get_alembic_head_revision()
        finally:
            engine.dispose()

    def test_drifted_partial_one_column_present(self, tmp_path):
        """Partial drift: only ``reconnect_event_count`` already present."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0015_partial_one.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0013")

        engine = create_engine(db_url, future=True)
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE session_telemetry_provider_daily "
                    "ADD COLUMN reconnect_event_count INTEGER NOT NULL DEFAULT 0"
                ))
        finally:
            engine.dispose()

        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry_provider_daily")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols, (
                    f"{new_col} missing after partial-drift upgrade — "
                    f"the per-column guard must add absent columns even "
                    f"when other targets are already present."
                )
        finally:
            engine.dispose()

    def test_drifted_partial_two_columns_present(self, tmp_path):
        """Partial drift: ``error_event_count`` and ``switch_event_count``
        already present; only ``reconnect_event_count`` is missing.
        """
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0015_partial_two.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0013")

        engine = create_engine(db_url, future=True)
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE session_telemetry_provider_daily "
                    "ADD COLUMN error_event_count INTEGER NOT NULL DEFAULT 0"
                ))
                conn.execute(text(
                    "ALTER TABLE session_telemetry_provider_daily "
                    "ADD COLUMN switch_event_count INTEGER NOT NULL DEFAULT 0"
                ))
        finally:
            engine.dispose()

        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry_provider_daily")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols, (
                    f"{new_col} missing after partial-drift upgrade"
                )
        finally:
            engine.dispose()


# Migration 0016 — session_telemetry Emby user attribution columns (bd-k026g)

@pytest.mark.integration
class TestMigration0016:
    """Migration 0016 — Emby user attribution columns on ``session_telemetry``.

    bd-k026g is the schema substrate for the Emby user attribution epic
    (parent ``enhancedchannelmanager-2cenq``). ECM only sees the
    Dispatcharr stream session's IP; when users watch via an Emby server
    all stream pulls collapse to a single "Emby server" identity in
    Stats. The Emby integration cross-references each live Emby session
    against ECM's active streams and persists the resolved user via two
    new NULLABLE TEXT columns on ``session_telemetry``:

      - ``emby_user_id`` (TEXT NULL — Emby user IDs are GUIDs, not ints)
      - ``emby_user_name`` (TEXT NULL — denormalized at write time)

    Pre-existing columns (``user_id``, ``dispatcharr_username``,
    ``provider_id``, ``channel_id``, ``buffer_event_count``, the three
    per-type event counters from migration 0013, ``stream_id``,
    ``stream_name``) are preserved verbatim — this migration is
    additive only.

    Coverage:
      - Fresh upgrade through 0016 — both new columns exist as TEXT NULL;
        ``dispatcharr_username`` is untouched.
      - Fresh downgrade — both new columns are gone;
        ``dispatcharr_username`` is still present.
    """

    NEW_COLUMNS = ("emby_user_id", "emby_user_name")

    def test_fresh_sqlite_upgrade_through_0016(self, tmp_path):
        """Fresh DB: ``alembic upgrade 0016`` adds the two new columns."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0016_fresh.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0016")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols, (
                    f"{new_col} missing after upgrade 0016 — migration "
                    f"0016 did not run correctly."
                )
            # dispatcharr_username is preserved — pre-bd-k026g back-compat
            # (the per-row writer maintains both Dispatcharr and Emby
            # attribution side-by-side; both columns are populated for
            # Emby-mediated streams and surface independently in Stats).
            assert "dispatcharr_username" in cols, (
                "dispatcharr_username must be preserved by 0016 — the "
                "Emby attribution columns are additive, not a rename."
            )
            # Verify NULLABLE TEXT via PRAGMA.
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "PRAGMA table_info(session_telemetry)"
                )).fetchall()
            col_info = {r[1]: r for r in rows}
            for new_col in self.NEW_COLUMNS:
                _, name, col_type, notnull, dflt, _pk = col_info[new_col]
                assert notnull == 0, (
                    f"{name} must be NULLABLE (notnull=0); got notnull={notnull}"
                )
                # SQLAlchemy ``sa.Text()`` renders as ``TEXT`` in SQLite.
                assert col_type == "TEXT", (
                    f"{name} must be TEXT; got type={col_type!r}"
                )
                # No DEFAULT for nullable text columns.
                assert dflt is None, (
                    f"{name} must have no DEFAULT; got dflt={dflt!r}"
                )
        finally:
            engine.dispose()

    def test_fresh_sqlite_downgrade_from_0016(self, tmp_path):
        """Downgrade 0016 -> 0015: the two new columns are dropped."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0016_downgrade.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0016")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols, (
                    f"test setup is wrong — {new_col} missing post-upgrade"
                )
        finally:
            engine.dispose()

        command.downgrade(cfg, "0015")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col not in cols, (
                    f"{new_col} still present after downgrade — 0016's "
                    f"downgrade() did not drop the column."
                )
            # dispatcharr_username must survive the downgrade.
            assert "dispatcharr_username" in cols, (
                "dispatcharr_username must survive downgrade — it was never "
                "added or dropped by 0016."
            )
        finally:
            engine.dispose()


@pytest.mark.integration
class TestMigration0016Idempotent:
    """Regression lock for bd-5w6jz idempotency on migration 0016.

    Three drift scenarios mirror the 0013 / 0015 idempotency tests
    scaled to the two-column shape:

    1. Both new columns already present via ``create_all()`` from the
       post-0016 ORM model (long-running install, alembic_version still
       at 0015). The upgrade must early-return without raising
       ``OperationalError: duplicate column name``.

    2. ONE of the two columns already present (partial drift — e.g.
       the migration crashed mid-run after one ADD COLUMN succeeded,
       or a manual ALTER added one column ahead of the migration).
       The upgrade must add the remaining one without raising on the
       already-present one.

    Each column's ADD is independently guarded so any subset of
    drifted columns is safe to re-encounter.
    """

    NEW_COLUMNS = ("emby_user_id", "emby_user_name")

    def test_drifted_both_columns_already_added(self, tmp_path):
        """create_all()-style drift: both columns present pre-0016."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0016_full_drift.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0015")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col not in cols, (
                    f"test setup is wrong — {new_col} already at 0015"
                )
            # Inject both columns via raw SQL — matches what
            # create_all() would emit from the post-0016 ORM model.
            with engine.begin() as conn:
                for new_col in self.NEW_COLUMNS:
                    conn.execute(text(
                        f"ALTER TABLE session_telemetry ADD COLUMN "
                        f"{new_col} TEXT"
                    ))
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == "0015"
        finally:
            engine.dispose()

        # Pre-fix this would raise:
        #   OperationalError: duplicate column name: emby_user_id
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == database.get_alembic_head_revision()
        finally:
            engine.dispose()

    def test_drifted_partial_emby_user_id_present(self, tmp_path):
        """Partial drift: only ``emby_user_id`` already present."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0016_partial_id.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0015")

        engine = create_engine(db_url, future=True)
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE session_telemetry ADD COLUMN "
                    "emby_user_id TEXT"
                ))
        finally:
            engine.dispose()

        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols, (
                    f"{new_col} missing after partial-drift upgrade — "
                    f"the per-column guard must add absent columns even "
                    f"when other targets are already present."
                )
        finally:
            engine.dispose()

    def test_drifted_partial_emby_user_name_present(self, tmp_path):
        """Partial drift: only ``emby_user_name`` already present."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0016_partial_name.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0015")

        engine = create_engine(db_url, future=True)
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE session_telemetry ADD COLUMN "
                    "emby_user_name TEXT"
                ))
        finally:
            engine.dispose()

        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols, (
                    f"{new_col} missing after partial-drift upgrade — "
                    f"the per-column guard must add absent columns even "
                    f"when other targets are already present."
                )
        finally:
            engine.dispose()

    def test_round_trip_up_down_up(self, tmp_path):
        """Round-trip: upgrade 0016, downgrade to 0015, re-upgrade to 0016.

        Covers the rollback/re-apply path: a migration that breaks on
        re-apply after a downgrade is the hardest mistake to catch in
        a fresh-DB test. Both columns must reappear after the second
        upgrade with their NULLABLE TEXT shape intact.
        """
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0016_round_trip.db'}"
        cfg = _make_alembic_config(db_url)

        # Up.
        command.upgrade(cfg, "0016")
        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols
        finally:
            engine.dispose()

        # Down.
        command.downgrade(cfg, "0015")
        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col not in cols
        finally:
            engine.dispose()

        # Up again — re-apply after downgrade must work.
        command.upgrade(cfg, "0016")
        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols, (
                    f"{new_col} missing after re-upgrade — the migration "
                    f"must be re-appliable after a downgrade."
                )
            # Verify the shape held across the round-trip.
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "PRAGMA table_info(session_telemetry)"
                )).fetchall()
            col_info = {r[1]: r for r in rows}
            for new_col in self.NEW_COLUMNS:
                _, name, col_type, notnull, _dflt, _pk = col_info[new_col]
                assert notnull == 0, (
                    f"{name} must be NULLABLE after round-trip"
                )
                assert col_type == "TEXT", (
                    f"{name} must be TEXT after round-trip"
                )
        finally:
            engine.dispose()


# Migration 0017 — session_telemetry Plex + Jellyfin user attribution columns (bd-r5f0c.1)

@pytest.mark.integration
class TestMigration0017:
    """Migration 0017 — Plex + Jellyfin user attribution columns on ``session_telemetry``.

    bd-r5f0c.1 is the schema substrate for the Plex + Jellyfin user
    attribution epic (parent ``enhancedchannelmanager-r5f0c``), at
    parity with the shipped Emby attribution (migration 0016, bd-k026g,
    parent epic ``enhancedchannelmanager-2cenq``). ECM only sees the
    Dispatcharr stream session's IP; when users watch via a Plex or
    Jellyfin server all stream pulls collapse to a single "Plex server"
    / "Jellyfin server" identity in Stats. The Plex (W2) and Jellyfin
    (W3) integrations cross-reference each live upstream session
    against ECM's active streams and persist the resolved user via four
    new NULLABLE TEXT columns on ``session_telemetry``:

      - ``plex_user_id`` (TEXT NULL — Plex serves user IDs as strings
        in ``/sessions``; staying aligned with the Emby TEXT choice)
      - ``plex_user_name`` (TEXT NULL — denormalized at write time)
      - ``jellyfin_user_id`` (TEXT NULL — Jellyfin user IDs are GUIDs,
        same as Emby since Jellyfin forked from Emby)
      - ``jellyfin_user_name`` (TEXT NULL — denormalized at write time)

    Pre-existing columns (every column carried through 0016 — including
    the Emby pair ``emby_user_id`` / ``emby_user_name``) are preserved
    verbatim — this migration is additive only.

    Coverage:
      - Fresh upgrade through 0017 — all four new columns exist as
        TEXT NULL; ``dispatcharr_username`` and the Emby pair are
        untouched.
      - Fresh downgrade — all four new columns are gone; the Emby
        pair and ``dispatcharr_username`` are still present.
    """

    NEW_COLUMNS = (
        "plex_user_id",
        "plex_user_name",
        "jellyfin_user_id",
        "jellyfin_user_name",
    )

    def test_fresh_sqlite_upgrade_through_0017(self, tmp_path):
        """Fresh DB: ``alembic upgrade 0017`` adds the four new columns."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0017_fresh.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0017")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols, (
                    f"{new_col} missing after upgrade 0017 — migration "
                    f"0017 did not run correctly."
                )
            # dispatcharr_username and the Emby pair are preserved —
            # pre-bd-r5f0c.1 back-compat (the per-row writer maintains
            # Dispatcharr + Emby + Plex + Jellyfin attribution side-by-
            # side; each upstream populates its own pair for the
            # matching mediated streams, and the columns surface
            # independently in Stats).
            assert "dispatcharr_username" in cols, (
                "dispatcharr_username must be preserved by 0017 — the "
                "Plex + Jellyfin attribution columns are additive, not "
                "a rename."
            )
            assert "emby_user_id" in cols, (
                "emby_user_id must be preserved by 0017 — the Plex + "
                "Jellyfin attribution columns are additive, not a "
                "replacement for the Emby pair."
            )
            assert "emby_user_name" in cols, (
                "emby_user_name must be preserved by 0017 — the Plex + "
                "Jellyfin attribution columns are additive, not a "
                "replacement for the Emby pair."
            )
            # Verify NULLABLE TEXT via PRAGMA.
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "PRAGMA table_info(session_telemetry)"
                )).fetchall()
            col_info = {r[1]: r for r in rows}
            for new_col in self.NEW_COLUMNS:
                _, name, col_type, notnull, dflt, _pk = col_info[new_col]
                assert notnull == 0, (
                    f"{name} must be NULLABLE (notnull=0); got notnull={notnull}"
                )
                # SQLAlchemy ``sa.Text()`` renders as ``TEXT`` in SQLite.
                assert col_type == "TEXT", (
                    f"{name} must be TEXT; got type={col_type!r}"
                )
                # No DEFAULT for nullable text columns.
                assert dflt is None, (
                    f"{name} must have no DEFAULT; got dflt={dflt!r}"
                )
        finally:
            engine.dispose()

    def test_fresh_sqlite_downgrade_from_0017(self, tmp_path):
        """Downgrade 0017 -> 0016: the four new columns are dropped."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0017_downgrade.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0017")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols, (
                    f"test setup is wrong — {new_col} missing post-upgrade"
                )
        finally:
            engine.dispose()

        command.downgrade(cfg, "0016")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col not in cols, (
                    f"{new_col} still present after downgrade — 0017's "
                    f"downgrade() did not drop the column."
                )
            # The Emby pair and dispatcharr_username must survive the
            # downgrade — they were never added or dropped by 0017.
            assert "dispatcharr_username" in cols, (
                "dispatcharr_username must survive downgrade — it was "
                "never added or dropped by 0017."
            )
            assert "emby_user_id" in cols, (
                "emby_user_id must survive downgrade — it was added by "
                "0016, not by 0017."
            )
            assert "emby_user_name" in cols, (
                "emby_user_name must survive downgrade — it was added "
                "by 0016, not by 0017."
            )
        finally:
            engine.dispose()


@pytest.mark.integration
class TestMigration0017Idempotent:
    """Regression lock for bd-5w6jz idempotency on migration 0017.

    Drift scenarios mirror the 0016 idempotency tests scaled to the
    four-column shape:

    1. All four new columns already present via ``create_all()`` from
       the post-0017 ORM model (long-running install, alembic_version
       still at 0016). The upgrade must early-return without raising
       ``OperationalError: duplicate column name``.

    2. ONE of the four columns already present (partial drift — e.g.
       the migration crashed mid-run after one ADD COLUMN succeeded,
       or a manual ALTER added one column ahead of the migration).
       The upgrade must add the remaining three without raising on the
       already-present one. We exercise each of the four columns
       individually as the "already present" one — each ADD is
       independently guarded so any subset of drifted columns is safe
       to re-encounter.
    """

    NEW_COLUMNS = (
        "plex_user_id",
        "plex_user_name",
        "jellyfin_user_id",
        "jellyfin_user_name",
    )

    def test_drifted_all_columns_already_added(self, tmp_path):
        """create_all()-style drift: all four columns present pre-0017."""
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0017_full_drift.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0016")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col not in cols, (
                    f"test setup is wrong — {new_col} already at 0016"
                )
            # Inject all four columns via raw SQL — matches what
            # create_all() would emit from the post-0017 ORM model.
            with engine.begin() as conn:
                for new_col in self.NEW_COLUMNS:
                    conn.execute(text(
                        f"ALTER TABLE session_telemetry ADD COLUMN "
                        f"{new_col} TEXT"
                    ))
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == "0016"
        finally:
            engine.dispose()

        # Pre-fix this would raise:
        #   OperationalError: duplicate column name: plex_user_id
        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
            assert row is not None and row[0] == database.get_alembic_head_revision()
        finally:
            engine.dispose()

    @pytest.mark.parametrize("drifted_col", [
        "plex_user_id",
        "plex_user_name",
        "jellyfin_user_id",
        "jellyfin_user_name",
    ])
    def test_drifted_single_column_present(self, tmp_path, drifted_col):
        """Partial drift: exactly one of the four columns already present.

        Each of the four columns is exercised individually as the
        already-drifted one — the per-column guard must add the
        remaining three without raising on the present one.
        """
        from alembic import command

        db_url = f"sqlite:///{tmp_path / f'mig0017_partial_{drifted_col}.db'}"
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "0016")

        engine = create_engine(db_url, future=True)
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    f"ALTER TABLE session_telemetry ADD COLUMN "
                    f"{drifted_col} TEXT"
                ))
        finally:
            engine.dispose()

        command.upgrade(cfg, "head")

        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols, (
                    f"{new_col} missing after partial-drift upgrade — "
                    f"the per-column guard must add absent columns "
                    f"even when {drifted_col} is already present."
                )
        finally:
            engine.dispose()

    def test_round_trip_up_down_up(self, tmp_path):
        """Round-trip: upgrade 0017, downgrade to 0016, re-upgrade to 0017.

        Covers the rollback/re-apply path: a migration that breaks on
        re-apply after a downgrade is the hardest mistake to catch in
        a fresh-DB test. All four columns must reappear after the
        second upgrade with their NULLABLE TEXT shape intact.
        """
        from alembic import command

        db_url = f"sqlite:///{tmp_path / 'mig0017_round_trip.db'}"
        cfg = _make_alembic_config(db_url)

        # Up.
        command.upgrade(cfg, "0017")
        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols
        finally:
            engine.dispose()

        # Down.
        command.downgrade(cfg, "0016")
        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col not in cols
        finally:
            engine.dispose()

        # Up again — re-apply after downgrade must work.
        command.upgrade(cfg, "0017")
        engine = create_engine(db_url, future=True)
        try:
            cols = _column_names(engine, "session_telemetry")
            for new_col in self.NEW_COLUMNS:
                assert new_col in cols, (
                    f"{new_col} missing after re-upgrade — the migration "
                    f"must be re-appliable after a downgrade."
                )
            # Verify the shape held across the round-trip.
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "PRAGMA table_info(session_telemetry)"
                )).fetchall()
            col_info = {r[1]: r for r in rows}
            for new_col in self.NEW_COLUMNS:
                _, name, col_type, notnull, _dflt, _pk = col_info[new_col]
                assert notnull == 0, (
                    f"{name} must be NULLABLE after round-trip"
                )
                assert col_type == "TEXT", (
                    f"{name} must be TEXT after round-trip"
                )
        finally:
            engine.dispose()
