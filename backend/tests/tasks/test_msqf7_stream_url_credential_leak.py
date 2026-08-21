"""No provider credential reaches the destination, in any URL position.

Bead ``enhancedchannelmanager-msqf7`` (epic ``f5a5j``). A real Xtream Codes
provider was sampled on 2026-08-20 to settle the question bead ``v7d37`` was
blocked on. Every one of the 1,409,363 stream URLs in its playlist put the
account's username and password in PATH SEGMENTS:

    https://<host>:443/live/<USER>/<PASS>/<id>.ts     53,277
    https://<host>:443/movie/<USER>/<PASS>/<id>      194,806
    https://<host>:443/series/<USER>/<PASS>/<id>   1,161,273

while the SAME provider authenticates its guide endpoint by query string
(``xmltv.php?username=…&password=…``). One provider, both carriers.

THE DEFECT. ``routers.backup._REDACT_KEYS`` is KEY-NAME based and a stream's key
is ``url``; the value-based scrubber ``_url_carries_credentials`` sees userinfo
and query strings only. So the path-segment shape crossed to the destination
untouched, on every scheduled cycle, while the run told the operator credentials
had been stripped.

THE FIX IS A LITERAL MATCH, NOT A PATTERN. No GENERAL rule separates
``/live/u/p/1.ts`` from an ordinary path — the old docstring was right about
that. But the source instance KNOWS its own provider credentials, so each path
segment is compared against those literal values (raw and percent-decoded), and
a URL is only rewritten once one of its segments CARRIES a known PASSWORD. That
gate is what keeps the fix from mangling a path that merely resembles
credentials; the containment rather than equality is what keeps a decorated
segment (``/<pass>-hd/``) from being a way through.

WHAT THE REPLICA GETS. The credential segments become the redaction sentinel and
everything else about the address survives, so the operator can see where the
stream pointed and the run says so in its one line — the reporting path bead
``v7d37`` just built, reused rather than rebuilt.

Four layers, because green at one proves nothing about the next:

1. **The rewriter** — which segments are replaced, and which URLs are left
   byte-identical.
2. **The plan** — no known credential value survives anywhere in the payload
   that goes on the wire.
3. **The destination** — what B actually STORES after a real apply, read off B's
   stream rows rather than off the report.
4. **The operator's line** — the summary built from that report names the
   shortfall.

All values here are SYNTHETIC. The real provider's credentials were used for
diagnosis only and appear in no file, fixture, bead or message.

Conventions: ``docs/pytest_conventions.md``.
"""
from __future__ import annotations

import json

import pytest

from dbas.restore_contracts import RestoreOutcome
from tasks.dbas_sync import DbasSyncTask
from tests.fixtures.sync_harness import StatefulDispatcharrFake, SyncHarness

# Synthetic stand-ins for the sampled provider's credential pair.
_XC_USER = "nw-demo-user"
_XC_PASS = "nw-demo-secret-9f2"
_XC_HOST = "http://p.test:9191"

_XC_ACCOUNT_NAME = "Northwind IPTV (Xtream Codes)"
_STD_ACCOUNT_NAME = "Northwind Local Affiliates (Standard M3U)"

# The three shapes the real provider emits, all credential-bearing.
_XC_LIVE_URL = f"{_XC_HOST}/live/{_XC_USER}/{_XC_PASS}/200.ts"
_XC_MOVIE_URL = f"{_XC_HOST}/movie/{_XC_USER}/{_XC_PASS}/300"
_XC_SERIES_URL = f"{_XC_HOST}/series/{_XC_USER}/{_XC_PASS}/400"

# Same instance, same provider host, NO credential anywhere: a path that merely
# RESEMBLES the credential shape, and a channel named after a number. Both must
# cross byte-identical, or the fix has broken playback on the replica to close a
# leak that was not there.
_DECOY_RESEMBLING_URL = f"{_XC_HOST}/live/sports/premium/301.ts"
_DECOY_NUMERIC_URL = f"{_XC_HOST}/movie/12345/999.mp4"
_STD_PLAIN_URL = "http://provider-northwind/stream/valley-public.ts"


def _source_with_xc_streams() -> StatefulDispatcharrFake:
    """Source-A holding one XC account whose streams carry path credentials.

    Also carries a credential-free Standard-M3U account and two decoy URLs on
    the SAME provider host, so every assertion below is a CONTRAST rather than a
    single-sided claim.
    """
    source = StatefulDispatcharrFake.seeded_source()
    xc = source.m3u_accounts.create(
        {
            "name": _XC_ACCOUNT_NAME,
            "account_type": "XC",
            "username": _XC_USER,
            "password": _XC_PASS,
            "server_url": _XC_HOST,
        }
    )
    std = source.m3u_accounts.create(
        {
            "name": _STD_ACCOUNT_NAME,
            "account_type": "STD",
            "username": None,
            "password": "",
            "server_url": "http://provider-northwind/local.m3u",
        }
    )
    for name, number, url, account in (
        ("Summit Sports 1", 200, _XC_LIVE_URL, xc),
        ("Silverline Cinema", 300, _XC_MOVIE_URL, xc),
        ("Orbit Sci-Fi", 400, _XC_SERIES_URL, xc),
        ("Pitchside FC", 301, _DECOY_RESEMBLING_URL, xc),
        ("Matinee Family", 302, _DECOY_NUMERIC_URL, xc),
        ("Valley Public", 800, _STD_PLAIN_URL, std),
    ):
        stream = source.streams.create(
            {"name": name, "url": url, "m3u_account": account["id"]}
        )
        source.channels.create(
            {"name": name, "channel_number": number, "streams": [stream["id"]]}
        )
    return source


# ---------------------------------------------------------------------------
# 1. THE REWRITER — which segments go, and which URLs are untouched.
# ---------------------------------------------------------------------------


def test_path_segments_that_literally_carry_a_known_credential_are_replaced():
    """The sampled shape, rewritten rather than stripped whole.

    The address survives — host, the ``live`` kind marker and the stream id are
    all still there — because a replica whose stream URLs were blanked is the
    ``v7d37`` failure again, not a fix.
    """
    from routers.backup import REDACTED, _scrub_credential_urls

    result = _scrub_credential_urls(
        _XC_LIVE_URL, secrets=frozenset({_XC_PASS}), identities=frozenset({_XC_USER})
    )

    assert result == f"{_XC_HOST}/live/{REDACTED}/{REDACTED}/200.ts"
    assert _XC_USER not in result
    assert _XC_PASS not in result


@pytest.mark.parametrize(
    "url",
    [
        # A path that merely RESEMBLES the credential shape. Nothing in it
        # equals a known credential, so guessing structurally is the only way to
        # flag it — and guessing is what costs the operator a working URL.
        _DECOY_RESEMBLING_URL,
        # A channel literally named after a number, in the credential position.
        _DECOY_NUMERIC_URL,
        # No credential-shaped path at all.
        _STD_PLAIN_URL,
    ],
)
def test_a_path_that_only_resembles_credentials_is_left_byte_identical(url):
    """``None`` means "left byte-identical", not "partially rewritten"."""
    from routers.backup import _scrub_credential_urls

    assert (
        _scrub_credential_urls(
            url, secrets=frozenset({_XC_PASS}), identities=frozenset({_XC_USER})
        )
        is None
    )


def test_a_username_segment_alone_never_triggers_a_rewrite():
    """The gate is the PASSWORD, and this is why.

    An operator whose XC username happens to be a structural path word
    (``live``, ``movie``, ``news``) would otherwise have every URL on the
    instance mangled — including credential-free ones from other providers. So a
    URL is only rewritten once one of its segments carries a known SECRET; the
    identity half is then redacted alongside it.
    """
    from routers.backup import REDACTED, _scrub_credential_urls

    identities = frozenset({"movie"})
    secrets = frozenset({_XC_PASS})

    # 'movie' is the account username AND the kind marker: untouched, because no
    # segment equals the password.
    assert _scrub_credential_urls(_DECOY_NUMERIC_URL, secrets, identities) is None
    # Once the password IS present, both halves go.
    gated = _scrub_credential_urls(
        f"{_XC_HOST}/movie/movie/{_XC_PASS}/300", secrets, identities
    )
    assert gated == f"{_XC_HOST}/{REDACTED}/{REDACTED}/{REDACTED}/300"


def test_a_decorated_credential_segment_is_still_caught():
    """CONTAINMENT, not equality — and then the whole segment goes.

    A provider that appends a quality suffix to the credential segment
    (``/<pass>-hd/``) is still handing over the password. Whole-segment equality
    would let that through while looking like a tight rule, which is the failure
    mode this bead exists to close.
    """
    from routers.backup import REDACTED, _scrub_credential_urls

    result = _scrub_credential_urls(
        f"{_XC_HOST}/live/{_XC_USER}/{_XC_PASS}-hd/200.ts",
        secrets=frozenset({_XC_PASS}),
        identities=frozenset({_XC_USER}),
    )

    assert result == f"{_XC_HOST}/live/{REDACTED}/{REDACTED}/200.ts"


def test_containment_still_needs_the_secret_present_to_open_the_gate():
    """The other direction: containment must not become "any resemblance".

    A path that contains the USERNAME as a substring and no secret at all is a
    credential-free address and must cross byte-identical.
    """
    from routers.backup import _scrub_credential_urls

    assert (
        _scrub_credential_urls(
            f"{_XC_HOST}/live/{_XC_USER}-archive/200.ts",
            secrets=frozenset({_XC_PASS}),
            identities=frozenset({_XC_USER}),
        )
        is None
    )


def test_a_percent_encoded_credential_segment_is_still_caught():
    """A credential with URL-reserved characters is encoded in the path.

    Matching only the raw segment would let ``p@ss/word`` through as
    ``p%40ss%2Fword``, which is the same secret wearing an escape.
    """
    from routers.backup import REDACTED, _scrub_credential_urls

    secret = "p@ss word"
    result = _scrub_credential_urls(
        f"{_XC_HOST}/live/{_XC_USER}/p%40ss%20word/200.ts",
        secrets=frozenset({secret}),
        identities=frozenset({_XC_USER}),
    )

    assert result == f"{_XC_HOST}/live/{REDACTED}/{REDACTED}/200.ts"


def test_a_url_carrying_the_credential_in_both_query_and_path_loses_the_whole_value():
    """The shape bead ``v7d37`` feared, asserted rather than assumed.

    A URL whose QUERY carries credentials is still replaced WHOLE — the existing
    rule — so the path half cannot survive on its coat-tails. This is the
    assertion that says the fix did not open the hole it was written to close.
    """
    from routers.backup import REDACTED, _scrub_credential_urls

    both = f"{_XC_HOST}/live/{_XC_USER}/{_XC_PASS}/200.ts?username={_XC_USER}"
    result = _scrub_credential_urls(
        both, secrets=frozenset({_XC_PASS}), identities=frozenset({_XC_USER})
    )

    assert result == REDACTED


def test_a_secret_used_as_an_unrecognised_query_parameter_is_caught_by_value():
    """``?u=…&p=…`` names no credential-shaped key, so the key rule is blind."""
    from routers.backup import REDACTED, _scrub_credential_urls

    result = _scrub_credential_urls(
        f"{_XC_HOST}/get.php?u={_XC_USER}&p={_XC_PASS}",
        secrets=frozenset({_XC_PASS}),
        identities=frozenset({_XC_USER}),
    )

    assert result == REDACTED


def test_with_no_known_credentials_every_url_is_left_alone():
    """The default is a no-op, so no existing caller changes behaviour."""
    from routers.backup import _scrub_credential_urls

    assert _scrub_credential_urls(_XC_LIVE_URL) is None


# ---------------------------------------------------------------------------
# 2. THE PLAN — nothing on the wire carries a credential.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_known_credential_survives_anywhere_in_the_outgoing_plan():
    """The bead's acceptance criterion, stated as the property.

    Not "the stream url is clean" — no credential value in ANY field, in ANY
    position, anywhere in the serialized plan. The stream URL is one example.
    """
    from routers import backup as backup_mod
    from tasks.dbas_sync_engine import build_live_source_plan
    from unittest.mock import patch

    source = _source_with_xc_streams()
    with patch.object(backup_mod, "get_client", return_value=source):
        plan = await build_live_source_plan()

    wire = json.dumps(
        [
            {"entity_type": c.entity_type.value, "entities": c.entities}
            for c in plan.categories
        ],
        default=str,
    )

    for secret in (_XC_PASS, _XC_USER, "SEED-M3U-SECRET", "SEED-EPG-SECRET"):
        assert secret not in wire, f"{secret!r} reached the wire"
    # And the addresses that carry no credential are still there, or this is a
    # blanking pass rather than a redaction.
    assert _DECOY_RESEMBLING_URL in wire
    assert _DECOY_NUMERIC_URL in wire
    assert _STD_PLAIN_URL in wire


# ---------------------------------------------------------------------------
# 3. THE DESTINATION — what B stores after a real apply.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b_stores_no_provider_credential_in_any_stream_url(tmp_path):
    """Read off B's stream rows, because the report is the thing that lied."""
    source = _source_with_xc_streams()
    dest = StatefulDispatcharrFake.empty_dest()

    report = await SyncHarness(source=source, dest=dest).run(
        confirm_apply=True, ledger_dir=tmp_path
    )

    stored = json.dumps(dest.streams.list(), default=str)
    assert _XC_PASS not in stored
    assert _XC_USER not in stored
    # The streams still landed — the fix must not cost the replica its rows.
    assert len(dest.streams.list()) == len(source.streams.list())
    # AMENDED BY BEAD ``…-1td94``. This line asserted ``SUCCESS``, and that was
    # the defect written down as an expectation: three of B's channels are left
    # on redacted placeholders that fetch HTTP 404, and calling that outcome a
    # success is precisely the silence bead ``…-posm1`` exists to end. Measured
    # live on 0.29.0 at the same time: 53 of B's 59 channels served 404 while the
    # run reported ``channels_with_no_playable_stream: 0``.
    #
    # WHAT DID NOT CHANGE: the redaction, which is msqf7's and stays exactly as
    # shipped. The credential assertions above are this test's subject and are
    # untouched. What changed is that the run now SAYS what the redaction cost.
    assert report.outcome == RestoreOutcome.COMPLETED_WITH_FAILURES
    assert report.channels_with_no_playable_stream == 3


@pytest.mark.asyncio
async def test_b_keeps_the_address_of_a_credential_bearing_stream(tmp_path):
    """The credential goes; the address stays, so the operator can see it."""
    from routers.backup import REDACTED

    source = _source_with_xc_streams()
    dest = StatefulDispatcharrFake.empty_dest()

    await SyncHarness(source=source, dest=dest).run(
        confirm_apply=True, ledger_dir=tmp_path
    )

    by_name = {row["name"]: row.get("url") or "" for row in dest.streams.list()}
    assert by_name["Summit Sports 1"] == f"{_XC_HOST}/live/{REDACTED}/{REDACTED}/200.ts"
    # The credential-free rows on the same instance crossed byte-identical.
    assert by_name["Pitchside FC"] == _DECOY_RESEMBLING_URL
    assert by_name["Matinee Family"] == _DECOY_NUMERIC_URL
    assert by_name["Valley Public"] == _STD_PLAIN_URL


# ---------------------------------------------------------------------------
# 4. THE OPERATOR'S LINE — the run says what it did.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_summary_names_the_streams_that_lost_their_credentials(tmp_path):
    """Silence is the other half of this defect: the run claimed credentials
    were stripped while shipping them, and must not now strip them silently.
    """
    source = _source_with_xc_streams()
    dest = StatefulDispatcharrFake.empty_dest()

    report = await SyncHarness(source=source, dest=dest).run(
        confirm_apply=True, ledger_dir=tmp_path
    )
    message = DbasSyncTask._summary_message(report, False, report.outcome.value)

    assert report.stream_urls_redacted == 3
    assert "3 stream(s) restored without a playable URL" in message
    # The decoys are NOT counted — an over-count is a false alarm the operator
    # cannot act on.
    assert {d.label for d in report.stream_url_redaction_details} == {
        "Summit Sports 1",
        "Silverline Cinema",
        "Orbit Sci-Fi",
    }


@pytest.mark.asyncio
async def test_a_preview_claims_nothing_because_it_created_nothing(tmp_path):
    """The counter is written where the create SUCCEEDS, so a preview is silent.

    Asserted rather than assumed: bead ``…-dgnms`` measured what a confidently
    reported preview ``0`` costs when the pass that produces the number cannot
    run before the apply.
    """
    source = _source_with_xc_streams()
    dest = StatefulDispatcharrFake.empty_dest()

    report = await SyncHarness(source=source, dest=dest).run(
        confirm_apply=False, ledger_dir=tmp_path
    )
    # A preview carries no outcome — it decided nothing (…-dgnms).
    message = DbasSyncTask._summary_message(report, False, "unknown")

    assert dest.streams.list() == []
    assert report.stream_urls_redacted == 0
    assert "without a playable URL" not in message


@pytest.mark.asyncio
async def test_a_sync_with_no_credential_bearing_stream_says_nothing(tmp_path):
    """The clause must mean something when it fires."""
    source = StatefulDispatcharrFake.seeded_source(with_embedded_streams=True)
    dest = StatefulDispatcharrFake.empty_dest()

    report = await SyncHarness(source=source, dest=dest).run(
        confirm_apply=True, ledger_dir=tmp_path
    )
    message = DbasSyncTask._summary_message(report, False, report.outcome.value)

    assert report.stream_urls_redacted == 0
    assert "without a playable URL" not in message
