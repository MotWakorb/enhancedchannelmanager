"""The debug bundle carries no provider credential VALUE (bead …-d0hoc).

THE INVARIANT UNDER TEST, stated as a property rather than as the shapes that
exposed it:

    No provider credential value appears anywhere in a debug bundle, at any
    path shape, in any member file, at any nesting depth.

NOT "``/live/`` URLs are scrubbed", and NOT "``_XC_PATH_RE`` matches more
shapes". The defect that motivated this file was measured live on 2026-08-23:
``POST /api/auto-creation/debug-bundle`` produced a tar.gz whose ``channels.csv``
and ``channels.json`` each held 316 copies of the operator's real Xtream Codes
username AND password, as ``https://example.com/live/<user>/<pass>/13580.ts``.
``obfuscate.py``'s ``_XC_PATH_RE`` anchored on a THREE-segment path
(``/user/pass/id.ext``); Dispatcharr renders four (``/live/user/pass/id.ts``),
plus ``/movie/``, ``/series/`` and ``/server1/live/...``. The pattern never
matched, so the credential branch never ran — while the hostname WAS replaced
with ``example.com``, which is why the output looked obfuscated.

Every test here drives the REAL bundle builder (:func:`_build_debug_bundle`) and
byte-scans every member of the produced archive. Asserting against
``obfuscate_url`` directly would prove only that one function's behaviour; the
claim is about the artifact an operator attaches to a support ticket.

The credential values are SYNTHETIC. No real credential belongs in this repo.
"""
from __future__ import annotations

import io
import json
import tarfile
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker


# --------------------------------------------------------------------------
# Synthetic credentials. Deliberately long and distinctive so a byte scan
# cannot collide with incidental bundle content, and so a partial match is
# visible as a partial match rather than as a near-miss.
# --------------------------------------------------------------------------
XC_USER = "d0hocSyntheticUser42"
XC_PASS = "d0hocSyntheticPass99"

# A SECOND provider whose credential is NOT in any account record the bundle
# builder can see (a stale stream row, a hand-entered URL). The value rule is
# blind to it by construction; only the shape rule can reach it.
ORPHAN_USER = "d0hocOrphanUser77"
ORPHAN_PASS = "d0hocOrphanPass88"


# --------------------------------------------------------------------------
# Recorded XtreamCodes URL shapes.
#
# The first five are the shapes recorded in bead …-d0hoc against a real
# Dispatcharr/XC provider (the ``/server1/`` prefix is confirmed by
# Dispatcharr's own ``apps/m3u/tests/test_xc_live_url.py``). ``bare_3seg`` is
# the ONLY shape the shipped regex matched and is kept as a regression guard.
# The remaining shapes are ours: they are not in the bead, and they exist
# because the acceptance criterion is the invariant, not the bead's list.
# --------------------------------------------------------------------------
RECORDED_XC_SHAPES: dict[str, str] = {
    # --- recorded in the bead ---
    "live": f"http://provider.example.net:8080/live/{XC_USER}/{XC_PASS}/13580.ts",
    "bare_3seg": f"http://provider.example.net:8080/{XC_USER}/{XC_PASS}/13580.ts",
    "movie": f"http://provider.example.net:8080/movie/{XC_USER}/{XC_PASS}/99.mkv",
    "series": f"http://provider.example.net:8080/series/{XC_USER}/{XC_PASS}/4242.mp4",
    "server_prefix": f"http://provider.example.net:8080/server1/live/{XC_USER}/{XC_PASS}/1.ts",
    # --- ours: shapes none of the above anticipate ---
    # A query-string credential. No path segment carries it at all, so every
    # path-shaped rule — the shipped one and any widening of it — is blind.
    "query_string": (
        f"http://provider.example.net:8080/get.php?username={XC_USER}"
        f"&password={XC_PASS}&type=m3u_plus&output=ts"
    ),
    # HLS: the credential sits three segments from the end and the final
    # segment is not ``<digits>.<ext>``, so a suffix-anchored shape rule
    # cannot reach it. Only the value rule can.
    "hls_playlist": f"http://provider.example.net:8080/hls/{XC_USER}/{XC_PASS}/1234/index.m3u8",
    # Percent-encoded credential. ``p%40ss`` is the same secret as ``p@ss``;
    # an escape must not be a way through.
    "percent_encoded": (
        f"http://provider.example.net:8080/live/{XC_USER}/{XC_PASS}%2Fx/7.ts"
    ),
    # Userinfo. Killed by the pre-existing netloc replacement, asserted here so
    # a future netloc change cannot silently reopen it.
    "userinfo": f"http://{XC_USER}:{XC_PASS}@provider.example.net:8080/playlist.m3u8",
    # A deeply nested prefix — proof the rule is not counting segments.
    "deep_prefix": f"http://provider.example.net:8080/a/b/c/live/{XC_USER}/{XC_PASS}/5.ts",
}

# The one shape whose credential is NOT recoverable from any account record.
ORPHAN_SHAPE = f"http://stale.example.net/live/{ORPHAN_USER}/{ORPHAN_PASS}/321.ts"


# --------------------------------------------------------------------------
# Bundle inspection. The scanner is the instrument every assertion here rests
# on, so it is smoke-tested against a known-good and a known-bad needle in
# ``TestTheScannerCanFail`` before any invariant is claimed from it.
# --------------------------------------------------------------------------
def extract_members(payload: bytes) -> dict[str, bytes]:
    """Every member of the tar.gz, as raw bytes, keyed by member name."""
    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tf:
        for info in tf.getmembers():
            handle = tf.extractfile(info)
            out[info.name] = handle.read() if handle is not None else b""
    return out


def scan_members(members: dict[str, bytes], needle: str) -> dict[str, int]:
    """Members whose RAW BYTES contain ``needle``, with occurrence counts.

    Byte-level on purpose. A member is JSON, YAML, CSV or plain text; decoding
    each one to search it would make the scan depend on the encoder agreeing
    with the scanner about escaping, and a JSON-escaped credential is still a
    credential. Returns only members with a non-zero count, so an empty dict is
    "found nowhere" — distinguishable from a scan that never ran, because the
    caller is handed the member dict it scanned.
    """
    encoded = needle.encode("utf-8")
    hits = {name: data.count(encoded) for name, data in members.items()}
    return {name: n for name, n in hits.items() if n}


# --------------------------------------------------------------------------
# Fixture data + fake upstream
# --------------------------------------------------------------------------
def build_streams(only_shapes=None) -> list[dict]:
    """One stream per recorded shape, plus the orphan-credential stream."""
    streams = []
    wanted = sorted(RECORDED_XC_SHAPES.items())
    if only_shapes is not None:
        wanted = [(k, v) for k, v in wanted if k in set(only_shapes)]
    for index, (label, url) in enumerate(wanted, start=1):
        streams.append({
            "id": index,
            "name": f"Recorded Shape {label}",
            "url": url,
            "m3u_account": {"id": 1, "name": "Synthetic XC Provider"},
        })
    streams.append({
        "id": 900,
        "name": "Orphan Credential Stream",
        "url": ORPHAN_SHAPE,
        "m3u_account": None,
    })
    return streams


class FakeDispatcharrClient:
    """The upstream surface :func:`_build_debug_bundle` actually touches."""

    def __init__(self, channels, streams, groups, m3u_accounts, epg_sources=None):
        self._channels = channels
        self._streams = {s["id"]: s for s in streams}
        self._groups = groups
        self._m3u_accounts = m3u_accounts
        self._epg_sources = epg_sources or []

    async def get_channels(self, page=1, page_size=100):
        return {"count": len(self._channels), "results": self._channels, "next": None}

    async def get_channel_groups(self):
        return self._groups

    async def get_streams_by_ids(self, ids):
        return [self._streams[i] for i in ids if i in self._streams]

    async def get_m3u_accounts(self):
        return self._m3u_accounts

    async def get_epg_sources(self):
        return self._epg_sources


async def build_bundle(test_engine, extra_log_lines=None, extra_streams=None,
                       extra_channel_fields=None, only_shapes=None,
                       m3u_accounts=None, disable_value_rule=False):
    """Drive the REAL :func:`_build_debug_bundle` and return its bytes.

    ``only_shapes`` narrows the stream set to the named recorded shapes, so a
    per-shape failure names the shape that leaked instead of reporting the
    union. ``disable_value_rule`` empties EVERY source the harvester reads —
    account records AND settings — so the shape fallback can be exercised
    alone; emptying only the accounts would leave the settings credentials
    behind and quietly keep the value rule armed.
    """
    streams = build_streams(only_shapes=only_shapes)
    if extra_streams:
        streams.extend(extra_streams)
    stream_ids = [s["id"] for s in streams]
    channel = {
        "id": 1,
        "name": "Synthetic Channel",
        "channel_number": 1.0,
        "channel_group_id": 10,
        "tvg_id": "synthetic.tv",
        "tvc_guide_stationid": "",
        "streams": stream_ids,
    }
    channel.update(extra_channel_fields or {})
    channels = [channel]
    groups = [{"id": 10, "name": "Synthetic Group"}]
    if disable_value_rule:
        m3u_accounts = []
    if m3u_accounts is None:
        m3u_accounts = [{
            "id": 1,
            "name": "Synthetic XC Provider",
            "account_type": "XC",
            "server_url": "http://provider.example.net:8080",
            "username": XC_USER,
            "password": XC_PASS,
        }]

    client = FakeDispatcharrClient(channels, streams, groups, m3u_accounts)
    TestSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False
    )

    settings_payload = {"url": "http://dispatcharr.example.net:9191"}
    if not disable_value_rule:
        settings_payload["username"] = "ecm-operator"
        settings_payload["password"] = "ecm-operator-password"
    settings_obj = SimpleNamespace(model_dump=lambda: dict(settings_payload))

    log_lines = [
        "[M3U] refresh starting for Synthetic XC Provider",
        *(extra_log_lines or []),
    ]

    with patch("routers.channel_pipeline.get_client", return_value=client), \
         patch("routers.channel_pipeline.get_session", TestSessionLocal), \
         patch("config.get_settings", return_value=settings_obj), \
         patch("log_utils.get_recent_logs", return_value=log_lines):
        from routers.channel_pipeline import _build_debug_bundle

        _filename, payload = await _build_debug_bundle()
    return payload


class TestTheScannerCanFail:
    """Smoke-test the instrument before trusting its silence.

    A byte scan that quietly returns nothing — a mis-encoded needle, a members
    dict that never got populated, an archive that failed to open — is
    indistinguishable from a clean bundle. These two tests are the known-bad
    and known-good poles that make the scanner's silence mean something.
    """

    @pytest.mark.asyncio
    async def test_scanner_finds_a_planted_known_bad_needle(self, test_engine):
        planted = "d0hocPlantedKnownBadNeedle"
        payload = await build_bundle(
            test_engine, extra_channel_fields={"name": f"Channel {planted}"}
        )
        members = extract_members(payload)
        assert members, "archive opened but yielded no members — scan never ran"
        hits = scan_members(members, planted)
        assert hits, (
            "scanner reported clean on a bundle that provably contains the "
            f"planted needle; members scanned: {sorted(members)}"
        )

    @pytest.mark.asyncio
    async def test_scanner_reports_clean_for_a_known_good_absent_needle(self, test_engine):
        payload = await build_bundle(test_engine)
        members = extract_members(payload)
        assert members
        assert scan_members(members, "d0hocNeedleThatIsNowhereInTheBundle") == {}


class TestNoCredentialValueSurvivesTheBundle:
    """The invariant itself, over every member of a real archive."""

    @pytest.mark.asyncio
    async def test_the_provider_password_appears_in_no_member(self, test_engine):
        payload = await build_bundle(test_engine)
        members = extract_members(payload)
        assert members
        hits = scan_members(members, XC_PASS)
        assert hits == {}, f"provider password leaked into {hits}"

    @pytest.mark.asyncio
    async def test_the_provider_username_appears_in_no_member(self, test_engine):
        payload = await build_bundle(test_engine)
        members = extract_members(payload)
        assert members
        hits = scan_members(members, XC_USER)
        assert hits == {}, f"provider username leaked into {hits}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("shape_label", sorted(RECORDED_XC_SHAPES))
    async def test_each_recorded_shape_is_scrubbed(self, test_engine, shape_label):
        """Per-shape attribution: the bundle carries THIS shape and nothing
        else, so a failure names the shape that leaked rather than the union."""
        url = RECORDED_XC_SHAPES[shape_label]
        assert XC_PASS in url and XC_USER in url  # the fixture carries both
        payload = await build_bundle(test_engine, only_shapes=[shape_label])
        members = extract_members(payload)
        assert members
        assert scan_members(members, XC_PASS) == {}, f"{shape_label} leaked the password"
        assert scan_members(members, XC_USER) == {}, f"{shape_label} leaked the username"

    @pytest.mark.asyncio
    async def test_a_credential_quoted_in_a_log_line_is_scrubbed(self, test_engine):
        """``logs.txt`` shares the blind spot: ``obfuscate_text`` applies the
        same per-URL logic, so a provider URL echoed into a log line leaked
        exactly as ``channels.csv`` did."""
        payload = await build_bundle(test_engine, extra_log_lines=[
            "[M3U] fetch failed: http://provider.example.net:8080/get.php"
            f"?username={XC_USER}&password={XC_PASS}&type=m3u_plus (502)",
            f"[M3U] retrying http://provider.example.net:8080/live/{XC_USER}/{XC_PASS}/1.ts",
        ])
        members = extract_members(payload)
        assert "logs.txt" in members
        assert scan_members(members, XC_PASS) == {}
        assert scan_members(members, XC_USER) == {}

    @pytest.mark.asyncio
    async def test_a_credential_in_a_bare_log_line_is_scrubbed(self, test_engine):
        """Not every leak is URL-shaped. A log line that names the credential
        outside any URL is still the credential, and ``_URL_RE`` cannot see it."""
        payload = await build_bundle(test_engine, extra_log_lines=[
            f"[M3U] auth rejected for user={XC_USER} pass={XC_PASS}",
        ])
        members = extract_members(payload)
        assert "logs.txt" in members
        assert scan_members(members, XC_PASS) == {}
        assert scan_members(members, XC_USER) == {}


class TestMembersNoProducerScrubs:
    """The per-member sweep, isolated.

    ``channels.json``, ``channels.csv`` and ``logs.txt`` are scrubbed by their
    own producers. ``rules.yaml`` is not — it serializes operator-authored rule
    bodies straight out of the database, and an operator who wrote a rule
    against their own provider URL put the credential there by hand. Nothing in
    the URL scrubber ever looks at it.

    This is the case that makes the final ``_add_tar_entry`` sweep load-bearing
    rather than belt-and-braces, and it is the shape of the NEXT leak: a member
    added later by an author who assumed the bundle was already safe.
    """

    @pytest.mark.asyncio
    async def test_a_credential_in_an_operator_authored_rule_body_is_scrubbed(
        self, test_engine, test_session
    ):
        from models import ChannelPipelineRule

        test_session.add(ChannelPipelineRule(
            name="Match my provider",
            description=(
                "matches http://provider.example.net:8080/live/"
                f"{XC_USER}/{XC_PASS}/1.ts"
            ),
            enabled=True,
            priority=0,
            conditions=json.dumps(
                [{"type": "stream_name_contains", "value": XC_PASS}]
            ),
            actions=json.dumps([{"type": "skip"}]),
            run_on_refresh=False,
            stop_on_first_match=True,
            sort_order="asc",
            orphan_action="delete",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        ))
        test_session.commit()

        payload = await build_bundle(test_engine)
        members = extract_members(payload)
        assert b"Match my provider" in members["rules.yaml"], (
            "the rule never reached rules.yaml — this test proves nothing"
        )
        assert scan_members(members, XC_PASS) == {}
        assert scan_members(members, XC_USER) == {}


class TestShapeFallbackForUnknownCredentials:
    """The value rule is blind to a credential no account record holds.

    A stale stream row or a hand-entered URL carries a credential this instance
    cannot enumerate. The shape rule is the second layer that reaches it — and
    it must reach every XC prefix, not the one the bead happened to record.
    """

    @pytest.mark.asyncio
    async def test_an_unknown_credential_in_an_xc_path_is_still_scrubbed(self, test_engine):
        payload = await build_bundle(test_engine)
        members = extract_members(payload)
        assert members
        assert scan_members(members, ORPHAN_PASS) == {}
        assert scan_members(members, ORPHAN_USER) == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "shape_label", ["live", "bare_3seg", "movie", "series", "server_prefix", "deep_prefix"]
    )
    async def test_the_shape_rule_alone_reaches_every_xc_prefix(
        self, test_engine, shape_label
    ):
        """With the value rule DISABLED — no account records at all — the shape
        fallback must still reach every XtreamCodes prefix.

        This is the test that would have passed on a widened ``_XC_PATH_RE``
        for ``live`` and failed for ``movie``/``series``/``server_prefix``. The
        acceptance criterion is the tail shape at ANY depth, not a prefix list.
        """
        payload = await build_bundle(
            test_engine, only_shapes=[shape_label], disable_value_rule=True
        )
        members = extract_members(payload)
        assert members
        manifest = json.loads(members["manifest.json"].decode("utf-8"))
        assert manifest["credential_scrub"]["value_rule_active"] is False, (
            "the value rule was still active — this test proves nothing about "
            "the shape fallback"
        )
        assert scan_members(members, XC_PASS) == {}, f"{shape_label} leaked the password"
        assert scan_members(members, XC_USER) == {}, f"{shape_label} leaked the username"

    @pytest.mark.asyncio
    async def test_the_manifest_admits_when_the_value_rule_could_not_run(self, test_engine):
        """A degraded scrub is reported, not inferred. The whole reason this
        bead exists is that a bundle looked obfuscated while it was not."""
        payload = await build_bundle(test_engine, disable_value_rule=True)
        manifest = json.loads(
            extract_members(payload)["manifest.json"].decode("utf-8")
        )
        assert manifest["credential_scrub"]["value_rule_active"] is False
        assert manifest["credential_scrub"]["known_credential_values"] >= 0

    @pytest.mark.asyncio
    async def test_the_manifest_never_carries_the_values_themselves(self, test_engine):
        payload = await build_bundle(test_engine)
        manifest_bytes = extract_members(payload)["manifest.json"]
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        assert manifest["credential_scrub"]["value_rule_active"] is True
        assert XC_PASS.encode() not in manifest_bytes
        assert XC_USER.encode() not in manifest_bytes


class TestExistingBehaviourIsPreserved:
    """Regression guards for what the shipped scrubber already did right."""

    @pytest.mark.asyncio
    async def test_the_hostname_is_still_replaced(self, test_engine):
        payload = await build_bundle(test_engine)
        members = extract_members(payload)
        assert scan_members(members, "provider.example.net") == {}

    @pytest.mark.asyncio
    async def test_the_three_segment_form_still_yields_user_pass(self, test_engine):
        """The one shape the shipped regex DID match kept the stream id and
        substituted literal ``user``/``pass``. That output is load-bearing for
        anyone reading an old bundle, so it must not change shape."""
        payload = await build_bundle(test_engine)
        members = extract_members(payload)
        channels = json.loads(members["channels.json"].decode("utf-8"))
        urls = [s["url"] for s in channels[0]["streams"]]
        assert any(u.endswith("/13580.ts") for u in urls), urls

    @pytest.mark.asyncio
    async def test_the_stream_id_survives_every_shape(self, test_engine):
        """A scrub that destroyed the address would cost the bundle its whole
        diagnostic value. The stream id is what a support ticket cross-refs."""
        payload = await build_bundle(test_engine)
        members = extract_members(payload)
        channels = json.loads(members["channels.json"].decode("utf-8"))
        urls = [s["url"] for s in channels[0]["streams"] if s["url"]]
        assert urls, "every URL was dropped entirely"
        assert any("13580" in u for u in urls), urls
