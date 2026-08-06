"""A preview describes what WOULD happen; it must not use the past tense.

Bead ``enhancedchannelmanager-juu3c``. ``DbasRestoreTask._credential_reentry_suffix``
is appended to the DRY-RUN summary as well as the apply's, so after PR #784 made
``profile_membership_drift`` genuinely PREDICTED, a preview read::

    Dry-run complete: would create 32, update 0, skip 0, 0 conflict(s) across 8
    categories; 6 profile membership(s) corrected

The number is right. The tense is not: a preview makes no changes, so nothing was
corrected. This matters more than a typo because the whole point of the #784 work
is that an operator can trust a preview BEFORE committing — and a past-tense verb
reads as though the restore already happened, which is precisely the confusion
that made the drill's ``Restore success: created 32, failed 0`` so misleading in
the first place.

WHAT IS AND IS NOT IN SCOPE

Wording only. The counters, the clause ORDER and the summary structure are
identical either way, and the tests below pin that: the same report rendered both
ways must differ ONLY in the verbs.

``credentials_needing_reentry`` is deliberately untouched — "N account(s) need
credentials re-entered" is an is/will-be statement that is already true of both a
preview and an apply. The two stream-health counters are ``None`` (not predicted)
on a preview, so the tense question never arises for them; that is asserted here
too so a future change that starts predicting them does not silently reintroduce
a past-tense preview clause.

Conventions: ``docs/pytest_conventions.md``.
"""
from __future__ import annotations

from dbas.restore_contracts import RestoreReport
from tasks.dbas_restore import DbasRestoreTask


def _report(**counters) -> RestoreReport:
    """A report carrying only the action-item counters under test."""
    report = RestoreReport(is_dry_run=True)
    for name, value in counters.items():
        setattr(report, name, value)
    return report


# ---------------------------------------------------------------------------
# 1. THE DEFECT — the measured clause
# ---------------------------------------------------------------------------


def test_a_preview_says_profile_memberships_would_be_corrected():
    """THE measured wording. A preview corrected nothing — it has not run yet."""
    suffix = DbasRestoreTask._credential_reentry_suffix(
        _report(profile_membership_drift=6), is_preview=True
    )

    assert "6 profile membership(s) would be corrected" in suffix
    assert "6 profile membership(s) corrected" not in suffix


def test_an_apply_still_says_profile_memberships_were_corrected():
    """The converse, unchanged: an apply DID the work and says so."""
    suffix = DbasRestoreTask._credential_reentry_suffix(
        _report(profile_membership_drift=6)
    )

    assert suffix == "; 6 profile membership(s) corrected"


# ---------------------------------------------------------------------------
# 2. THE SIBLING CLAUSES — same defect, same summary builder
# ---------------------------------------------------------------------------


def test_a_preview_uses_the_future_tense_for_every_action_item():
    """Both siblings are past-tense on an apply and must not be on a preview."""
    report = _report(logo_misses=11, epg_links_unrestored=12)

    preview = DbasRestoreTask._credential_reentry_suffix(report, is_preview=True)

    assert "11 logo(s) would not be reinstated" in preview
    assert "12 channel(s) would be restored without an EPG link" in preview
    assert "could not be reinstated" not in preview
    assert "channel(s) restored without" not in preview


def test_an_apply_is_unchanged_for_every_action_item():
    """The apply wording is the shipped wording and does not move."""
    report = _report(logo_misses=11, epg_links_unrestored=12)

    applied = DbasRestoreTask._credential_reentry_suffix(report)

    assert "11 logo(s) could not be reinstated" in applied
    assert "12 channel(s) restored without an EPG link" in applied
    assert "would" not in applied


def test_the_credential_clause_reads_the_same_either_way():
    """"N account(s) need credentials re-entered" is already tense-neutral.

    It describes state the operator must fix, which is equally true of a
    prediction and of a completed restore. Rewording it would be churn.
    """
    report = _report(credentials_needing_reentry=2)

    assert (
        DbasRestoreTask._credential_reentry_suffix(report, is_preview=True)
        == DbasRestoreTask._credential_reentry_suffix(report)
        == "; 2 account(s) need credentials re-entered"
    )


# ---------------------------------------------------------------------------
# 3. STRUCTURE IS UNCHANGED — only the verbs move
# ---------------------------------------------------------------------------


def test_only_the_verbs_differ_between_a_preview_and_an_apply():
    """Same counters, same clauses, same order — wording only.

    Comparing the two renderings clause-by-clause is what pins "wording only":
    a change that dropped, reordered or recounted a clause on the preview would
    fail here even though every individual phrase assertion above still passed.
    """
    report = _report(
        credentials_needing_reentry=2,
        logo_misses=11,
        epg_links_unrestored=12,
        profile_membership_drift=6,
    )

    preview = DbasRestoreTask._credential_reentry_suffix(report, is_preview=True)
    applied = DbasRestoreTask._credential_reentry_suffix(report)

    assert len(preview.split("; ")) == len(applied.split("; ")) == 5
    # Every clause carries the same count, in the same position.
    for predicted, done in zip(preview.split("; "), applied.split("; ")):
        assert [w for w in predicted.split() if w.isdigit()] == [
            w for w in done.split() if w.isdigit()
        ]


def test_a_clean_preview_has_no_suffix_at_all():
    """No action items, no clause — on either rendering."""
    report = RestoreReport(is_dry_run=True)

    assert DbasRestoreTask._credential_reentry_suffix(report, is_preview=True) == ""
    assert DbasRestoreTask._credential_reentry_suffix(report) == ""


# ---------------------------------------------------------------------------
# 4. THE SUMMARY LINE ITSELF — the surface an operator actually reads
# ---------------------------------------------------------------------------


def test_the_dry_run_summary_line_carries_the_future_tense():
    """End of the chain: the task-history row and MCP result an operator sees."""
    report = _report(profile_membership_drift=6)

    message = DbasRestoreTask._summary_message(report, is_apply=False)

    assert message.startswith("Dry-run complete:")
    assert message.endswith("; 6 profile membership(s) would be corrected")


def test_the_apply_summary_line_is_unchanged():
    """The apply path did the work, and its sentence is untouched."""
    report = RestoreReport(is_dry_run=False)
    report.profile_membership_drift = 6

    message = DbasRestoreTask._summary_message(report, is_apply=True)

    assert message.endswith("; 6 profile membership(s) corrected")


def test_the_stream_health_counters_stay_unpredicted_on_a_preview():
    """They are NULL on a dry run, so no tense question arises for them.

    Pinned so a future change that starts predicting them cannot silently
    reintroduce a past-tense preview clause through this same builder.
    """
    report = RestoreReport(is_dry_run=True)
    report.mark_stream_health_unpredicted()

    assert report.channels_needing_stream_reattach is None
    assert report.channels_with_no_playable_stream is None
    assert DbasRestoreTask._credential_reentry_suffix(report, is_preview=True) == ""
