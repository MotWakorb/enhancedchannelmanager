"""Unit tests for :mod:`services.attribution_reconciler` (bd-mlcla).

The reconciler is the structural core of the networking-agnostic
attribution redesign. These tests pin the four PO constraints at the
pure-function level (no HTTP, no DB, no resolver mocks):

1. Networking-agnostic — IP only ranks, never gates. Flipping the
   trusted-network set changes ONLY tie-break order, not which users
   attribute (constraint 1 + the bd-mlcla brief's test #8).
2. Anti-collapse — a user is consumed at most once; two distinct viewers
   never collapse onto the same user (brief test #3).
3. Anti-broadcast — one user never lands on two connections (brief
   test #4).
4. No phantom clients — surplus users (users > connections) surface only
   in the channel-level viewer list (brief test #6).

Plus the count-mismatch matrix (#6) and the Option-B rollup predicate
(#7). The bridge-IP / NAT'd case (#1, #2) is exercised end-to-end in the
resolver + stats + bandwidth suites; here we prove the IP-agnostic
property the higher layers depend on.

Synthetic identities only.
"""
from __future__ import annotations

import logging

import pytest

from observability import JsonFormatter
from services.attribution_reconciler import (
    AMBIGUOUS_GROUP_PREDICATE,
    CandidateUser,
    Connection,
    build_trusted_networks,
    distinct_users,
    eligible_connections,
    has_dispatcharr_account_identity,
    ip_priority,
    normalize_client_ip,
    reconcile_channel,
    rollup_client_ips,
    rollup_label,
    url_embeds_username,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _conn(
    client_id: str,
    *,
    ip: str | None = None,
    connected_at: float | None = None,
    url_identity: bool = False,
    account_identity: bool = False,
    server_proxy: bool = False,
) -> Connection:
    return Connection(
        client_id=client_id,
        ip_address=ip,
        connected_at=connected_at,
        has_url_identity=url_identity,
        has_account_identity=account_identity,
        is_server_proxy=server_proxy,
    )


def _user(
    name: str,
    *,
    user_id: str | None = None,
    last_activity=None,
    source: str = "jellyfin",
    client_ip: str | None = None,
) -> CandidateUser:
    return CandidateUser(
        user_name=name,
        user_id=user_id,
        last_activity_date=last_activity,
        source=source,
        client_ip=client_ip,
    )


def _assigned_names(result) -> dict[str, str | None]:
    """Map client_id → assigned single user_name (None if User #0 or rollup)."""
    return {
        a.client_id: (a.user.user_name if a.user else None)
        for a in result.assignments
    }


def test_malformed_trusted_network_log_does_not_retain_input(caplog):
    sensitive_value = "username:provider-password@example.invalid"

    with caplog.at_level(logging.DEBUG, logger="services.attribution_reconciler"):
        assert build_trusted_networks(configured_cidrs=[sensitive_value]) == []

    records = [
        record
        for record in caplog.records
        if record.name == "services.attribution_reconciler"
    ]
    assert [record.getMessage() for record in records] == [
        "[ATTR-RECONCILE] Skipping unparsable trusted-network entry"
    ]
    assert all(sensitive_value not in repr(record.args) for record in records)
    assert all(sensitive_value not in JsonFormatter().format(record) for record in records)


# ---------------------------------------------------------------------------
# Eligibility — the URL-identity discriminator that replaces the IP gate
# ---------------------------------------------------------------------------


class TestUrlIdentityDiscriminator:
    """The original (now DORMANT) URL-identity discriminator (brief test #5).

    RETAINED-BUT-NOT-WIRED (bd-spzeu): ``url_embeds_username`` and the
    ``has_url_identity`` eligibility branch are production-dead — both
    reconciler call sites hardcode ``has_url_identity=False`` and the live
    discriminator is ``has_account_identity``. These tests pin the dormant
    scaffolding so it stays correct for the day a per-client credentialed-URL
    signal surfaces; they do NOT exercise a live production path. See
    ``services.attribution_reconciler.url_embeds_username`` for the rationale.
    """

    @pytest.mark.parametrize(
        "url",
        [
            "http://provider.tv/live/mot/secretpw/85796.ts",
            "http://provider.tv/movie/mot/secretpw/12345.mkv",
            "http://provider.tv/mot/secretpw/85796",
            "http://provider.tv/mot/secretpw/85796.ts",
            "http://provider.tv/get.php?username=mot&password=pw&type=m3u_plus",
            "http://provider.tv/player_api.php?username=mot&password=pw",
        ],
    )
    def test_xc_credentialed_urls_are_url_identity(self, url):
        assert url_embeds_username(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "",
            None,
            "http://dispatcharr:9191/proxy/ts/stream/abc-uuid",
            "http://dispatcharr/output/12.ts",
            "not a url",
        ],
    )
    def test_non_credentialed_urls_are_not_url_identity(self, url):
        assert url_embeds_username(url) is False


class TestEligibility:
    def test_url_identity_connections_excluded(self):
        """Connections with an embedded URL username are not reconciled.

        Pins the DORMANT ``has_url_identity`` eligibility branch (bd-spzeu:
        RETAINED-BUT-NOT-WIRED — always ``False`` in production). Kept correct
        for a future per-client credentialed-URL signal.
        """
        conns = [
            _conn("c1", ip="172.18.0.1"),
            _conn("c2", ip="10.0.0.5", url_identity=True),
        ]
        eligible = eligible_connections(conns)
        assert [c.client_id for c in eligible] == ["c1"]

    def test_url_identity_connection_never_gets_a_user(self):
        """A direct-IPTV client is excluded even when users are available.

        Also pins the DORMANT ``has_url_identity`` branch (bd-spzeu). See
        :meth:`test_url_identity_connections_excluded`.
        """
        conns = [_conn("xc1", ip="10.0.0.5", url_identity=True)]
        users = [_user("alice")]
        result = reconcile_channel(conns, users)
        # No eligible connections → no assignments; the user surfaces only
        # at channel level.
        assert result.assignments == ()
        assert [u.user_name for u in result.channel_viewers] == ["alice"]

    def test_account_identity_connections_excluded(self):
        """bd-rools: a Dispatcharr-account connection is not reconciled."""
        conns = [
            _conn("c1", ip="172.18.0.1"),  # anonymous media-server pull
            _conn("c2", ip="10.0.0.5", account_identity=True),  # direct sub
        ]
        eligible = eligible_connections(conns)
        assert [c.client_id for c in eligible] == ["c1"]

    def test_account_identity_connection_never_absorbs_media_server_user(self):
        """bd-rools (re-fix bd-cat70): a genuine direct XC subscriber sharing a
        channel with a media-server viewer must NOT absorb that viewer.

        kmfelmer (Dispatcharr account) + an anonymous media-server pull on
        the same channel; the resolver (now non-IP-gated) offers MotWakorb.
        kmfelmer is excluded from reconciliation, so MotWakorb pairs to the
        anonymous pull — kmfelmer keeps no media-server name and the real
        viewer is NOT dropped to User #0.
        """
        kmfelmer = _conn("kmfelmer", ip="172.16.0.50", account_identity=True)
        media_pull = _conn("anon", ip="172.16.0.19")
        users = [_user("MotWakorb", user_id="uid-mw")]
        result = reconcile_channel([kmfelmer, media_pull], users)
        names = _assigned_names(result)
        # kmfelmer never appears in the assignments (excluded upstream).
        assert "kmfelmer" not in names
        # The media-server viewer lands on the anonymous pull.
        assert names["anon"] == "MotWakorb"

    def test_account_identity_connection_never_server_proxy_carrier(self):
        """bd-rools: even when its IP equals the server IP, an account-identity
        connection (set is_server_proxy=False by the call site) is excluded
        and never carries the proxy remainder."""
        # The call sites set is_server_proxy=False for account-identity conns;
        # the reconciler additionally drops it via eligible_connections().
        sub = _conn("sub", ip="172.16.0.19", account_identity=True)
        users = [_user("alice"), _user("bob")]
        result = reconcile_channel([sub], users)
        assert result.assignments == ()
        assert {u.user_name for u in result.channel_viewers} == {"alice", "bob"}


class TestAccountIdentityHelper:
    """bd-rools: the Dispatcharr-account-identity discriminator helper."""

    @pytest.mark.parametrize(
        "user_id",
        [1, 3, "3", "42", 9],
    )
    def test_positive_user_id_is_account_identity(self, user_id):
        assert has_dispatcharr_account_identity(user_id=user_id) is True

    @pytest.mark.parametrize(
        "user_id",
        [None, "", 0, "0", -1, "-1", "abc", True, False, 42.0],
    )
    def test_anonymous_or_sentinel_user_id_is_not_account_identity(self, user_id):
        assert has_dispatcharr_account_identity(user_id=user_id) is False

    def test_real_username_without_user_id_is_account_identity(self):
        # Defensive fallback: a real name with no numeric id still counts.
        assert (
            has_dispatcharr_account_identity(user_id=None, username="kmfelmer")
            is True
        )

    @pytest.mark.parametrize("username", [None, "", "   ", "0"])
    def test_blank_or_sentinel_username_is_not_account_identity(self, username):
        assert (
            has_dispatcharr_account_identity(user_id="0", username=username)
            is False
        )


# ---------------------------------------------------------------------------
# IP priority — soft hint, never reject
# ---------------------------------------------------------------------------


class TestIpPriority:
    def test_trusted_cidr_ranks_first(self):
        nets = build_trusted_networks(configured_cidrs=["172.16.0.0/24"])
        assert ip_priority("172.16.0.19", nets) == 0
        assert ip_priority("172.18.0.1", nets) == 1

    def test_bare_ip_treated_as_host(self):
        nets = build_trusted_networks(server_ips=["172.16.0.19"])
        assert ip_priority("172.16.0.19", nets) == 0
        assert ip_priority("172.16.0.20", nets) == 1

    def test_missing_or_bad_ip_is_unknown_not_rejected(self):
        nets = build_trusted_networks(configured_cidrs=["172.16.0.0/24"])
        # UNKNOWN bucket (1), NOT a reject — there is no reject value.
        assert ip_priority(None, nets) == 1
        assert ip_priority("not-an-ip", nets) == 1

    def test_unparsable_cidr_skipped_silently(self):
        # A junk operator entry must not raise and must not poison ranking.
        nets = build_trusted_networks(configured_cidrs=["garbage", "172.16.0.0/24"])
        assert ip_priority("172.16.0.19", nets) == 0


# ---------------------------------------------------------------------------
# distinct_users — dedup + recency ranking
# ---------------------------------------------------------------------------


class TestDistinctUsers:
    def test_dedup_by_user_id(self):
        users = [
            _user("alice", user_id="u1", last_activity="2026-05-20T10:00:00Z"),
            _user("alice", user_id="u1", last_activity="2026-05-20T11:00:00Z"),
        ]
        ranked = distinct_users(users)
        assert len(ranked) == 1
        # Keeps the fresher activity.
        assert ranked[0].last_activity_date == "2026-05-20T11:00:00Z"

    def test_ranked_most_recent_first(self):
        users = [
            _user("old", user_id="u1", last_activity="2026-05-20T08:00:00Z"),
            _user("new", user_id="u2", last_activity="2026-05-20T12:00:00Z"),
        ]
        ranked = distinct_users(users)
        assert [u.user_name for u in ranked] == ["new", "old"]

    def test_none_activity_sinks_to_bottom(self):
        users = [
            _user("noact", user_id="u1", last_activity=None),
            _user("hasact", user_id="u2", last_activity="2026-05-20T08:00:00Z"),
        ]
        ranked = distinct_users(users)
        assert [u.user_name for u in ranked] == ["hasact", "noact"]

    def test_same_name_different_source_kept_distinct(self):
        users = [
            _user("bob", user_id=None, source="plex"),
            _user("bob", user_id=None, source="jellyfin"),
        ]
        ranked = distinct_users(users)
        assert len(ranked) == 2


# ---------------------------------------------------------------------------
# Count behavior matrix (brief test #6)
# ---------------------------------------------------------------------------


class TestCountBehavior:
    def test_exact_one_to_one_same_bucket_is_ambiguous_rollup(self):
        """Two connections in the SAME IP-priority bucket + two users is the
        genuinely-ambiguous case (Option B), NOT a confident 1:1 pin.

        ``connected_at`` orders WHICH connection gets the top user when
        users < connections, but it does NOT tell us which media-server
        viewer is which — connection start time does not correlate with
        session identity any more than the (NAT'd) source IP does. So when
        the count is exactly equal and there is no IP signal, the PO's
        "genuinely indistinguishable" predicate fires and both rows show
        the rollup. The channel-level set is still exactly {alice, bob}."""
        conns = [_conn("c1", connected_at=1.0), _conn("c2", connected_at=2.0)]
        users = [
            _user("alice", user_id="u1", last_activity="2026-05-20T12:00:00Z"),
            _user("bob", user_id="u2", last_activity="2026-05-20T11:00:00Z"),
        ]
        result = reconcile_channel(conns, users)
        assert all(a.is_rollup for a in result.assignments)
        # The true distinct set is correct at channel level.
        assert {u.user_name for u in result.channel_viewers} == {"alice", "bob"}

    def test_exact_one_to_one_distinct_buckets_pins_single_names(self):
        """When IP priority splits the two connections into distinct buckets,
        the count-equal case resolves to confident single names (no
        rollup) — each single-connection group pairs to one user."""
        conns = [
            _conn("trusted", ip="172.16.0.19", connected_at=1.0),
            _conn("nat", ip="172.18.0.1", connected_at=2.0),
        ]
        users = [
            _user("alice", user_id="u1", last_activity="2026-05-20T12:00:00Z"),
            _user("bob", user_id="u2", last_activity="2026-05-20T11:00:00Z"),
        ]
        result = reconcile_channel(
            conns, users,
            trusted_networks=build_trusted_networks(server_ips=["172.16.0.19"]),
        )
        names = _assigned_names(result)
        assert all(not a.is_rollup for a in result.assignments)
        assert {n for n in names.values() if n} == {"alice", "bob"}

    def test_fewer_users_than_connections_surplus_stay_user0(self):
        conns = [
            _conn("c1", connected_at=1.0),
            _conn("c2", connected_at=2.0),
            _conn("c3", connected_at=3.0),
        ]
        users = [_user("alice", user_id="u1", last_activity="2026-05-20T12:00:00Z")]
        result = reconcile_channel(conns, users)
        names = _assigned_names(result)
        # Exactly one connection attributed; the other two stay User #0.
        attributed = [cid for cid, n in names.items() if n]
        unattributed = [cid for cid, n in names.items() if n is None]
        assert len(attributed) == 1
        assert len(unattributed) == 2
        # No rollup — single user means single name, never a rollup.
        assert all(not a.is_rollup for a in result.assignments)

    def test_more_users_than_connections_surplus_only_in_channel_viewers(self):
        conns = [_conn("c1", connected_at=1.0)]
        users = [
            _user("alice", user_id="u1", last_activity="2026-05-20T12:00:00Z"),
            _user("bob", user_id="u2", last_activity="2026-05-20T11:00:00Z"),
            _user("carol", user_id="u3", last_activity="2026-05-20T10:00:00Z"),
        ]
        result = reconcile_channel(conns, users)
        # Only one connection — it gets the single top-ranked user (no
        # rollup: one connection is never ambiguous).
        assert len(result.assignments) == 1
        assert result.assignments[0].user.user_name == "alice"
        assert not result.assignments[0].is_rollup
        # All three users surface at the channel level (no phantom clients).
        assert [u.user_name for u in result.channel_viewers] == ["alice", "bob", "carol"]

    def test_no_users_all_stay_user0(self):
        conns = [_conn("c1"), _conn("c2")]
        result = reconcile_channel(conns, [])
        assert all(a.user is None and not a.is_rollup for a in result.assignments)
        assert result.channel_viewers == ()


# ---------------------------------------------------------------------------
# Anti-collapse (brief test #3) + anti-broadcast (brief test #4)
# ---------------------------------------------------------------------------


class TestAntiCollapseAntiBroadcast:
    def test_two_distinct_viewers_never_collapse_onto_one_user(self):
        """jkaisersoze regression: two connections, two users, never the
        same user twice."""
        conns = [
            _conn("c1", ip="172.16.0.19", connected_at=1.0),
            _conn("c2", ip="172.16.0.19", connected_at=2.0),
        ]
        users = [
            _user("alice", user_id="u1", last_activity="2026-05-20T12:00:00Z"),
            _user("bob", user_id="u2", last_activity="2026-05-20T11:00:00Z"),
        ]
        result = reconcile_channel(
            conns, users,
            trusted_networks=build_trusted_networks(server_ips=["172.16.0.19"]),
        )
        # Same IP, same priority bucket, 2 conns + 2 users → Option B rollup
        # (genuinely ambiguous). Each row carries BOTH names; neither is
        # duplicated onto a single pinned slot.
        assert all(a.is_rollup for a in result.assignments)
        for a in result.assignments:
            names = [u.user_name for u in a.rollup_users]
            assert sorted(names) == ["alice", "bob"]

    def test_one_user_never_broadcast_to_multiple_connections(self):
        """Anti-broadcast: a single user with multiple connections does NOT
        appear on every connection."""
        conns = [
            _conn("c1", connected_at=1.0),
            _conn("c2", connected_at=2.0),
        ]
        users = [_user("solo", user_id="u1", last_activity="2026-05-20T12:00:00Z")]
        result = reconcile_channel(conns, users)
        attributed = [a for a in result.assignments if a.user]
        # Exactly ONE connection carries the user; the other is User #0.
        assert len(attributed) == 1
        assert attributed[0].user.user_name == "solo"

    def test_distinct_priority_buckets_resolve_to_single_names(self):
        """When IP priority orders the connections (different buckets), each
        unambiguously pairs to one user — no rollup."""
        conns = [
            _conn("trusted", ip="172.16.0.19", connected_at=5.0),
            _conn("nat", ip="172.18.0.1", connected_at=1.0),
        ]
        users = [
            _user("alice", user_id="u1", last_activity="2026-05-20T12:00:00Z"),
            _user("bob", user_id="u2", last_activity="2026-05-20T11:00:00Z"),
        ]
        result = reconcile_channel(
            conns, users,
            trusted_networks=build_trusted_networks(server_ips=["172.16.0.19"]),
        )
        names = _assigned_names(result)
        # Two separate single-connection groups → single names, no rollup.
        assert all(not a.is_rollup for a in result.assignments)
        assert names["trusted"] == "alice"  # trusted bucket gets top user
        assert names["nat"] == "bob"


# ---------------------------------------------------------------------------
# Option B predicate (brief test #7)
# ---------------------------------------------------------------------------


class TestOptionBRollup:
    def test_ambiguous_group_gets_rollup(self):
        """2+ indistinguishable connections + 2+ users → each row shows the
        N-viewers rollup, not a single pinned name."""
        conns = [
            _conn("c1", ip="172.18.0.1", connected_at=1.0),
            _conn("c2", ip="172.18.0.1", connected_at=2.0),
        ]
        users = [
            _user("alice", user_id="u1", last_activity="2026-05-20T12:00:00Z"),
            _user("bob", user_id="u2", last_activity="2026-05-20T11:00:00Z"),
        ]
        result = reconcile_channel(conns, users)
        assert all(a.is_rollup for a in result.assignments)
        assert all(a.user is None for a in result.assignments)

    def test_rollup_label_format(self):
        users = (
            _user("alice", user_id="u1"),
            _user("bob", user_id="u2"),
        )
        assert rollup_label(users) == "2 viewers: alice, bob"

    def test_rollup_label_dedups_names(self):
        users = (
            _user("alice", user_id="u1", source="emby"),
            _user("alice", user_id="u2", source="plex"),
        )
        # Two distinct candidates that happen to share a display name → the
        # label collapses the duplicate name but the count reflects names.
        assert rollup_label(users) == "1 viewers: alice"

    def test_single_user_in_group_is_not_a_rollup(self):
        """A 2-connection group offered only ONE user is not ambiguous: the
        user pins to the top connection, the other stays User #0."""
        conns = [
            _conn("c1", ip="172.18.0.1", connected_at=1.0),
            _conn("c2", ip="172.18.0.1", connected_at=2.0),
        ]
        users = [_user("alice", user_id="u1", last_activity="2026-05-20T12:00:00Z")]
        result = reconcile_channel(conns, users)
        assert all(not a.is_rollup for a in result.assignments)
        names = _assigned_names(result)
        assert sorted(n for n in names.values() if n) == ["alice"]


# ---------------------------------------------------------------------------
# Server-proxy branch (bd-mlcla B1: direct-first / proxy-remainder)
# ---------------------------------------------------------------------------


class TestServerProxyBranch:
    """The proxy branch had ZERO coverage on this PR — these pin the B1 fix.

    The PO decision is NEVER DROP THE VIEWER: distinct direct connections
    are reconciled FIRST, and the server-proxy connection carries only the
    REMAINING (unconsumed) users. A browser-direct viewer sharing a channel
    with a proxy pull therefore always gets its own name when an unconsumed
    matching user exists.
    """

    def test_b1a_mixed_proxy_plus_one_browser_direct_both_attributed(self):
        """(a) Mixed proxy + 1 browser-direct same channel: the browser-direct
        connection gets its distinct name; the proxy carries the remainder.

        This is the exact TSN5 mixed case the feature exists to fix. Before
        the B1 fix the proxy early-return stamped the browser-direct
        connection to User #0 — the regression this test guards."""
        conns = [
            _conn("proxy", ip="172.16.0.19", connected_at=1.0, server_proxy=True),
            _conn("browser", ip="172.18.0.1", connected_at=2.0),
        ]
        users = [
            _user("alice", user_id="u1", last_activity="2026-05-20T12:00:00Z"),
            _user("bob", user_id="u2", last_activity="2026-05-20T11:00:00Z"),
        ]
        result = reconcile_channel(
            conns, users,
            trusted_networks=build_trusted_networks(server_ips=["172.16.0.19"]),
        )
        by_id = {a.client_id: a for a in result.assignments}

        # Browser-direct viewer is NEVER dropped: it gets a distinct single
        # name (the top-ranked unconsumed user).
        browser = by_id["browser"]
        assert browser.user is not None
        assert browser.user.user_name == "alice"
        assert not browser.is_rollup

        # Proxy carries the REMAINING single user (bob) — not a phantom, not
        # a duplicate of alice, not User #0.
        proxy = by_id["proxy"]
        assert proxy.user is not None
        assert proxy.user.user_name == "bob"
        assert not proxy.is_proxy_multi  # only one remaining → single user

        # No user appears twice across the channel (anti-collapse).
        singles = [a.user.user_name for a in result.assignments if a.user]
        assert sorted(singles) == ["alice", "bob"]

    def test_b1b_proxy_serving_multiple_viewers_no_direct_preserves_rollup(self):
        """(b) Proxy serving multiple app-viewers with NO direct connection:
        the proxy carries the full viewer list (bd-r5f0c.9 preserved).

        With no direct connection consuming any user, the remainder IS the
        full set, so the proxy rollup is unchanged from pre-B1 behavior."""
        conns = [
            _conn("proxy", ip="172.16.0.19", connected_at=1.0, server_proxy=True),
        ]
        users = [
            _user("alice", user_id="u1", last_activity="2026-05-20T12:00:00Z"),
            _user("bob", user_id="u2", last_activity="2026-05-20T11:00:00Z"),
            _user("carol", user_id="u3", last_activity="2026-05-20T10:00:00Z"),
        ]
        result = reconcile_channel(
            conns, users,
            trusted_networks=build_trusted_networks(server_ips=["172.16.0.19"]),
        )
        assert len(result.assignments) == 1
        proxy = result.assignments[0]
        assert proxy.is_proxy_multi
        # Full set, recency order, position 0 = legacy single name.
        assert [u.user_name for u in proxy.proxy_viewers] == ["alice", "bob", "carol"]
        # Not an Option-B rollup — the proxy knows all its sessions.
        assert not proxy.is_rollup

    def test_b1c_proxy_plus_two_indistinguishable_direct_none_dropped(self):
        """(c) Proxy + 2 indistinguishable browser-direct + enough users:
        the direct rows get distinct names or the Option-B rollup per the
        predicate; none drop to User #0 while an unconsumed user exists.

        Two browser-direct connections in the SAME (UNKNOWN) IP-priority
        bucket with 2+ users offered to that bucket → Option-B rollup on the
        direct rows. The proxy carries whatever the direct group did not
        consume. The key invariant: no direct row is User #0 while users
        remain."""
        conns = [
            _conn("proxy", ip="172.16.0.19", connected_at=1.0, server_proxy=True),
            _conn("d1", ip="172.18.0.1", connected_at=2.0),
            _conn("d2", ip="172.18.0.1", connected_at=3.0),
        ]
        users = [
            _user("alice", user_id="u1", last_activity="2026-05-20T12:00:00Z"),
            _user("bob", user_id="u2", last_activity="2026-05-20T11:00:00Z"),
            _user("carol", user_id="u3", last_activity="2026-05-20T10:00:00Z"),
        ]
        result = reconcile_channel(
            conns, users,
            trusted_networks=build_trusted_networks(server_ips=["172.16.0.19"]),
        )
        by_id = {a.client_id: a for a in result.assignments}

        # Two indistinguishable direct connections → Option-B rollup (the
        # group consumed the top two users alice + bob).
        for cid in ("d1", "d2"):
            assert by_id[cid].is_rollup, cid
            names = sorted(u.user_name for u in by_id[cid].rollup_users)
            assert names == ["alice", "bob"]
            # Never User #0 while users remained.
            assert not (by_id[cid].user is None and not by_id[cid].is_rollup)

        # Proxy carries the single remaining user (carol).
        proxy = by_id["proxy"]
        assert proxy.user is not None
        assert proxy.user.user_name == "carol"

        # Full distinct set still surfaces at channel level.
        assert {u.user_name for u in result.channel_viewers} == {
            "alice", "bob", "carol"
        }

    def test_b1d_is_server_proxy_misfire_browser_direct_with_server_ip(self):
        """(d) is_server_proxy mis-fire edge: a browser-direct connection
        whose observed source IP happens to equal the configured server IP is
        mis-flagged as the proxy (exact-IP-equality, no corroborating signal).

        The call sites set ``is_server_proxy = (source_ip == server_ip)``. If
        the operator runs the browser on the media-server host, that one
        connection trips the flag. This test pins the bounded behavior: a
        SECOND genuine direct viewer (NAT'd) is still reconciled first and
        keeps its own name; the mis-flagged connection carries the remainder
        rather than swallowing everyone. The blast radius is limited to that
        one connection's display, never the other viewer's attribution."""
        conns = [
            # Browser on the server host → mis-flagged as proxy.
            _conn("misfired", ip="172.16.0.19", connected_at=1.0, server_proxy=True),
            # Genuine NAT'd browser-direct viewer.
            _conn("nat", ip="172.18.0.1", connected_at=2.0),
        ]
        users = [
            _user("alice", user_id="u1", last_activity="2026-05-20T12:00:00Z"),
            _user("bob", user_id="u2", last_activity="2026-05-20T11:00:00Z"),
        ]
        result = reconcile_channel(
            conns, users,
            trusted_networks=build_trusted_networks(server_ips=["172.16.0.19"]),
        )
        by_id = {a.client_id: a for a in result.assignments}

        # The genuine NAT'd viewer is reconciled FIRST and keeps its own
        # distinct name — the mis-fire does not suppress it.
        nat = by_id["nat"]
        assert nat.user is not None
        assert nat.user.user_name == "alice"

        # The mis-flagged connection carries the remainder (bob) — bounded
        # blast radius, not a User #0 drop and not a collapse onto alice.
        misfired = by_id["misfired"]
        assert misfired.user is not None
        assert misfired.user.user_name == "bob"

        # Anti-collapse holds even under the mis-fire.
        singles = [a.user.user_name for a in result.assignments if a.user]
        assert sorted(singles) == ["alice", "bob"]

    def test_proxy_with_no_users_stays_user0(self):
        """A proxy connection with zero candidate users stays User #0 — no
        phantom, no broadcast."""
        conns = [
            _conn("proxy", ip="172.16.0.19", server_proxy=True),
        ]
        result = reconcile_channel(conns, [])
        assert len(result.assignments) == 1
        assert result.assignments[0].user is None
        assert not result.assignments[0].is_proxy_multi

    def test_proxy_remainder_empty_when_direct_consume_all(self):
        """When the direct connections consume every user, the proxy gets
        User #0 (the remainder is empty) — no double-count."""
        conns = [
            _conn("proxy", ip="172.16.0.19", connected_at=1.0, server_proxy=True),
            _conn("nat", ip="172.18.0.1", connected_at=2.0),
        ]
        users = [_user("solo", user_id="u1", last_activity="2026-05-20T12:00:00Z")]
        result = reconcile_channel(
            conns, users,
            trusted_networks=build_trusted_networks(server_ips=["172.16.0.19"]),
        )
        by_id = {a.client_id: a for a in result.assignments}
        assert by_id["nat"].user.user_name == "solo"
        assert by_id["proxy"].user is None  # remainder empty → User #0
        assert not by_id["proxy"].is_proxy_multi

    def test_two_proxies_only_first_carries_remainder(self):
        """Exotic multi-proxy topology: only the first proxy carries the
        remaining set; a second would double-count the same upstream
        sessions, so it gets User #0."""
        conns = [
            _conn("proxy1", ip="172.16.0.19", connected_at=1.0, server_proxy=True),
            _conn("proxy2", ip="172.16.0.19", connected_at=2.0, server_proxy=True),
        ]
        users = [
            _user("alice", user_id="u1", last_activity="2026-05-20T12:00:00Z"),
            _user("bob", user_id="u2", last_activity="2026-05-20T11:00:00Z"),
        ]
        result = reconcile_channel(
            conns, users,
            trusted_networks=build_trusted_networks(server_ips=["172.16.0.19"]),
        )
        by_id = {a.client_id: a for a in result.assignments}
        # First proxy carries the full set (no direct connections consumed
        # anything).
        assert by_id["proxy1"].is_proxy_multi
        assert [u.user_name for u in by_id["proxy1"].proxy_viewers] == ["alice", "bob"]
        # Second proxy → User #0 (no double-count).
        assert by_id["proxy2"].user is None
        assert not by_id["proxy2"].is_proxy_multi


# ---------------------------------------------------------------------------
# bd-spzeu — ACCEPTED LIMITATION boundary pin (mixed media-server + anonymous
# direct-IPTV/XC on one channel, only the media-server user matched)
# ---------------------------------------------------------------------------


class TestAcceptedMixedDirectBoundary:
    """Pin the ACCEPTED current outcome of the mixed media-server + genuine
    direct-IPTV/XC same-channel case (bd-spzeu, follow-up to bd-4w9w6).

    THIS IS NOT A BUG TO FIX. It is the residual, PO-accepted limitation that
    bd-4w9w6 / bd-rools left in place, pinned here so it cannot silently drift
    and so it serves as the target if a per-client identity signal ever
    surfaces.

    The discriminator that distinguishes a genuine direct-IPTV subscriber from
    an anonymous media-server pull is a per-client identity signal:
    ``has_account_identity`` (a positive Dispatcharr ``user_id``) or
    ``has_url_identity`` (per-client credentialed URL). When the direct-IPTV/XC
    client carries EITHER signal it is correctly excluded — that path is the
    happy case and is pinned by
    :class:`TestEligibility.test_account_identity_connection_never_absorbs_media_server_user`.

    The accepted limitation lives in the gap WHERE NO SUCH SIGNAL EXISTS: a
    genuine direct-IPTV/XC client that is ANONYMOUS to Dispatcharr
    (``user_id == "0"`` / ``0`` / ``None``, no per-client credentialed URL —
    exactly what Dispatcharr ``/proxy/ts/status`` surfaces for many real direct
    clients) is INDISTINGUISHABLE from the anonymous media-server pull. Both
    reach the reconciler as plain eligible connections with no identity. Source
    IP only RANKS (soft hint), it never identifies. So when only the
    media-server user matched, the reconciler may pin that user's name to the
    direct-IPTV connection (the wrong one) and drop the real media-server pull
    to User #0 — depending purely on tie-break order.

    Net-positive vs. the prior all-User#0 state (the media-server name DOES
    surface on the channel; it is the per-connection ROW that can be wrong),
    which is why this is accepted, not blocked. Would change if a per-client
    identity signal ever surfaces on the anonymous direct connection.
    """

    def test_anonymous_direct_iptv_may_take_media_server_name_when_ranked_first(self):
        """ACCEPTED LIMITATION: an anonymous genuine direct-IPTV/XC client that
        ranks ahead of the anonymous media-server pull takes the media-server
        user's name, dropping the real pull to User #0.

        No per-client identity signal exists to tell the two anonymous
        connections apart, so the mis-pin is structural, not a logic error.
        Pinned here so it cannot silently drift; the assertions describe
        TODAY'S behavior, not a desired one. If a per-client identity signal
        surfaces (e.g. the direct client gains a Dispatcharr account id), this
        scenario moves to the happy path and this test would be updated to
        assert the direct client is excluded instead.
        """
        # Genuine direct-IPTV/XC viewer, anonymous to Dispatcharr (no account
        # identity, no per-client URL identity), connected earlier so it sorts
        # ahead of the media-server pull in the shared UNKNOWN IP-priority
        # bucket. There is NO signal that marks it as a direct client.
        direct_xc = _conn("xc_client", ip="203.0.113.50", connected_at=1.0)
        # The anonymous media-server pull (later connected_at → sorts second).
        media_pull = _conn("media_pull", ip="172.18.0.1", connected_at=2.0)
        # Only the media-server user matched on this channel.
        users = [
            _user("MotWakorb", user_id="uid-mw", last_activity="2026-05-20T12:00:00Z")
        ]

        result = reconcile_channel([direct_xc, media_pull], users)
        names = _assigned_names(result)

        # ACCEPTED current outcome: the media-server name lands on the WRONG
        # connection (the direct-IPTV client), because nothing distinguishes
        # the two anonymous connections except rank order.
        assert names["xc_client"] == "MotWakorb"
        # The real media-server pull is left at User #0.
        assert names["media_pull"] is None
        # Neither is a rollup — only one candidate user, so no Option-B group.
        by_id = {a.client_id: a for a in result.assignments}
        assert not by_id["xc_client"].is_rollup
        assert not by_id["media_pull"].is_rollup
        # Net-positive invariant that MUST hold: the media-server user still
        # surfaces somewhere on the channel (not the prior all-User#0 state).
        assert "MotWakorb" in {u.user_name for u in result.channel_viewers}

    def test_account_identity_direct_client_does_not_trip_the_limitation(self):
        """Companion to the limitation: the moment the direct-IPTV client DOES
        carry a per-client identity signal (a Dispatcharr account id), the
        limitation disappears — it is excluded and the media-server user pairs
        to the real anonymous pull. This is the target state the limitation
        would reach if such a signal ever surfaces on the anonymous case.
        """
        # Same topology, but now the direct client carries an account identity.
        direct_xc = _conn(
            "xc_client", ip="203.0.113.50", connected_at=1.0, account_identity=True
        )
        media_pull = _conn("media_pull", ip="172.18.0.1", connected_at=2.0)
        users = [
            _user("MotWakorb", user_id="uid-mw", last_activity="2026-05-20T12:00:00Z")
        ]

        result = reconcile_channel([direct_xc, media_pull], users)
        names = _assigned_names(result)

        # The identified direct client is excluded entirely (no assignment).
        assert "xc_client" not in names
        # The media-server user correctly pairs to the real anonymous pull.
        assert names["media_pull"] == "MotWakorb"


# ---------------------------------------------------------------------------
# Topology-agnostic property (brief test #1, #2, #8)
# ---------------------------------------------------------------------------


class TestTopologyAgnostic:
    @pytest.mark.parametrize(
        "ip",
        ["172.16.0.19", "172.18.0.1", "10.88.0.7", "192.168.1.50", None],
    )
    def test_single_viewer_attributes_regardless_of_source_ip(self, ip):
        """One connection, one user — attributes correctly no matter what
        source IP ECM observed (host, bridge gateway, NAT, container,
        configured server IP)."""
        conns = [_conn("c1", ip=ip, connected_at=1.0)]
        users = [_user("motwakorb", user_id="u1", last_activity="2026-05-20T12:00:00Z")]
        # Even with a trusted set that does NOT contain this IP.
        result = reconcile_channel(
            conns, users,
            trusted_networks=build_trusted_networks(server_ips=["172.16.0.19"]),
        )
        assert result.assignments[0].user.user_name == "motwakorb"

    def test_flipping_detected_gateway_changes_order_not_correctness(self):
        """Brief test #8: changing which gateway is auto-detected only
        reorders tie-breaks; the SET of attributed users is identical."""
        conns = [
            _conn("a", ip="172.18.0.1", connected_at=1.0),
            _conn("b", ip="172.19.0.1", connected_at=2.0),
        ]
        users = [
            _user("alice", user_id="u1", last_activity="2026-05-20T12:00:00Z"),
            _user("bob", user_id="u2", last_activity="2026-05-20T11:00:00Z"),
        ]

        # Detect gateway 172.18.0.1 as trusted.
        nets1 = build_trusted_networks(detected_gateways=["172.18.0.1"])
        r1 = reconcile_channel(conns, users, trusted_networks=nets1)

        # Flip: detect 172.19.0.1 instead.
        nets2 = build_trusted_networks(detected_gateways=["172.19.0.1"])
        r2 = reconcile_channel(conns, users, trusted_networks=nets2)

        def attributed_set(result):
            names = set()
            for a in result.assignments:
                if a.user:
                    names.add(a.user.user_name)
                for u in a.rollup_users:
                    names.add(u.user_name)
            return names

        # Correctness invariant: the set of users surfaced across the
        # channel is identical no matter which gateway was detected.
        assert attributed_set(r1) == attributed_set(r2)
        assert {u.user_name for u in r1.channel_viewers} == {
            u.user_name for u in r2.channel_viewers
        }
        # Both still consume each user at most once (no collapse).
        for result in (r1, r2):
            singles = [a.user.user_name for a in result.assignments if a.user]
            assert len(singles) == len(set(singles))

    def test_predicate_docstring_constant_present(self):
        # The Option-B predicate has one canonical statement.
        assert "2+ connections" in AMBIGUOUS_GROUP_PREDICATE
        assert "2+ distinct candidate users" in AMBIGUOUS_GROUP_PREDICATE


# ---------------------------------------------------------------------------
# bd-7ncci — real client device IP normalization + threading
# ---------------------------------------------------------------------------


class TestNormalizeClientIp:
    """The bare-IP normalizer for media-server-reported client endpoints."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("47.203.164.8", "47.203.164.8"),       # bare IPv4 (Emby live)
            ("172.16.0.2", "172.16.0.2"),            # bare IPv4 (Jellyfin/Plex)
            ("47.203.164.8:51514", "47.203.164.8"),  # host:port → strip port
            ("2001:db8::1", "2001:db8::1"),          # bare IPv6
            ("[2001:db8::1]", "2001:db8::1"),        # bracketed IPv6
            ("[2001:db8::1]:51514", "2001:db8::1"),  # bracketed IPv6 + port
            ("47.203.164.8, 10.0.0.1", "47.203.164.8"),  # XFF → first hop
            ("  47.203.164.8  ", "47.203.164.8"),    # surrounding whitespace
        ],
    )
    def test_normalizes_to_bare_ip(self, raw, expected):
        assert normalize_client_ip(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "   ", "not-an-ip", "999.999.999.999"])
    def test_missing_or_invalid_returns_none(self, raw):
        assert normalize_client_ip(raw) is None


class TestCandidateUserCarriesClientIp:
    """CandidateUser threads the matched session's real client IP through
    the reconciler assignment so the caller can stamp the Client IP field."""

    def test_single_assignment_carries_client_ip(self):
        conns = [_conn("c1", ip="172.18.0.1")]
        users = [_user("MotWakorb", user_id="u1", client_ip="172.16.0.2")]
        result = reconcile_channel(conns, users)
        assignment = result.assignment_for("c1")
        assert assignment is not None
        assert assignment.user is not None
        assert assignment.user.client_ip == "172.16.0.2"

    def test_unattributed_connection_has_no_client_ip(self):
        # One connection, zero candidate users → User #0, no client IP.
        conns = [_conn("c1", ip="172.18.0.1")]
        result = reconcile_channel(conns, [])
        assignment = result.assignment_for("c1")
        assert assignment is not None
        assert assignment.user is None
        assert assignment.rollup_users == ()
        assert assignment.proxy_viewers == ()

    def test_channel_viewers_carry_client_ip(self):
        conns = [_conn("c1", ip="172.18.0.1")]
        users = [
            _user("amitb", user_id="u1", client_ip="47.203.164.8"),
            _user("MotWakorb", user_id="u2", client_ip="172.16.0.2"),
        ]
        result = reconcile_channel(conns, users)
        ip_by_name = {u.user_name: u.client_ip for u in result.channel_viewers}
        assert ip_by_name == {"amitb": "47.203.164.8", "MotWakorb": "172.16.0.2"}

    def test_proxy_viewers_carry_per_viewer_client_ip(self):
        # Server-proxy carrying 2 viewers → proxy_viewers list, each with IP.
        conns = [_conn("proxy", ip="172.16.0.19", server_proxy=True)]
        users = [
            _user("amitb", user_id="u1", client_ip="47.203.164.8"),
            _user("MotWakorb", user_id="u2", client_ip="172.16.0.2"),
        ]
        result = reconcile_channel(conns, users)
        assignment = result.assignment_for("proxy")
        assert assignment is not None
        assert assignment.is_proxy_multi is True
        ips = {u.client_ip for u in assignment.proxy_viewers}
        assert ips == {"47.203.164.8", "172.16.0.2"}


class TestRollupClientIps:
    """rollup_client_ips renders the distinct set behind an Option-B rollup."""

    def test_distinct_set_preserves_recency_order(self):
        users = (
            _user("amitb", client_ip="47.203.164.8"),
            _user("MotWakorb", client_ip="172.16.0.2"),
        )
        assert rollup_client_ips(users) == ["47.203.164.8", "172.16.0.2"]

    def test_dedupes_repeated_ips(self):
        users = (
            _user("a", client_ip="1.1.1.1"),
            _user("b", client_ip="1.1.1.1"),
            _user("c", client_ip="2.2.2.2"),
        )
        assert rollup_client_ips(users) == ["1.1.1.1", "2.2.2.2"]

    def test_skips_missing_ips(self):
        users = (
            _user("a", client_ip=None),
            _user("b", client_ip="2.2.2.2"),
        )
        assert rollup_client_ips(users) == ["2.2.2.2"]

    def test_empty_when_no_ips(self):
        users = (_user("a", client_ip=None), _user("b", client_ip=None))
        assert rollup_client_ips(users) == []

    def test_option_b_rollup_group_carries_ip_set(self):
        # 2 indistinguishable direct connections + 2 users → Option-B rollup.
        conns = [
            _conn("c1", ip="10.0.0.5", connected_at=100.0),
            _conn("c2", ip="10.0.0.5", connected_at=100.0),
        ]
        users = [
            _user("amitb", user_id="u1", client_ip="47.203.164.8"),
            _user("MotWakorb", user_id="u2", client_ip="172.16.0.2"),
        ]
        result = reconcile_channel(conns, users)
        for a in result.assignments:
            assert a.is_rollup is True
            assert rollup_client_ips(a.rollup_users) == [
                "47.203.164.8",
                "172.16.0.2",
            ]
