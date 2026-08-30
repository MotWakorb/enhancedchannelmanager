"""A redacted stream URL is a DEAD ADDRESS, not a playable one, and not an identity.

Bead ``enhancedchannelmanager-1td94``. A regression produced by two commits on
``fix/f5a5j-replica-fidelity`` blinding each other, measured live on Dispatcharr
0.29.0 by reading B's Postgres and by FETCHING, never from the run's own report::

    53 of B's channels bound to streams whose upstream returns HTTP 404
    channels_with_no_playable_stream: 0

THE TWO HALVES, and each is independently load-bearing.

**Half 1 — PLAYABILITY.** Bead ``…-msqf7`` rewrites a credential-bearing Xtream
Codes stream URL to ``https://<host>/live/***REDACTED***/***REDACTED***/<id>.ts``
rather than dropping it, so the operator can still see where the stream pointed.
Bead ``…-kcfru``'s playability predicate is BARE TRUTHINESS on the ``url`` field.
A redacted URL is a non-empty string, so every one of those 53 dead streams
counted as playable and the replica reported entirely healthy.

**Half 2 — IDENTITY.** The archived record carries the REDACTED url too, and the
placeholder ``custom_stream_fallback`` synthesized from it is the ONE destination
row whose url is exactly that string. So the placeholder is its own perfect
Tier-1 (EXACT URL) match, on the highest-confidence rung of the ladder, forever::

    archived RAW url       -> tier=1 match_id=118   (real stream, replicated account)
    archived REDACTED url  -> tier=1 match_id=7     (placeholder, synthetic account)

B's live binding was 7. Fixing only half 1 leaves that mis-binding permanent —
honestly reported, and never healed. Fixing only half 2 leaves the false
``0 unplayable`` on the cycle that creates the placeholders, when no real stream
exists to match instead. Both halves are proven separately below.

WHAT THIS SUITE DOES **NOT** DO: weaken the redaction. ``…-msqf7`` closed a real
credential leak whose own live proof showed 53 provider passwords reaching B.
The sentinel is imported from :mod:`credential_sentinel`, never spelled twice.

THE INVARIANT, and the 53-channel case is one example of it: a channel bound to a
stream that cannot serve is reported as unplayable, whatever the reason the
address is dead — absent, sentinel-bearing, or otherwise. That is why the fix is
ONE named predicate (:func:`credential_sentinel.url_can_serve`) used at every
site that asks "does this stream have a usable address?", rather than a
sentinel test bolted onto each call site: a future reason an address is dead
lands in one function, not in six.

STRUCTURE USED, stated because six engineers have been caught by it:
``channels_with_no_playable_stream`` is a **top-level ``int``** on
``RestoreReport`` (``int | None``, ``None`` until a verdict is taken), with its
drill-down in ``stream_reattach_details`` (``StreamReattachDetail``). NOT
``skip_details``/``SkipReason`` and NOT ``failure_details``/``FailureReason`` —
this is a verdict on the destination's state, not a failed operation.

Conventions: ``docs/pytest_conventions.md``; the Dispatcharr client is an
``AsyncMock`` (no live upstream). Helpers are imported from the sibling suites
rather than copied so the fixtures cannot drift.
"""
from __future__ import annotations

import pytest

from credential_sentinel import REDACTION_SENTINEL
from dbas.custom_stream_fallback import CUSTOM_STREAM_ACCOUNT_NAME
from dbas.restore_contracts import (
    EntityType,
    IdRemapTable,
    RestoreOutcome,
    RestoreReport,
    RollbackLedger,
)
from dbas.restore_orchestrator import compute_outcome
from dbas.stream_matcher import MatchTier, match_stream
from tests.dbas.test_rebind_after_m3u_refresh import (
    OPERATOR_ACCOUNT_ID,
    PROVIDER_ACCOUNT_ID,
    SYNTHETIC_ACCOUNT_ID,
    _accounts,
    _real,
    _wire,
)
from tests.dbas.test_restore_state_loss import _client

# The exact shape ``…-msqf7`` puts on the wire: the credential PATH SEGMENTS are
# replaced, the rest of the address crosses intact so the operator can see which
# provider and which stream it was. Built from the imported constant — the
# sentinel is msqf7's to define and this suite only recognizes it.
REDACTED_URL = "http://provider-northwind:9191/live/%s/%s/53.ts" % (
    REDACTION_SENTINEL,
    REDACTION_SENTINEL,
)
# What the SAME stream looks like on the destination once the operator has
# re-entered their credentials and refreshed the replicated account: a real,
# fetchable address under a real provider account.
REAL_URL = "http://provider-northwind:9191/live/replica-user/replica-pass/53.ts"
STREAM_NAME = "Bayou Country"
# SOURCE A's own M3U account id, which does not exist on the destination.
# The cross-instance shape: the archived record names a provider id that is
# meaningless on B, so Tier 2 (same-provider) cannot fire and the ladder
# lands on Tier 3 — the rung bead ``…-efvyg`` floors sync at.
SOURCE_ACCOUNT_ID = 41


def _redacted_placeholder(
    stream_id: int,
    name: str = STREAM_NAME,
    account: int = SYNTHETIC_ACCOUNT_ID,
) -> dict:
    """A placeholder carrying a SENTINEL-bearing url, as msqf7 now produces.

    ``custom_stream_fallback`` forwards the archived ``url`` verbatim, so on a
    full-fidelity cross-instance sync the synthesized placeholder inherits the
    redacted address rather than being URL-less. That single fact is what this
    whole bead is about, so the fixture must carry it rather than reuse the
    URL-less placeholder the older suites were written against.
    """
    return {"id": stream_id, "name": name, "url": REDACTED_URL, "m3u_account": account}


# ===========================================================================
# HALF 1 — PLAYABILITY: a sentinel-bearing url cannot serve
# ===========================================================================


@pytest.mark.asyncio
async def test_a_channel_on_a_redacted_placeholder_is_counted_unplayable():
    """THE measured defect: 53 channels fetch HTTP 404, report says 0 unplayable.

    One channel, bound to one placeholder whose url carries the sentinel and
    nothing else. Fetching it returns 404 — the address names a path the
    provider has never heard of. The verdict must say so.
    """
    from dbas.placeholder_rebind import rebind_placeholder_streams

    client = _client()
    client.get_streams.return_value = {"results": [_redacted_placeholder(500)]}
    client.get_channels.return_value = {
        "results": [{"id": 201, "name": "Bayou", "streams": [500]}]
    }

    report = RestoreReport(is_dry_run=False)
    ledger = RollbackLedger(restore_id="t")
    ledger.record_created(EntityType.STREAM, 500, STREAM_NAME)
    remap = IdRemapTable()
    remap.add(EntityType.STREAM, 7, 500)
    remap.add(EntityType.CHANNEL, 101, 201)

    await rebind_placeholder_streams(
        allow_fuzzy=True,
        client=client,
        report=report,
        ledger=ledger,
        remap=remap,
        archive_channels=[
            {
                "id": 101,
                "name": "Bayou",
                "streams": [{"id": 7, "name": STREAM_NAME, "url": REDACTED_URL}],
            }
        ],
    )

    # Top-level int on RestoreReport — not skip_details, not failure_details.
    assert report.channels_with_no_playable_stream == 1
    assert report.stream_reattach_details[0].has_playable_stream is False
    assert compute_outcome(
        report=report, failure_occurred=False, rollback=None
    ) == RestoreOutcome.COMPLETED_WITH_FAILURES


@pytest.mark.asyncio
async def test_the_redacted_verdict_holds_on_every_cycle_not_only_the_creating_one():
    """Steady state is part of the property (bead ``…-kcfru``), not an extension.

    Cycle 2 runs with an EMPTY ledger — this run synthesized nothing, so the
    placeholder is outside its write authority. The verdict must still fire,
    because it is a function of the DESTINATION's state alone. A single-apply
    test is exactly what let kcfru's defect through, so this one runs the pass
    twice over an unchanged destination and asserts the answer does not move.
    """
    from dbas.placeholder_rebind import rebind_placeholder_streams

    archive_channels = [
        {
            "id": 101,
            "name": "Bayou",
            "streams": [{"id": 7, "name": STREAM_NAME, "url": REDACTED_URL}],
        }
    ]

    verdicts: list[int | None] = []
    for _cycle in range(2):
        client = _client()
        client.get_streams.return_value = {"results": [_redacted_placeholder(500)]}
        client.get_channels.return_value = {
            "results": [{"id": 201, "name": "Bayou", "streams": [500]}]
        }
        report = RestoreReport(is_dry_run=False)
        remap = IdRemapTable()
        remap.add(EntityType.CHANNEL, 101, 201)

        await rebind_placeholder_streams(
            allow_fuzzy=True,
            client=client,
            report=report,
            # EMPTY ledger: a later cycle created nothing of its own.
            ledger=RollbackLedger(restore_id="t"),
            remap=remap,
            archive_channels=archive_channels,
        )
        verdicts.append(report.channels_with_no_playable_stream)

    assert verdicts == [1, 1]


@pytest.mark.asyncio
async def test_a_channel_on_a_real_url_is_not_reported():
    """The control that stops this becoming a second crying-wolf signal.

    Bead ``…-15g1j``'s faithful-versus-undelivered distinction: a replica that
    received a stream which actually plays has lost nothing and must not appear
    in the report at all.
    """
    from dbas.placeholder_rebind import rebind_placeholder_streams

    client = _client()
    client.get_streams.return_value = {
        "results": [
            {
                "id": 900,
                "name": STREAM_NAME,
                "url": REAL_URL,
                "m3u_account": PROVIDER_ACCOUNT_ID,
            }
        ]
    }
    client.get_channels.return_value = {
        "results": [{"id": 201, "name": "Bayou", "streams": [900]}]
    }

    report = RestoreReport(is_dry_run=False)
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 201)

    await rebind_placeholder_streams(
        allow_fuzzy=True,
        client=client,
        report=report,
        ledger=RollbackLedger(restore_id="t"),
        remap=remap,
        archive_channels=[
            {
                "id": 101,
                "name": "Bayou",
                "streams": [{"id": 7, "name": STREAM_NAME, "url": REAL_URL}],
            }
        ],
    )

    assert not report.stream_reattach_details
    assert report.channels_with_no_playable_stream in (None, 0)


@pytest.mark.asyncio
async def test_a_slot_is_never_rebound_onto_a_redacted_stream():
    """WRITE AUTHORITY: a dead address is not a rebind TARGET either.

    This run synthesized URL-less placeholder 500. Stream 600 shares its name and
    carries a url — but a REDACTED one, left behind by an earlier cycle. Under
    bare truthiness 600 is a candidate, so the pass "heals" the channel by moving
    it from one dead stream to another and counts a rebind. The channel must keep
    500 and be reported unplayable instead.

    This NARROWS the candidate set; it does not widen write authority. Bead
    ``…-kcfru``'s separation is untouched — ``candidates`` is still ledger-scoped
    and only ledgered ids are ever rebound away from.
    """
    from dbas.placeholder_rebind import rebind_placeholder_streams

    client = _client()
    client.get_streams.return_value = {
        "results": [
            {
                "id": 500,
                "name": STREAM_NAME,
                "url": None,
                "m3u_account": SYNTHETIC_ACCOUNT_ID,
            },
            _redacted_placeholder(600),
        ]
    }
    client.get_channels.return_value = {
        "results": [{"id": 201, "name": "Bayou", "streams": [500]}]
    }

    report = RestoreReport(is_dry_run=False)
    ledger = RollbackLedger(restore_id="t")
    ledger.record_created(EntityType.STREAM, 500, STREAM_NAME)
    remap = IdRemapTable()
    remap.add(EntityType.STREAM, 7, 500)
    remap.add(EntityType.CHANNEL, 101, 201)

    result = await rebind_placeholder_streams(
        allow_fuzzy=True,
        client=client,
        report=report,
        ledger=ledger,
        remap=remap,
        archive_channels=[
            {
                "id": 101,
                "name": "Bayou",
                "streams": [{"id": 7, "name": STREAM_NAME, "url": REDACTED_URL}],
            }
        ],
    )

    assert result.rebound == 0
    client.update_channel.assert_not_awaited()
    assert report.channels_with_no_playable_stream == 1


# ===========================================================================
# HALF 2 — IDENTITY: a redacted url is the ABSENCE of an identity
# ===========================================================================


def test_a_redacted_archived_url_never_wins_tier_1():
    """The permanent mis-binding, at its source.

    The archived record and the placeholder carry the SAME redacted string, so
    the placeholder is a byte-exact Tier-1 match for the record that produced it
    — the strongest rung on the ladder, beating the real stream that arrives
    later on Tiers 2–3. A sentinel is not an identity; it is what is left when
    the identity has been removed, and it must not be compared for equality.
    """
    archived = {
        "id": 7,
        "name": STREAM_NAME,
        "url": REDACTED_URL,
        "m3u_account": SOURCE_ACCOUNT_ID,
    }
    candidates = [
        _redacted_placeholder(7),
        {
            "id": 118,
            "name": STREAM_NAME,
            "url": REAL_URL,
            "m3u_account": PROVIDER_ACCOUNT_ID,
        },
    ]

    tier, match_id = match_stream(archived, candidates, allow_fuzzy=False)

    assert tier != MatchTier.EXACT_URL
    assert match_id == 118


def test_a_servable_candidate_beats_an_unservable_one_inside_the_same_tier():
    """The tie-break, which is where the placeholder wins if Tier 1 is all we fix.

    Tier 3 admits both rows on an identical normalized name and breaks the tie on
    LOWEST ID — and the placeholder was created first, so it has the lower id.
    This is the live shape exactly: placeholder 7 versus real stream 118. Closing
    only Tier 1 moves the wrong answer down a rung; it does not remove it.

    The tier NUMBER is unchanged by this preference — Tier 3 stays Tier 3, and
    bead ``…-efvyg``'s Tier-3 floor for sync is untouched.
    """
    archived = {"id": 7, "name": STREAM_NAME, "m3u_account": SOURCE_ACCOUNT_ID}
    candidates = [
        _redacted_placeholder(7),
        {
            "id": 118,
            "name": STREAM_NAME,
            "url": REAL_URL,
            "m3u_account": PROVIDER_ACCOUNT_ID,
        },
    ]

    tier, match_id = match_stream(archived, candidates, allow_fuzzy=False)

    assert tier == MatchTier.EXACT_NORMALIZED_NAME
    assert match_id == 118


def test_a_url_less_candidate_also_loses_to_a_servable_one():
    """The invariant, not the reproduction: ABSENT is as dead as SENTINEL-BEARING.

    Same shape as above with an ordinary URL-less placeholder in place of the
    redacted one. If the preference were written against the sentinel rather than
    against "can this address serve", this case would still hand back the dead
    stream — and a URL-less placeholder outranking a real stream is the same
    defect by a different route.
    """
    archived = {"id": 7, "name": STREAM_NAME, "m3u_account": SOURCE_ACCOUNT_ID}
    candidates = [
        {
            "id": 7,
            "name": STREAM_NAME,
            "url": None,
            "m3u_account": SYNTHETIC_ACCOUNT_ID,
        },
        {
            "id": 118,
            "name": STREAM_NAME,
            "url": REAL_URL,
            "m3u_account": PROVIDER_ACCOUNT_ID,
        },
    ]

    assert match_stream(archived, candidates, allow_fuzzy=False) == (
        MatchTier.EXACT_NORMALIZED_NAME,
        118,
    )


def test_the_placeholder_is_still_re_found_when_nothing_servable_matches():
    """The control that stops the fix causing UNBOUNDED PLACEHOLDER GROWTH.

    On a cycle where the operator has not yet re-entered their credentials there
    is no real stream to match. If the matcher MISSED here, the custom-stream
    fallback would synthesize a SECOND placeholder for the same archived stream,
    and a third on the next cycle — 53 new dead rows per unattended run, forever.
    A dead candidate is DEPRIORITIZED, never excluded.
    """
    archived = {
        "id": 7,
        "name": STREAM_NAME,
        "url": REDACTED_URL,
        "m3u_account": SOURCE_ACCOUNT_ID,
    }
    candidates = [_redacted_placeholder(7)]

    tier, match_id = match_stream(archived, candidates, allow_fuzzy=False)

    assert tier != MatchTier.MISS
    assert match_id == 7


def test_the_raw_name_preference_still_decides_among_servable_candidates():
    """Bead ``…-ixdaw``'s guarantee survives, and is subordinate to servability.

    Two servable candidates differing only in capitalisation, plus a dead one
    with the lowest id of all. The dead row must drop out first; the byte-
    identical raw name must then win over the case-folded one, exactly as before.
    """
    archived = {"id": 7, "name": "TX | Dallas | PBS KERA", "m3u_account": SOURCE_ACCOUNT_ID}
    candidates = [
        _redacted_placeholder(3, name="TX | Dallas | PBS KERA"),
        {
            "id": 101,
            "name": "TX | DALLAS | PBS KERA",
            "url": REAL_URL,
            "m3u_account": PROVIDER_ACCOUNT_ID,
        },
        {
            "id": 102,
            "name": "TX | Dallas | PBS KERA",
            "url": REAL_URL,
            "m3u_account": PROVIDER_ACCOUNT_ID,
        },
    ]

    assert match_stream(archived, candidates, allow_fuzzy=False) == (
        MatchTier.EXACT_NORMALIZED_NAME,
        102,
    )


def test_a_credential_that_merely_resembles_the_sentinel_still_matches_on_tier_1():
    """Exact-substring recognition, not a look-alike heuristic.

    ``credential_sentinel`` is emphatic that a value RESEMBLING the placeholder is
    a real value. A provider path segment of ``**REDACTED**`` (two stars, not
    three) is somebody's real address and keeps its Tier-1 identity.
    """
    url = "http://provider-northwind:9191/live/**REDACTED**/pass/53.ts"
    archived = {"id": 7, "name": STREAM_NAME, "url": url, "m3u_account": SOURCE_ACCOUNT_ID}
    candidates = [
        {"id": 55, "name": "Other", "url": url, "m3u_account": PROVIDER_ACCOUNT_ID}
    ]

    assert match_stream(archived, candidates, allow_fuzzy=False) == (
        MatchTier.EXACT_URL,
        55,
    )


# ===========================================================================
# RECOVERY — credential re-entry plus a refresh must actually heal the replica
# ===========================================================================


@pytest.mark.asyncio
async def test_a_redacted_placeholder_is_rebound_once_the_operator_re_credentials():
    """The recovery the operator reaches for FIRST must work.

    The post-refresh pass gates on "a URL-LESS stream on the synthetic account".
    A redacted placeholder carries a url, so the gate returns empty, the pass
    exits before doing anything, and the operator's credential re-entry plus
    refresh changes nothing they can see — bead ``…-2o0cz``'s drill step 2,
    reached again by a new route.
    """
    from dbas.placeholder_rebind import rebind_placeholders_after_refresh

    client = _client()
    _wire(
        client,
        streams=[_redacted_placeholder(500), _real(900, STREAM_NAME)],
        channels=[{"id": 201, "name": "Bayou", "streams": [500]}],
    )

    result = await rebind_placeholders_after_refresh(client=client, trigger="test")

    client.update_channel.assert_awaited_once_with(201, {"streams": [900]})
    assert result.rebound == 1
    assert result.channels_updated == 1


@pytest.mark.asyncio
async def test_a_freed_redacted_placeholder_is_swept_off_the_synthetic_account():
    """Otherwise every healed cycle leaves its dead rows behind forever.

    The residue sweep also tests ``not url``, so a redacted placeholder that
    nothing references any more survives every sweep. On an unattended schedule
    that is a monotonically growing population of dead streams in the matcher's
    own candidate universe.
    """
    from dbas.placeholder_rebind import rebind_placeholders_after_refresh

    client = _client()
    _wire(
        client,
        streams=[_redacted_placeholder(500), _real(900, STREAM_NAME)],
        channels=[{"id": 201, "name": "Bayou", "streams": [500]}],
    )

    result = await rebind_placeholders_after_refresh(client=client, trigger="test")

    client.delete_stream.assert_awaited_once_with(500)
    assert result.orphans_swept == 1


@pytest.mark.asyncio
async def test_the_account_envelope_still_holds_for_redacted_streams():
    """The safety envelope, re-asserted for the widened predicate.

    Recognising the sentinel must not become "rebind anything that looks dead".
    The account test is what keeps this pass off the operator's own rows, and it
    is the ONLY thing standing between a sentinel-bearing stream an operator
    created themselves and a silent rewrite of their lineup.
    """
    from dbas.placeholder_rebind import rebind_placeholders_after_refresh

    client = _client()
    client.get_m3u_accounts.return_value = _accounts(
        {"id": OPERATOR_ACCOUNT_ID, "name": "My Own Streams"}
    )
    client.get_streams.return_value = {
        "results": [
            _redacted_placeholder(700, account=OPERATOR_ACCOUNT_ID),
            _real(900, STREAM_NAME),
        ]
    }
    client.get_channels.return_value = {
        "results": [{"id": 201, "name": "Bayou", "streams": [700]}]
    }

    result = await rebind_placeholders_after_refresh(client=client, trigger="test")

    client.update_channel.assert_not_awaited()
    client.delete_stream.assert_not_awaited()
    assert result.rebound == 0


def test_the_synthetic_account_name_is_the_one_the_fallback_creates():
    """Guard against the fixtures drifting from the code they stand in for."""
    assert _accounts()[1]["name"] == CUSTOM_STREAM_ACCOUNT_NAME


def test_tier_4_also_prefers_a_candidate_that_can_serve():
    """The preference holds on EVERY rung, including the fuzzy one.

    The dead candidate is deliberately the BETTER fuzzy match — a strict superset
    of the archived name, which ``token_set_ratio`` scores 1.00 against the real
    stream's 0.97 — so a score-only winner is the placeholder. Tier 4 is the rung
    an archive restore reaches on a name that drifted, and a run that lands there
    should not be handed the one row that cannot stream.
    """
    archived = {
        "id": 7,
        "name": "News One Sports HD",
        "m3u_account": SOURCE_ACCOUNT_ID,
    }
    candidates = [
        _redacted_placeholder(5, name="News One Sports HD Extra"),
        {
            "id": 200,
            "name": "News One Sport HD",
            "url": REAL_URL,
            "m3u_account": PROVIDER_ACCOUNT_ID,
        },
    ]

    assert match_stream(archived, candidates, allow_fuzzy=True) == (
        MatchTier.FUZZY_NORMALIZED_NAME,
        200,
    )


@pytest.mark.asyncio
async def test_the_refresh_pass_will_not_rebind_a_slot_onto_a_redacted_stream():
    """The post-refresh pass has its own candidate set, and it needs the same rule.

    Stream 600 sits on the PROVIDER account — so the placeholder gate never looks
    at it — carries the archived name, and carries a redacted url. Under bare
    truthiness it is a perfectly good rebind target, so the pass moves the channel
    off one dead stream onto another and calls it a rebind. Nothing may move, and
    the channel must be named unplayable.
    """
    from dbas.placeholder_rebind import rebind_placeholders_after_refresh

    client = _client()
    _wire(
        client,
        streams=[
            {
                "id": 500,
                "name": STREAM_NAME,
                "url": None,
                "m3u_account": SYNTHETIC_ACCOUNT_ID,
            },
            _redacted_placeholder(600, account=PROVIDER_ACCOUNT_ID),
        ],
        channels=[{"id": 201, "name": "Bayou", "streams": [500]}],
    )

    result = await rebind_placeholders_after_refresh(client=client, trigger="test")

    client.update_channel.assert_not_awaited()
    assert result.rebound == 0
