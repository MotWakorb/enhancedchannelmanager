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

AMENDED 2026-08-22 — THE PO RULED THAT PROVIDER CREDENTIALS CROSS ON EVERY CYCLE
--------------------------------------------------------------------------------
ADR-013 amendment (b). The stream URL now crosses WHOLE, credential segments
included, so the replica's channels are bound to addresses that play on the same
cycle that writes them. Layers 2-4 below therefore assert the OPPOSITE of what
they asserted when this file was written, and that inversion is deliberate
rather than a regression: the suite is kept, not deleted, because the property
it now pins is the one this bead actually cares about.

**THE BEAD'S REAL SUBJECT SURVIVES UNCHANGED, and it is not "credentials must
not cross".** ``msqf7`` was a defect about ECM TELLING THE OPERATOR credentials
were stripped WHILE TRANSMITTING THEM ANYWAY — implicitly, in a field nothing
inspected, with the run report, the user guide and the audit row's
``redaction_mode`` all asserting the opposite. Deliberate transmission with the
product's words matching is the opposite of that defect. So layer 4 below now
checks that the audit row and the run SAY what crossed, and
``test_ecm_own_secrets_still_never_reach_the_wire`` keeps the half of the
original property that did not change.

The redaction machinery in layer 1 is UNCHANGED and still shipped: it is what
the standard backup ARTIFACT uses, and an artifact is a file that gets attached
to support tickets. Only the SYNC path opted out of it.

Four layers, because green at one proves nothing about the next:

1. **The rewriter** — which segments are replaced, and which URLs are left
   byte-identical. Unchanged; still the artifact path's behaviour.
2. **The plan** — the provider credential IS on the wire, and ECM's own secrets
   are NOT.
3. **The destination** — what B actually STORES after a real apply, read off B's
   stream rows rather than off the report.
4. **The operator's line** — the run and its audit row NAME what crossed.

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
# 2. THE PLAN — the provider credential IS on the wire; ECM's own secrets are not.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_provider_credential_reaches_the_wire_in_every_carrier():
    """The 2026-08-22 property, stated as the inverse of the original one.

    Every position the sampled provider uses has to carry: the account's own
    ``username``/``password`` FIELDS, and the PATH SEGMENTS of all three stream
    shapes. Asserting only the fields would pass while the replica still could
    not play anything, which is the whole point of the change.
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

    assert _XC_PASS in wire, "the provider password did not reach the wire"
    assert _XC_USER in wire, "the provider username did not reach the wire"
    for url in (_XC_LIVE_URL, _XC_MOVIE_URL, _XC_SERIES_URL):
        assert url in wire, f"{url!r} did not cross whole"
    # And the addresses that never carried a credential still cross untouched.
    assert _DECOY_RESEMBLING_URL in wire
    assert _DECOY_NUMERIC_URL in wire
    assert _STD_PLAIN_URL in wire
    # Nothing anywhere is still wearing the sentinel.
    assert backup_mod.REDACTED not in wire


@pytest.mark.asyncio
async def test_ecm_own_secrets_still_never_reach_the_wire():
    """The half of the original property that did NOT change, pinned.

    The ruling widened the sync payload by exactly two sections. An ECM settings
    secret and an alert-method secret are not provider credentials, have no
    purpose on a replica, and must still be sentinelled — otherwise the
    exception has quietly become "redaction is off", which is the failure mode a
    ``preserve_keys`` change makes easy and invisible.
    """
    from routers import backup as backup_mod
    from tasks.dbas_sync_engine import _redact_sync_sections

    sections = {
        "m3u_accounts": [
            {"name": _XC_ACCOUNT_NAME, "username": _XC_USER, "password": _XC_PASS}
        ],
        "settings": [{"key": "smtp_password", "password": "ECM-OWN-SETTINGS-SECRET"}],
        "alert_methods": [{"name": "Ops", "bot_token": "ECM-OWN-BOT-TOKEN"}],
    }
    redacted = _redact_sync_sections(sections)
    blob = json.dumps(redacted, default=str)

    assert "ECM-OWN-SETTINGS-SECRET" not in blob
    assert "ECM-OWN-BOT-TOKEN" not in blob
    # ...while the provider half crossed, so this is a CONTRAST and not a test
    # that would pass with redaction on everywhere.
    assert _XC_PASS in blob
    assert _XC_USER in blob


# ---------------------------------------------------------------------------
# 3. THE DESTINATION — what B stores after a real apply.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b_stores_the_provider_credential_in_every_stream_url(tmp_path):
    """Read off B's stream rows, because the report is the thing that lied.

    This is bead ``…-2jvvb`` / ``…-5bib5`` closed at the layer that produced
    them: B's channels are bound to addresses that PLAY on this cycle, so there
    are no placeholders to rebind and no orphan window for B's own refresh to
    open. The outcome is back to ``SUCCESS`` and
    ``channels_with_no_playable_stream`` is 0 — not because the counter was
    silenced (bead ``…-1td94`` made it honest and it stays honest) but because
    there is nothing left for it to count.
    """
    source = _source_with_xc_streams()
    dest = StatefulDispatcharrFake.empty_dest()

    report = await SyncHarness(source=source, dest=dest).run(
        confirm_apply=True, ledger_dir=tmp_path
    )

    stored = json.dumps(dest.streams.list(), default=str)
    assert _XC_PASS in stored
    assert _XC_USER in stored
    assert len(dest.streams.list()) == len(source.streams.list())
    assert report.outcome == RestoreOutcome.SUCCESS
    assert report.channels_with_no_playable_stream == 0


@pytest.mark.asyncio
async def test_b_keeps_the_whole_address_of_a_credential_bearing_stream(tmp_path):
    """Byte-identical, every carrier, no sentinel anywhere."""
    source = _source_with_xc_streams()
    dest = StatefulDispatcharrFake.empty_dest()

    await SyncHarness(source=source, dest=dest).run(
        confirm_apply=True, ledger_dir=tmp_path
    )

    by_name = {row["name"]: row.get("url") or "" for row in dest.streams.list()}
    assert by_name["Summit Sports 1"] == _XC_LIVE_URL
    assert by_name["Silverline Cinema"] == _XC_MOVIE_URL
    assert by_name["Orbit Sci-Fi"] == _XC_SERIES_URL
    # The credential-free rows on the same instance are unaffected either way.
    assert by_name["Pitchside FC"] == _DECOY_RESEMBLING_URL
    assert by_name["Matinee Family"] == _DECOY_NUMERIC_URL
    assert by_name["Valley Public"] == _STD_PLAIN_URL


@pytest.mark.asyncio
async def test_b_receives_the_accounts_own_credential_fields(tmp_path):
    """The account authenticates in its own right, not only via stream URLs.

    A replica whose stream rows play but whose M3U account cannot authenticate
    stops serving the moment it refreshes. Both halves have to cross.
    """
    source = _source_with_xc_streams()
    dest = StatefulDispatcharrFake.empty_dest()

    await SyncHarness(source=source, dest=dest).run(
        confirm_apply=True, ledger_dir=tmp_path
    )

    accounts = {row["name"]: row for row in dest.m3u_accounts.list()}
    xc = accounts[_XC_ACCOUNT_NAME]
    assert xc.get("username") == _XC_USER
    assert xc.get("password") == _XC_PASS


# ---------------------------------------------------------------------------
# 4. THE OPERATOR'S LINE — the run SAYS what crossed.
#
# This is the bead's surviving subject. The defect was never the transmission;
# it was the product asserting the transmission had not happened.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_audit_row_states_that_credentials_crossed(tmp_path):
    """``redaction_mode`` said ``topology_only`` while msqf7 was live.

    That string is the audit trail asserting the exact thing that was false, so
    it is pinned here rather than left to a docstring: the row must name the
    mode, the count, and the records by label and FIELD NAME — and no value.
    """
    from unittest.mock import patch

    import journal

    source = _source_with_xc_streams()
    dest = StatefulDispatcharrFake.empty_dest()

    rows: list[dict] = []
    with patch.object(journal, "log_entry", side_effect=lambda **kw: rows.append(kw)):
        await SyncHarness(source=source, dest=dest).run(
            confirm_apply=True, ledger_dir=tmp_path
        )

    sync_rows = [r for r in rows if r.get("action_type") == "sync_run"]
    assert len(sync_rows) == 1, "a cycle must write exactly one sync_run row"
    after = sync_rows[0]["after_value"]
    assert after["redaction_mode"] == "topology_plus_provider_credentials"
    assert after["provider_credentials_transmitted"] >= 1
    named = " ".join(after["provider_credential_records"])
    assert _XC_ACCOUNT_NAME in named
    assert "username" in named and "password" in named
    # FIELD NAMES ONLY. The row is the record that a secret moved; it must never
    # be a place the secret can be read.
    blob = json.dumps(sync_rows[0], default=str)
    assert _XC_PASS not in blob
    assert _XC_USER not in blob


@pytest.mark.asyncio
async def test_the_summary_no_longer_reports_stripped_stream_urls(tmp_path):
    """The shortfall line must go quiet, because the shortfall is gone.

    A counter that keeps firing after the condition it describes has been
    removed is bead ``…-kcfru``'s crying wolf, and it would tell the operator to
    perform a recovery that no longer exists.
    """
    source = _source_with_xc_streams()
    dest = StatefulDispatcharrFake.empty_dest()

    report = await SyncHarness(source=source, dest=dest).run(
        confirm_apply=True, ledger_dir=tmp_path
    )
    message = DbasSyncTask._summary_message(report, False, report.outcome.value)

    assert report.stream_urls_redacted == 0
    assert report.stream_url_redaction_details == []
    assert "without a playable URL" not in message


@pytest.mark.asyncio
async def test_a_preview_says_not_measured_rather_than_zero(tmp_path):
    """The counter describes the DESTINATION, and a preview reads no streams.

    AMENDED BY BEAD ``…-ukjx5``, which moved this counter off the create path.
    It used to assert ``0`` here and the reasoning was "the recorder fires on a
    successful create, which a preview does not perform" — true of the old
    mechanism, and exactly the confident-zero bead ``…-dgnms`` measured the cost
    of. Now that the number means "streams the destination is holding redacted",
    ``0`` from a preview would be a claim about a destination nothing looked at,
    and on a second cycle a FALSE one. ``None`` is the honest answer, and the
    operator's line still says nothing either way.
    """
    source = _source_with_xc_streams()
    dest = StatefulDispatcharrFake.empty_dest()

    report = await SyncHarness(source=source, dest=dest).run(
        confirm_apply=False, ledger_dir=tmp_path
    )
    # A preview carries no outcome — it decided nothing (…-dgnms).
    message = DbasSyncTask._summary_message(report, False, "unknown")

    assert dest.streams.list() == []
    assert report.stream_urls_redacted is None
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
