"""A sync that strips an XC guide URL must not present as an unqualified success.

Bead ``enhancedchannelmanager-v7d37`` (epic ``f5a5j``). Measured on Dispatcharr
0.29.0 by reading BOTH databases after an apply that reported
``success, created 134, failed 0``:

  A  'Northwind IPTV EPG (Xtream Codes)'  url=http://.../xmltv.php?username=...
  B  'Northwind IPTV EPG (Xtream Codes)'  url=<EMPTY>   status 'error', 'No URL provided'
  A  'Northwind Local XMLTV'              url=http://provider-northwind/local-epg.xml
  B  'Northwind Local XMLTV'              url=http://provider-northwind/local-epg.xml

53 of 59 channels landed on B with no EPG link; only the 6 Standard-M3U ones
kept theirs. The credential-free URL crossed intact and the credential-bearing
one did not, which is the mechanism rather than an inference.

WHY THE URL IS STRIPPED WHOLE, AND WHY THAT IS NOT THE BUG UNDER TEST HERE.
For an Xtream Codes account the guide URL CONTAINS the credentials, so
``routers.backup._scrub_credential_urls`` cannot separate the secret from the
address and replaces the entire value with the redaction sentinel; the importer
then refuses to write the sentinel and leaves the field unset. Carrying a
partially-redacted address instead (shape (a) on the bead) is NOT attempted:
see the module docstring note in ``tasks/dbas_sync.py`` and the bead's report —
it is blocked on real-provider URL sampling, because an XC url whose PATH also
carries credentials would start leaking them the moment the whole-value rule
stopped applying.

THE BUG UNDER TEST IS THE SILENCE. The information already exists — the report
carries ``epg_links_unrestored`` and ``credential_reentry_details`` naming the
source and the ``url`` field — and the sync's one-line summary, the only surface
an unattended scheduled run ever produces, never mentioned either. The restore
path has named these action items since bead ``…-6pilh``/``…-dfkbn``
(``DbasRestoreTask._credential_reentry_suffix``); the SYNC path rendered only
the placeholder-stream clause, so everything else fell off the operator's line.

Four layers, because green at one proves nothing about the next:

1. **The producer** — which URL shape survives redaction and which does not.
2. **The destination** — what B actually STORES after a real apply: the EPG
   source's ``url`` and the channels' EPG links, read off B, not off the report.
3. **The operator's line** — the summary built from THAT report names both
   shortfalls. This is the assertion the broken code fails; it reported success.
4. **The TaskResult** — that line is what task history and the notification
   carry.

Conventions: ``docs/pytest_conventions.md``.
"""
from __future__ import annotations

import pytest

from dbas.restore_contracts import EntityType, RestoreOutcome
from tasks.dbas_sync import DbasSyncTask
from tests.fixtures.sync_harness import StatefulDispatcharrFake, SyncHarness

# The measured A-side row, verbatim in shape: an XC xmltv endpoint authenticated
# by username/password in the query string.
_XC_GUIDE_URL = (
    "http://dispatcharr-p-web:9191/xmltv.php"
    "?username=northwind_user&password=northwind_pass"
)
# The measured A-side row that crossed intact — same instance, no credential.
_PLAIN_GUIDE_URL = "http://provider-northwind/local-epg.xml"

_XC_SOURCE_NAME = "Northwind IPTV EPG (Xtream Codes)"
_PLAIN_SOURCE_NAME = "Northwind Local XMLTV"


def _source_with_guide(url: str, name: str) -> StatefulDispatcharrFake:
    """Source-A holding one EPG source at ``url`` and one channel linked to it.

    The channel's link is stamped through a real ``epg_data`` row so the gather's
    natural-key resolution (``routers.backup._resolve_epg_link_natural_keys``)
    has a ``tvg_id`` to carry — the same provenance a live A supplies.
    """
    source = StatefulDispatcharrFake.seeded_source()
    source.epg_sources.create(
        {"name": name, "source_type": "xmltv", "m3u_account": None, "url": url}
    )
    guide_row = source.epg_data.create(
        {"name": "Northwind News guide", "tvg_id": "northwind.news"}
    )
    channel = next(iter(source.channels.rows.values()))
    channel["epg_data_id"] = guide_row["id"]
    channel["tvg_id"] = None
    return source


def _strip_seeded_credentials(source: StatefulDispatcharrFake) -> None:
    """Blank every credential-class field the shared fixture seeds.

    ``seeded_source`` deliberately carries a plaintext M3U ``password``/
    ``username`` and an EPG ``api_key`` so the redaction invariant is testable.
    Redaction only rewrites TRUTHY values, so blanking them is what makes a run
    with genuinely nothing to report possible.
    """
    for row in source.m3u_accounts.rows.values():
        row["username"] = ""
        row["password"] = ""
    for row in source.epg_sources.rows.values():
        if row.get("api_key"):
            row["api_key"] = ""


def _b_row(dest: StatefulDispatcharrFake, name: str) -> dict:
    return next(row for row in dest.epg_sources.list() if row["name"] == name)


# ---------------------------------------------------------------------------
# 1. THE PRODUCER — which shape survives, and which does not.
# ---------------------------------------------------------------------------


def test_only_the_credential_bearing_guide_url_is_stripped():
    """Pins the measured contrast: the plain URL crosses, the XC one does not.

    ``None`` means "left byte-identical". Asserting on the pair rather than on
    the XC url alone is what makes this a mechanism test: a redactor that ate
    every URL would also produce an empty ``url`` on B.
    """
    from routers.backup import REDACTED, _scrub_credential_urls

    assert _scrub_credential_urls(_PLAIN_GUIDE_URL) is None
    assert _scrub_credential_urls(_XC_GUIDE_URL) == REDACTED


@pytest.mark.parametrize(
    "url, stripped_whole",
    [
        # Query-string credentials — the shape measured on 0.29.0.
        ("http://p.test:9191/xmltv.php?username=u&password=p", True),
        # The same, with the extra parameters a playlist endpoint carries.
        ("http://p.test:9191/get.php?username=u&password=p&type=m3u_plus", True),
        # RFC 3986 userinfo, a different carrier for the same secret.
        ("http://u:p@p.test:9191/xmltv.php", True),
        # No credential anywhere — the address must survive, or every restored
        # source would be left pointing nowhere.
        ("http://provider-northwind/local-epg.xml", False),
        # PATH-SEGMENT credentials, with NO credential values supplied. Still not
        # recognized, and that remains correct: no GENERAL rule separates
        # ``/u/p/`` from an ordinary path without guessing, and guessing costs
        # the operator an address the restore needs.
        #
        # ANSWERED SINCE, by bead ``…-msqf7``. The real-provider sample this row
        # was waiting for came back: every one of that provider's 1.4 million
        # stream URLs carries the pair in path segments, so the shape is not
        # hypothetical. The fix is not a general rule — it is a LITERAL match
        # against the values the source instance holds, passed in as
        # ``secrets``/``identities``. With them supplied this same URL IS
        # rewritten; see
        # ``tests/tasks/test_msqf7_stream_url_credential_leak.py``. This row pins
        # the UNPARAMETERIZED default, which every other caller still gets.
        ("http://p.test:9191/xmltv/u/p/guide.xml", False),
    ],
)
def test_which_url_shapes_the_redactor_can_and_cannot_see(url, stripped_whole):
    from routers.backup import REDACTED, _scrub_credential_urls

    result = _scrub_credential_urls(url)
    assert (result == REDACTED) is stripped_whole
    if not stripped_whole:
        assert result is None  # byte-identical, not partially rewritten


@pytest.mark.asyncio
async def test_the_report_is_the_same_whichever_carrier_the_credential_uses(tmp_path):
    """Shape-independence of the FIX, stated as the property rather than the
    example. The operator's line must not depend on whether the provider put the
    secret in the query string or in the userinfo — both lose the address, so
    both are the same action item.
    """
    source = _source_with_guide("http://u:p@p.test:9191/xmltv.php", _XC_SOURCE_NAME)
    dest = StatefulDispatcharrFake.empty_dest()

    report = await SyncHarness(source=source, dest=dest).run(
        confirm_apply=True, ledger_dir=tmp_path
    )
    message = DbasSyncTask._summary_message(report, False, report.outcome.value)

    assert not _b_row(dest, _XC_SOURCE_NAME).get("url")
    assert "1 source(s) need their URL re-entered" in message
    assert "1 channel(s) restored without an EPG link" in message


# ---------------------------------------------------------------------------
# 2. THE DESTINATION — what B stores after a real apply.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b_stores_an_addressless_xc_source_and_an_unlinked_channel(tmp_path):
    """Read off B: no url on the source, no EPG link on the channel.

    This is the state the operator opens, and it is asserted on B's stored rows
    rather than on the report, because the report is the thing that lied. The
    causal chain is reproduced end to end: no url on B means B never downloads a
    guide, means no ``epg_data`` row carries the channel's ``tvg_id``, means the
    link cannot be reattached.
    """
    source = _source_with_guide(_XC_GUIDE_URL, _XC_SOURCE_NAME)
    dest = StatefulDispatcharrFake.empty_dest()

    report = await SyncHarness(source=source, dest=dest).run(
        confirm_apply=True, ledger_dir=tmp_path
    )

    # The source row exists on B — the topology converged — with no address.
    assert not _b_row(dest, _XC_SOURCE_NAME).get("url")
    # And no channel on B carries a guide link.
    assert [c.get("epg_data_id") for c in dest.channels.list()] == [None]
    # While the run itself is a clean success by every count it reports.
    assert report.outcome == RestoreOutcome.SUCCESS
    assert sum(c.failed for c in report.categories) == 0
    # The shortfall IS measured — it is simply not on any operator surface.
    assert report.epg_links_unrestored == 1
    # And it is the ADDRESS that was lost, not a password beside it. (The
    # harness's baseline source also carries an M3U password and an EPG api_key
    # that are correctly redacted, so this asserts on the XC row specifically
    # rather than on the list being a singleton.)
    xc_detail = next(
        d for d in report.credential_reentry_details if d.label == _XC_SOURCE_NAME
    )
    assert xc_detail.fields == ["url"]
    assert xc_detail.entity_type == EntityType.EPG_SOURCE


@pytest.mark.asyncio
async def test_a_credential_free_guide_url_reaches_b_intact(tmp_path):
    """The control. Same pipeline, same shape, no credential — B keeps the address."""
    source = _source_with_guide(_PLAIN_GUIDE_URL, _PLAIN_SOURCE_NAME)
    dest = StatefulDispatcharrFake.empty_dest()

    report = await SyncHarness(source=source, dest=dest).run(
        confirm_apply=True, ledger_dir=tmp_path
    )

    assert _b_row(dest, _PLAIN_SOURCE_NAME)["url"] == _PLAIN_GUIDE_URL
    assert report.outcome == RestoreOutcome.SUCCESS
    # Nothing about this source is an address action item.
    assert not [
        d for d in report.credential_reentry_details if "url" in d.fields
    ]


# ---------------------------------------------------------------------------
# 3. THE OPERATOR'S LINE — built from the report layer 2 produced.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_summary_names_the_lost_address_and_the_lost_links(tmp_path):
    """The assertion the broken code fails.

    Before the fix this read exactly ``Sync success: created N, updated 0,
    failed 0 across M categories`` — the 0.29.0 measurement's own words — with
    no clause naming either shortfall.
    """
    source = _source_with_guide(_XC_GUIDE_URL, _XC_SOURCE_NAME)
    dest = StatefulDispatcharrFake.empty_dest()

    report = await SyncHarness(source=source, dest=dest).run(
        confirm_apply=True, ledger_dir=tmp_path
    )
    message = DbasSyncTask._summary_message(report, False, report.outcome.value)

    # The channels that arrived without guide data.
    assert "1 channel(s) restored without an EPG link" in message
    # And WHY, phrased as the action the operator can actually take: there is no
    # password field to fill in, the ADDRESS is what is missing.
    assert "1 source(s) need their URL re-entered" in message
    # Never the credential itself, on any surface.
    assert "northwind_pass" not in message
    assert "northwind_user" not in message


@pytest.mark.asyncio
async def test_a_clean_sync_gains_no_clause(tmp_path):
    """Anti-crying-wolf (bead ``…-15g1j``): only report what the sync FAILED to
    deliver. A cycle that carried everything must gain no clause at all —
    otherwise the clause stops meaning anything and operators learn to skip it.

    Everything the pipeline could report on is deliberately made deliverable
    here: no credential to strip anywhere on A, a credential-free guide URL, and
    a destination that already holds the guide row the channel's link resolves
    through.
    """
    source = _source_with_guide(_PLAIN_GUIDE_URL, _PLAIN_SOURCE_NAME)
    _strip_seeded_credentials(source)
    dest = StatefulDispatcharrFake.empty_dest()
    dest.epg_data.create({"name": "B guide", "tvg_id": "northwind.news"})

    report = await SyncHarness(source=source, dest=dest).run(
        confirm_apply=True, ledger_dir=tmp_path
    )

    assert report.epg_links_unrestored == 0
    assert report.credentials_needing_reentry == 0
    message = DbasSyncTask._summary_message(report, False, report.outcome.value)
    assert message == (
        "Sync success: created %d, updated %d, failed 0 across %d categories"
        % (
            sum(c.created for c in report.categories),
            sum(c.updated for c in report.categories),
            len(report.categories),
        )
    )


@pytest.mark.asyncio
async def test_the_preview_says_the_same_thing_in_the_future_tense(tmp_path):
    """A dry run changed nothing, so its clause must not read as history
    (bead ``…-juu3c``). The credential clause is already tense-neutral."""
    source = _source_with_guide(_XC_GUIDE_URL, _XC_SOURCE_NAME)
    dest = StatefulDispatcharrFake.empty_dest()

    report = await SyncHarness(source=source, dest=dest).run(ledger_dir=tmp_path)
    message = DbasSyncTask._summary_message(report, True, "dry_run")

    assert "1 source(s) need their URL re-entered" in message
    # A preview never CLAIMS an EPG-link loss — it cannot know one (…-15g1j);
    # the clause it must not print is the past-tense apply wording.
    assert "restored without an EPG link" not in message


# ---------------------------------------------------------------------------
# 4. THE TASKRESULT — the line task history and the notification carry.
# ---------------------------------------------------------------------------


def test_the_address_clause_counts_entities_not_the_aggregate():
    """Structure trap. The credential signal is a top-level ``int`` aggregate
    PLUS a per-entity detail list, and only the detail list knows WHICH field was
    lost. An aggregate with no details (an older report, or a producer that only
    bumped the counter) must still be described — as the generic credential
    clause, never silently dropped."""
    from dbas.restore_contracts import RestoreReport
    from tasks.dbas_restore import DbasRestoreTask

    report = RestoreReport(is_dry_run=False)
    report.record_credential_reentry(EntityType.EPG_SOURCE, "XC guide", ["url"])
    report.record_credential_reentry(
        EntityType.M3U_ACCOUNT, "XC playlist", ["password"]
    )
    suffix = DbasRestoreTask._credential_reentry_suffix(report)

    assert "1 source(s) need their URL re-entered" in suffix
    assert "1 account(s) need credentials re-entered" in suffix

    # Aggregate-only (no details): the generic clause covers all of it.
    bare = RestoreReport(is_dry_run=False, credentials_needing_reentry=3)
    assert (
        DbasRestoreTask._credential_reentry_suffix(bare)
        == "; 3 account(s) need credentials re-entered"
    )


def test_more_address_details_than_the_aggregate_never_renders_a_negative_count():
    """The other direction of the same drift, and the reason for the clamp.

    Reports are not always built through ``record_credential_reentry`` — the
    preview-tense suite constructs them field-by-field, and an artifact written
    by an older ECM can be rehydrated the same way. If the details ever outrun
    the aggregate, subtracting them yields "-1 account(s) need credentials
    re-entered", which is worse than saying nothing: it is a number the operator
    cannot reconcile with anything else on the line.
    """
    from dbas.restore_contracts import CredentialReentryDetail, RestoreReport
    from tasks.dbas_restore import DbasRestoreTask

    report = RestoreReport(
        is_dry_run=False,
        credentials_needing_reentry=1,
        credential_reentry_details=[
            CredentialReentryDetail(
                entity_type=EntityType.EPG_SOURCE, label="XC guide", fields=["url"]
            ),
            CredentialReentryDetail(
                entity_type=EntityType.M3U_ACCOUNT,
                label="XC playlist",
                fields=["server_url"],
            ),
        ],
    )
    suffix = DbasRestoreTask._credential_reentry_suffix(report)

    assert suffix == (
        "; 1 source(s) need their URL re-entered (the address carried the "
        "credentials, so it could not be copied)"
    )


def test_the_sync_preview_renders_its_predictions_in_the_future_tense():
    """A sync PREVIEW changed nothing, so its action items must not read as
    history (bead ``…-juu3c``, the restore path's rule).

    Asserted on a counter a dry run genuinely PREDICTS. The XC scenario above
    cannot exercise this: a preview never claims an EPG-link loss (…-15g1j), so
    the tense-sensitive template it would use is never reached there, and a
    preview that silently rendered "corrected" instead of "would be corrected"
    would go unnoticed.
    """
    from dbas.restore_contracts import RestoreReport

    report = RestoreReport(is_dry_run=True, profile_membership_drift=2)
    preview = DbasSyncTask._summary_message(report, True, "dry_run")
    applied = DbasSyncTask._summary_message(
        RestoreReport(is_dry_run=False, profile_membership_drift=2),
        False,
        "success",
    )

    assert "2 profile membership(s) would be corrected" in preview
    assert "2 profile membership(s) corrected" in applied
    assert "would" not in applied.split("across")[-1]
