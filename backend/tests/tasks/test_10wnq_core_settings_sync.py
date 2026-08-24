"""Core settings reach the replica, per blob, with reasons (bead ``…-10wnq``).

THE INVARIANT UNDER TEST, quoted from the bead and stated as a property::

    Every category and column ADR-013 names is either implemented as named, or
    the ADR is amended to say what the code actually does and why. No item is
    left diverging silently.

``core_settings`` and ``insecure`` are TWO EXAMPLES of that property, not its
specification, so the tests below are parameterised over the register rather
than written against those two names.

WHAT WAS DIVERGING. ADR-013 S9 and S3 have listed "core settings" in the
per-cycle config set since the ADR was written. A grep for ``core_settings``
across the sync engine returned NOTHING — not in the category set, not in the
step registry, nowhere. Three of the seven blobs were additionally ruled SYNC by
the PO on 2026-08-21 and never built. Separately, S3's never-sync COLUMN list
named four columns while the shipped constant named three.

THE TRAP, named on the bead and pinned by test 2: settings are key/value BLOBS
and are deliberately absent from ``_SECTION_TO_ENTITY``, which maps sections to
ENTITY-LIST categories. The plan assembler iterates that table, so a category
key added on its own would have been INERT — wired-looking and moving nothing.

THE SILENT FK PROBLEM (tests 6-9), which the 2026-08-21 ruling did not account
for. Three of ``stream_settings``' five members are instance-local FK ids. Unlike
the M3U account's ``user_agent`` (bead ``…-9h6cv``) and ``server_group`` (bead
``…-g8tyd``), where B answered 400 and the defect announced itself, a settings
blob is a JSON value Dispatcharr stores without validating — so forwarding A's
ids points B's defaults at unrelated rows with no error, no counter and no
report.

MEASURED AGAINST ``dispatcharr:latest`` (0.29.0) on 2026-08-23, from the source
rather than from the live instance's values — the bead explicitly forbade
resolving ``user_limit_settings`` from its empty live case:

* ``core/models.py:426`` — ``stream_settings`` = ``default_user_agent``,
  ``default_stream_profile``, ``m3u_hash_key``, ``default_output_format``,
  ``hdhr_output_profile_id``.
* ``core/models.py:614`` — ``dvr_settings`` = path templates, comskip flags,
  offsets, ``series_rules``.
* ``core/models.py:701`` — ``proxy_settings`` = six plain numbers.
* ``core/models.py:714`` — ``network_access`` = per-endpoint CIDR allowlists.
* ``core/models.py:720`` — ``system_settings`` = timezone, event cap, region,
  three booleans.
* ``core/models.py:756`` — ``user_limit_settings`` = FOUR GLOBAL BOOLEANS, no
  user reference at any depth. Consumers: ``apps/proxy/utils.py:148-151,
  308-309, 358``.
* ``apps/backups/scheduler.py`` — ``backup_settings`` = schedule + retention.
"""

import json

import pytest

from dbas.restore_artifact import _SECTION_TO_ENTITY
from dbas.restore_contracts import EntityType, IdRemapTable, RestoreReport
from tasks.dbas_sync_engine import (
    NEVER_SYNC_CORE_SETTINGS_BLOBS,
    SYNC_CONFIG_CATEGORIES,
    SYNC_CORE_SETTINGS_BLOBS,
    SYNC_NEVER_CREDENTIAL_COLUMNS,
    remap_stream_settings_fks,
    select_core_settings_blobs,
    sync_config_importer_steps,
    target_excluded_core_settings,
)


#: Every blob Dispatcharr 0.29.0 exposes, and the ruling this bead recorded for
#: it. Parameterising over this is what makes the tests a property over the
#: REGISTER rather than three hand-written cases: a blob added to the engine
#: without a decision recorded here fails test 1.
BLOB_RULINGS: dict[str, str] = {
    "stream_settings": "sync",
    "dvr_settings": "sync",
    "system_settings": "sync",
    "user_limit_settings": "sync",
    "proxy_settings": "sync",
    "backup_settings": "sync",
    "network_access": "never",
}


# ---------------------------------------------------------------------------
# 1-3. The category exists, is not inert, and every blob has a ruling.
# ---------------------------------------------------------------------------


def test_every_blob_dispatcharr_exposes_carries_a_recorded_ruling():
    """No blob is left undecided — the ADR's "no item diverges silently"."""
    decided = SYNC_CORE_SETTINGS_BLOBS | NEVER_SYNC_CORE_SETTINGS_BLOBS
    assert decided == set(BLOB_RULINGS)
    for name, ruling in BLOB_RULINGS.items():
        if ruling == "sync":
            assert name in SYNC_CORE_SETTINGS_BLOBS
        else:
            assert name in NEVER_SYNC_CORE_SETTINGS_BLOBS
    # The two sets are disjoint, so no blob is both replicated and excluded.
    assert SYNC_CORE_SETTINGS_BLOBS.isdisjoint(NEVER_SYNC_CORE_SETTINGS_BLOBS)


def test_the_category_is_wired_and_not_merely_declared():
    """THE TRAP. A category key alone would be inert.

    ``core_settings`` is deliberately absent from ``_SECTION_TO_ENTITY`` (it is
    a key/value blob, not an entity list), and the plan assembler iterates that
    table — so being in ``SYNC_CONFIG_CATEGORIES`` proves the GATHER fetches it
    and nothing more. The step is what makes it move, and this asserts both
    halves plus the fact that makes the first half insufficient.
    """
    assert "core_settings" in SYNC_CONFIG_CATEGORIES
    assert "core_settings" not in _SECTION_TO_ENTITY  # the trap itself
    steps = [s.entity_type for s in sync_config_importer_steps()]
    assert EntityType.SETTINGS in steps


def test_the_settings_step_runs_after_the_categories_its_fks_resolve_through():
    """``stream_settings`` carries USER_AGENT and STREAM_PROFILE pks.

    A step ordered before either namespace is populated resolves nothing and
    drops both defaults — the ``…-hiacv`` failure shape, one layer down.
    """
    order = [s.entity_type for s in sync_config_importer_steps()]
    assert order.index(EntityType.USER_AGENT) < order.index(EntityType.SETTINGS)
    assert order.index(EntityType.STREAM_PROFILE) < order.index(EntityType.SETTINGS)


# ---------------------------------------------------------------------------
# 4-5. The register is a chokepoint, and the one exclusion is code-enforced.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blob", sorted(SYNC_CORE_SETTINGS_BLOBS))
def test_every_replicated_blob_survives_the_selector(blob):
    gathered = {name: {"k": 1} for name in BLOB_RULINGS}
    assert blob in select_core_settings_blobs(gathered)


def test_network_access_cannot_be_opted_back_into():
    """The one surviving exclusion is code-enforced, not configuration.

    Its harm — replicating A's per-endpoint CIDR allowlist onto a replica that
    sits elsewhere on the network either locks the operator out of B or opens B
    up — does not become acceptable because someone ticked a box. The opt-out
    list can only ever NARROW.
    """
    gathered = {name: {"k": 1} for name in BLOB_RULINGS}
    assert "network_access" not in select_core_settings_blobs(gathered)
    # …not even when a target names it, which would be the "opt in" reading.
    assert "network_access" not in select_core_settings_blobs(
        gathered, frozenset({"network_access"})
    )


def test_an_unknown_future_blob_does_not_cross_until_someone_decides():
    """The one place the faithful-copy default is deliberately inverted.

    A settings blob's CONTENT is unknowable in advance — it could be an
    allowlist, a path, a token — so a blob Dispatcharr adds tomorrow waits for a
    ruling in the register. That is the ADR's own mechanism for "every exclusion
    is named", applied to a surface where replicating blind is the risk.
    """
    gathered = {"stream_settings": {"a": 1}, "brand_new_blob_0_30_0": {"b": 2}}
    assert set(select_core_settings_blobs(gathered)) == {"stream_settings"}


@pytest.mark.parametrize("blob", ["proxy_settings", "backup_settings"])
def test_the_two_risk_bearing_blobs_can_be_declined_per_target(blob):
    """The ADR's answer to both tensions: opt-out, not omission.

    Declining one blob must not cost the operator the others — that is the
    difference between a named exclusion and a silent one.
    """
    gathered = {name: {"k": 1} for name in BLOB_RULINGS}
    kept = select_core_settings_blobs(gathered, frozenset({blob}))
    assert blob not in kept
    assert "stream_settings" in kept
    assert "system_settings" in kept


def test_an_unreadable_opt_out_column_excludes_nothing():
    """A value that cannot be trusted fails TOWARD the faithful copy.

    The other direction would let a corrupt column silently stop settings
    replicating, which is the failure mode this bead exists to remove.
    """

    class _T:
        core_settings_excluded = "{not json"

    assert target_excluded_core_settings(_T()) == frozenset()

    class _Ok:
        core_settings_excluded = json.dumps(["proxy_settings"])

    assert target_excluded_core_settings(_Ok()) == frozenset({"proxy_settings"})


# ---------------------------------------------------------------------------
# 6-9. The SILENT FK problem the 2026-08-21 ruling did not account for.
# ---------------------------------------------------------------------------


def test_a_resolvable_stream_settings_fk_is_rewritten_to_the_replicas_id():
    remap = IdRemapTable()
    remap.add(EntityType.USER_AGENT, 4, 5004)
    remap.add(EntityType.STREAM_PROFILE, 9, 5009)
    values, dropped = remap_stream_settings_fks(
        {
            "default_user_agent": 4,
            "default_stream_profile": 9,
            "default_output_format": "mpegts",
        },
        remap,
    )
    assert values["default_user_agent"] == 5004
    assert values["default_stream_profile"] == 5009
    assert values["default_output_format"] == "mpegts"
    assert dropped == []


def test_an_unresolvable_fk_is_dropped_rather_than_pointed_at_a_random_row():
    """The whole point. Dispatcharr validates nothing here.

    A settings blob is a JSON value: forwarding A's id would simply store the
    integer, and B's default user agent would then be whichever row happens to
    hold that number. No 400, no counter, no report — which is why this is
    strictly more dangerous than the ``…-9h6cv`` / ``…-g8tyd`` cases it
    resembles, and why "unresolvable" must mean "unset" and never "as-is".
    """
    values, dropped = remap_stream_settings_fks(
        {"default_user_agent": 4, "default_stream_profile": 9}, IdRemapTable()
    )
    assert "default_user_agent" not in values
    assert "default_stream_profile" not in values
    assert sorted(dropped) == ["default_stream_profile", "default_user_agent"]


def test_the_hdhr_output_profile_id_is_always_dropped():
    """It addresses a ``core.models.OutputProfile``; ECM has no such category.

    Same position ``server_group`` was in before bead ``…-tyrg1``, and the same
    disposition: dropped and reported, never forwarded. Dispatcharr treats an
    unresolvable value as "serve without transcoding", so absent is valid.
    """
    remap = IdRemapTable()
    remap.add(EntityType.USER_AGENT, 4, 5004)
    values, dropped = remap_stream_settings_fks(
        {"default_user_agent": 4, "hdhr_output_profile_id": 12}, remap
    )
    assert "hdhr_output_profile_id" not in values
    assert "hdhr_output_profile_id" in dropped
    # …while the resolvable sibling still crossed, so this proves an exclusion
    # rather than proving the remap never ran.
    assert values["default_user_agent"] == 5004


def test_an_explicitly_unset_default_is_preserved_not_treated_as_an_id():
    """``None`` means "no default", which is a meaningful value to replicate."""
    values, dropped = remap_stream_settings_fks(
        {"default_user_agent": None, "default_stream_profile": ""}, IdRemapTable()
    )
    assert values["default_user_agent"] is None
    assert values["default_stream_profile"] == ""
    assert dropped == []


def test_a_redaction_sentinel_is_dropped_rather_than_written_to_the_replica():
    """Writing the placeholder through is the ``…-6pilh`` defect, one layer down.

    The gather runs the deep redactor over every non-provider section, so a
    nested member whose key name looks credential-class arrives sentinelled.
    Writing that would replace B's real value with the literal string.
    """
    from credential_sentinel import REDACTION_SENTINEL

    values, _dropped = remap_stream_settings_fks(
        {"m3u_hash_key": REDACTION_SENTINEL, "default_output_format": "mpegts"},
        IdRemapTable(),
    )
    assert "m3u_hash_key" not in values
    assert values["default_output_format"] == "mpegts"


# ---------------------------------------------------------------------------
# 10. PART 2 — the never-sync column drift.
# ---------------------------------------------------------------------------


def test_the_never_sync_credential_columns_are_the_three_the_code_ships():
    """ADR-013 S3 named a fourth, ``insecure``; the code named three.

    Resolved in the ADR's favour-of-the-code direction (bead ``10wnq`` Part 2):
    ``insecure`` is a local per-target TRANSPORT FLAG describing A's connection
    to B, not a secret and not instance configuration, and it has no counterpart
    on B to overwrite. This pins the constant so a future edit cannot widen a
    load-bearing security set to match stale prose — which is the direction that
    would have been wrong.
    """
    assert SYNC_NEVER_CREDENTIAL_COLUMNS == frozenset(
        {"credentials", "credential_version", "token_revoked_at"}
    )
    assert "insecure" not in SYNC_NEVER_CREDENTIAL_COLUMNS


def test_the_adr_and_the_code_now_agree_about_that_column():
    """The other half of the reconciliation, asserted against the ADR text.

    The bead's acceptance criterion is that code and ADR AGREE — pinning only
    the constant would leave the doc free to keep saying four.
    """
    from pathlib import Path

    adr = Path(__file__).resolve().parents[3] / "docs" / "adr" / (
        "ADR-013-cross-instance-live-sync.md"
    )
    text = adr.read_text(encoding="utf-8")
    assert "the credential-freshness columns (`credentials`, `credential_version`, `token_revoked_at`)" in text
    assert "`token_revoked_at`, `insecure`)" not in text


def test_the_report_is_a_plain_settings_category_the_shared_importer_fills():
    """Sanity on the reused contract: SETTINGS is report-only, never ledgered."""
    report = RestoreReport(is_dry_run=True)
    cat = report.category(EntityType.SETTINGS)
    assert cat.created == 0


# ---------------------------------------------------------------------------
# 13-15. ACROSS THE REAL SEAM — the half a structural assertion cannot give.
#
# Everything above is unit level. "The step is in the registry" is exactly the
# kind of claim that passes on a step that was wired and inert, which is the
# failure this bead was warned about by name. These three run a real cycle
# (gather -> run_sync -> run_restore -> the reused settings importer -> B's
# stateful settings rows) and look at what the replica ended up holding.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_replicated_blobs_actually_land_on_the_replica(tmp_path):
    """The blobs cross, with the source's values, through the real path."""
    from tests.fixtures.sync_harness import StatefulDispatcharrFake, SyncHarness

    source = StatefulDispatcharrFake.seeded_source()
    dest = StatefulDispatcharrFake.empty_dest()
    harness = SyncHarness(source=source, dest=dest)
    await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert dest.core_setting("system_settings") == {
        "time_zone": "Europe/London",
        "catchup_enabled": False,
    }
    assert dest.core_setting("stream_settings")["default_output_format"] == "hls"


@pytest.mark.asyncio
async def test_network_access_never_reaches_the_replica(tmp_path):
    """The one surviving exclusion, proven against the destination's own row.

    B keeps ITS allowlist. Had A's crossed, an operator reaching B from a
    different network would be locked out of their own replica.
    """
    from tests.fixtures.sync_harness import StatefulDispatcharrFake, SyncHarness

    source = StatefulDispatcharrFake.seeded_source()
    dest = StatefulDispatcharrFake.empty_dest()
    before = dest.core_setting("network_access")
    assert before != source.core_setting("network_access"), "fixture does not differ"

    harness = SyncHarness(source=source, dest=dest)
    await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert dest.core_setting("network_access") == before


@pytest.mark.asyncio
async def test_the_replicas_default_user_agent_points_at_ITS_OWN_row(tmp_path):
    """THE SILENT DEFECT, proven end to end.

    A settings blob is a JSON value Dispatcharr stores without validating, so
    forwarding A's ``default_user_agent`` pk would have left B's default pointing
    at whatever row holds that number — with no 400, no counter and no report.
    The harness gives A and B disjoint id bases precisely so a leaked source pk
    is visible here rather than coincidentally correct.

    ``hdhr_output_profile_id`` is asserted absent in the same breath: it names a
    Dispatcharr ``OutputProfile``, ECM has no category for one, and the honest
    outcome is "unset on B" rather than "pointed at an unrelated profile".
    """
    from tests.fixtures.sync_harness import StatefulDispatcharrFake, SyncHarness

    source = StatefulDispatcharrFake.seeded_source()
    dest = StatefulDispatcharrFake.empty_dest()
    source_agent_id = source.user_agents.list()[0]["id"]

    harness = SyncHarness(source=source, dest=dest)
    await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    dest_agent_id = next(
        a["id"] for a in dest.user_agents.list() if a["name"] == "ECM UA"
    )
    landed = dest.core_setting("stream_settings")
    assert landed["default_user_agent"] == dest_agent_id
    assert landed["default_user_agent"] != source_agent_id
    assert "hdhr_output_profile_id" not in landed


@pytest.mark.asyncio
async def test_a_settings_failure_does_not_delete_the_replica(tmp_path):
    """THE BLAST-RADIUS INVARIANT: a bad settings write costs settings only.

    ``SETTINGS`` is a FATAL failure category by default, and building the
    core-settings category made that reachable on the sync path for the first
    time — before this bead ``core_settings`` synced nowhere, so the step had
    nothing to fail on. One destination that cannot answer
    ``GET /api/core/settings/``, or one blob key a version-skewed B does not
    have, would have rolled the WHOLE cycle back: every M3U account, EPG source,
    group, profile and channel the run had just created, deleted — while the
    settings themselves stayed exactly as they were, because a setting is never
    ledgered and there is no SETTINGS compensator to undo it. It fixed nothing
    and cost everything, on an unattended schedule, forever.

    The sync path therefore widens the PER-RUN non-fatal set. The archive
    restore does NOT: the PO ruled on 2026-08-03 (bead ``…-zt3kf``) that a
    one-shot restore aborts and rolls back on exactly this failure, and that
    ruling stands for the context it was made about.

    Asserted through a real cycle against a destination whose settings list is
    unreadable, because the constant being right proves nothing about whether
    ``run_sync`` actually passes it — which is how the first version of this
    change went in with a mutation-proof gap.
    """
    from tests.fixtures.sync_harness import StatefulDispatcharrFake, SyncHarness

    source = StatefulDispatcharrFake.seeded_source()
    dest = StatefulDispatcharrFake.empty_dest()

    async def _unreadable(*_args, **_kwargs):
        raise RuntimeError("destination refused the core-settings list")

    dest.get_core_setting_id_map = _unreadable

    harness = SyncHarness(source=source, dest=dest)
    report = await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    # Counted and surfaced — the failure is never silent…
    assert report.category(EntityType.SETTINGS).failed > 0
    assert report.outcome is not None
    assert report.outcome.value != "success"
    # …and the replica the cycle built is STILL THERE.
    assert [a["name"] for a in dest.m3u_accounts.list()], "the replica was rolled back"
    assert dest.server_groups.list(), "the replica was rolled back"
