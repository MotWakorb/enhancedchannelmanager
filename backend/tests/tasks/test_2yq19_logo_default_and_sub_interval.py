"""A replica arrives WITH its branding (bead ``…-2yq19``).

THE INVARIANT UNDER TEST, and it is two halves that only work together::

    A sync target created without an explicit choice replicates logos, and the
    cost of doing so is paid on a slower clock than the config slice — never by
    leaving the replica unbranded.

WHAT CHANGED AND WHAT DID NOT. ``sync_logos`` shipped default OFF under bead
``7ipq2.1``, and the recorded reason was COST: the logos importer carries a
destructive ``clear_existing`` bulk-delete plus a per-logo streaming upload that
ADR-013 S9 judged wrong to run every interval. That MECHANISM decision is
untouched here — logos still do not join the unconditional per-cycle set. What
the 2026-08-22 governing principle disturbs is the DEFAULT: "everything
replicates by default; every exclusion must be named, individually justified,
and visible", with "it is cheaper not to" named explicitly as a non-reason. An
operator who never finds the toggle received a replica with no artwork at all,
which is the second half of what epic ``f5a5j`` is named for — "a structurally
correct replica that has lost its guide data and its branding".

So the default flips and the cost moves to a SUB-INTERVAL
(``logo_sync_interval_hours``, default 24; ``0`` = every cycle) clocked off
``last_logo_sync_at``.

THE HALF THAT IS EASY TO GET WRONG, and is asserted here as its own property:
**no existing target's stored ``sync_logos`` value is rewritten.** A stored
``False`` is indistinguishable from an operator who deliberately turned logos
off, and a migration that silently flips a saved operator choice is a one-way
door this bead was not asked to open. A ``server_default`` governs rows inserted
WITHOUT the column; it does not touch rows that already have one.

LAYERS. Tests 1-2 are ORM/route level (where the product default actually
lives — asserting it through ``make_sync_target`` would assert the fixture).
Tests 3-8 are unit-level over ``logo_slice_is_due``. Test 9 crosses the real
engine seam to show the gate reaches the plan.
"""

from datetime import datetime, timedelta, timezone

import pytest

from tasks.dbas_sync_engine import logo_slice_is_due
from tests.fixtures.sync_harness import (
    StatefulDispatcharrFake,
    SyncHarness,
    make_sync_target,
)


class _Target:
    """A minimal target row — plain attributes, so nothing is a Mock's truthiness.

    ``make_sync_target`` returns a ``MagicMock``, on which EVERY attribute is
    truthy and every unset one is a ``Mock``. That is fine for the engine seam
    and useless for a boundary test: ``logo_sync_interval_hours`` would read as
    a Mock rather than as a number, and the assertion could not fail for the
    reason it claims to.
    """

    def __init__(self, **kwargs):
        self.sync_logos = kwargs.get("sync_logos", True)
        self.logo_sync_interval_hours = kwargs.get("logo_sync_interval_hours", 24)
        self.last_logo_sync_at = kwargs.get("last_logo_sync_at", None)


# ---------------------------------------------------------------------------
# 1-2. THE DEFAULT, asserted where the product actually sets it.
# ---------------------------------------------------------------------------


def test_the_orm_column_defaults_logos_on():
    """A target built through the ORM with no explicit choice replicates logos."""
    from export_models import SyncTarget

    target = SyncTarget(name="B", base_url="https://b.example.com")
    column = SyncTarget.__table__.columns["sync_logos"]
    # The Python-side default is what a plain ``SyncTarget(...)`` gets on flush;
    # the server_default is what a row inserted without the column gets. Both
    # have to say ON, or the default depends on which path created the row.
    assert column.default.arg is True
    assert "1" in str(column.server_default.arg)
    assert target.sync_logos is None or target.sync_logos is True


def test_the_sub_interval_column_defaults_to_a_slower_clock():
    """The throttle is what lets the default above be ON — it must exist."""
    from export_models import SyncTarget

    interval = SyncTarget.__table__.columns["logo_sync_interval_hours"]
    assert interval.default.arg == 24
    assert "24" in str(interval.server_default.arg)
    # NULL == never ran, which is what makes a brand-new target's FIRST cycle
    # carry logos instead of waiting out an interval with no artwork on B.
    assert SyncTarget.__table__.columns["last_logo_sync_at"].nullable is True


@pytest.mark.integration
def test_migration_0048_flips_the_default_without_rewriting_a_stored_choice(tmp_path):
    """Run the real migration and read the real rows — the load-bearing half.

    Three facts, and the second is the one a prose-only migration note cannot
    establish:

    1. a row inserted AFTER 0048 without the column gets logos ON;
    2. a row that already existed with ``sync_logos = 0`` still has ``0``. A
       stored ``False`` is indistinguishable from an operator who deliberately
       turned logos off, so flipping it in a migration is a one-way door;
    3. the two sub-interval columns exist and backfill (24 / NULL), which is
       what makes an existing target replicate on a slower clock rather than on
       every cycle the moment its operator turns the toggle on.
    """
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text

    import database

    db_url = f"sqlite:///{tmp_path / 'm0048.db'}"
    cfg = Config(str(Path(database.ALEMBIC_INI_PATH)))
    cfg.set_main_option("sqlalchemy.url", db_url)

    # Pre-feature schema, then a live target with logos explicitly OFF.
    command.upgrade(cfg, "0047")
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO sync_targets "
                    "(name, base_url, credentials, enabled, credential_version, "
                    " insecure, fuzzy_stream_matching, sync_logos, created_at, "
                    " updated_at) "
                    "VALUES ('legacy-b', 'https://b.example.com', '', 1, 1, 0, 0, 0, "
                    " '2026-08-01 00:00:00', '2026-08-01 00:00:00')"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(cfg, "0048")

    engine = create_engine(db_url)
    try:
        columns = {c["name"] for c in inspect(engine).get_columns("sync_targets")}
        assert {"logo_sync_interval_hours", "last_logo_sync_at"} <= columns

        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO sync_targets "
                    "(name, base_url, credentials, enabled, credential_version, "
                    " insecure, fuzzy_stream_matching, created_at, updated_at) "
                    "VALUES ('new-b', 'https://c.example.com', '', 1, 1, 0, 0, "
                    " '2026-08-23 00:00:00', '2026-08-23 00:00:00')"
                )
            )
            rows = dict(
                conn.execute(
                    text("SELECT name, sync_logos FROM sync_targets ORDER BY name")
                ).all()
            )
            backfill = conn.execute(
                text(
                    "SELECT logo_sync_interval_hours, last_logo_sync_at "
                    "FROM sync_targets WHERE name = 'legacy-b'"
                )
            ).one()

        # (1) the NEW row takes the new default…
        assert rows["new-b"] == 1
        # (2) …and the operator's stored choice on the OLD row is untouched.
        assert rows["legacy-b"] == 0
        # (3) the sub-interval backfills for the existing row.
        assert backfill[0] == 24
        assert backfill[1] is None
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 3-8. THE SUB-INTERVAL, as a boundary property.
# ---------------------------------------------------------------------------


def test_logos_off_means_off_whatever_the_clock_says():
    """The throttle never turns a disabled slice back on."""
    assert (
        logo_slice_is_due(
            _Target(sync_logos=False, logo_sync_interval_hours=0, last_logo_sync_at=None)
        )
        is False
    )


def test_a_target_that_has_never_run_the_slice_is_due_now():
    """NULL means DUE — a new replica does not wait a day for its artwork."""
    assert logo_slice_is_due(_Target(last_logo_sync_at=None)) is True


def test_an_interval_of_zero_means_every_cycle():
    """The pre-throttle behaviour stays reachable."""
    target = _Target(
        logo_sync_interval_hours=0,
        last_logo_sync_at=datetime.now(timezone.utc),
    )
    assert logo_slice_is_due(target) is True


def test_the_slice_is_not_due_before_the_interval_elapses():
    """This is the whole cost saving — assert it can actually say no."""
    target = _Target(
        logo_sync_interval_hours=24,
        last_logo_sync_at=datetime.now(timezone.utc) - timedelta(hours=23),
    )
    assert logo_slice_is_due(target) is False


def test_the_slice_is_due_once_the_interval_has_elapsed():
    target = _Target(
        logo_sync_interval_hours=24,
        last_logo_sync_at=datetime.now(timezone.utc) - timedelta(hours=25),
    )
    assert logo_slice_is_due(target) is True


def test_a_naive_stamp_is_read_as_utc_rather_than_raising():
    """The column is naive UTC like every other timestamp on this row.

    A tz-aware/naive mix raises ``TypeError`` on subtraction, and an exception
    here would abort a whole sync cycle over the logo clock.
    """
    naive_recent = datetime.utcnow() - timedelta(hours=1)
    assert logo_slice_is_due(
        _Target(logo_sync_interval_hours=24, last_logo_sync_at=naive_recent)
    ) is False
    naive_old = datetime.utcnow() - timedelta(hours=48)
    assert logo_slice_is_due(
        _Target(logo_sync_interval_hours=24, last_logo_sync_at=naive_old)
    ) is True


def test_an_unreadable_interval_falls_back_to_every_cycle_not_to_never():
    """A value that cannot be trusted must fail TOWARD the faithful copy.

    Falling back to "never" would reinstate the silent-omission failure this
    bead exists to remove, and it would do it invisibly.
    """
    target = _Target(
        logo_sync_interval_hours="not-a-number",
        last_logo_sync_at=datetime.now(timezone.utc),
    )
    assert logo_slice_is_due(target) is True


# ---------------------------------------------------------------------------
# 9. Through the real engine: the gate reaches the plan.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_throttled_cycle_carries_no_logo_category(tmp_path):
    """Logos ON but not yet due => the cycle runs and the LOGO slice does not.

    A structural assertion on ``logo_slice_is_due`` alone would pass on a gate
    that was computed and then ignored, which is precisely how a wired-looking
    control moves nothing. This runs a cycle and looks at what B received.
    """
    config_dir = tmp_path / "config"
    logos_dir = config_dir / "uploads" / "logos"
    logos_dir.mkdir(parents=True)
    (logos_dir / "cnn.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)

    source = StatefulDispatcharrFake.seeded_source()
    dest = StatefulDispatcharrFake.empty_dest()
    target = make_sync_target(
        sync_logos=True,
        logo_sync_interval_hours=24,
        last_logo_sync_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    harness = SyncHarness(
        source=source, dest=dest, target=target, config_dir=config_dir
    )
    report = await harness.run(confirm_apply=True, ledger_dir=tmp_path / "ledger")

    from dbas.restore_contracts import EntityType

    logo_cat = report.category(EntityType.LOGO)
    assert logo_cat.created == 0
    assert dest.logo_names() == set()
