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

import pytest

from services.attribution_reconciler import (
    AMBIGUOUS_GROUP_PREDICATE,
    CandidateUser,
    Connection,
    build_trusted_networks,
    distinct_users,
    eligible_connections,
    ip_priority,
    reconcile_channel,
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
    server_proxy: bool = False,
) -> Connection:
    return Connection(
        client_id=client_id,
        ip_address=ip,
        connected_at=connected_at,
        has_url_identity=url_identity,
        is_server_proxy=server_proxy,
    )


def _user(
    name: str,
    *,
    user_id: str | None = None,
    last_activity=None,
    source: str = "jellyfin",
) -> CandidateUser:
    return CandidateUser(
        user_name=name,
        user_id=user_id,
        last_activity_date=last_activity,
        source=source,
    )


def _assigned_names(result) -> dict[str, str | None]:
    """Map client_id → assigned single user_name (None if User #0 or rollup)."""
    return {
        a.client_id: (a.user.user_name if a.user else None)
        for a in result.assignments
    }


# ---------------------------------------------------------------------------
# Eligibility — the URL-identity discriminator that replaces the IP gate
# ---------------------------------------------------------------------------


class TestUrlIdentityDiscriminator:
    """The discriminator that replaces the IP gate (brief test #5)."""

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
        """Connections with an embedded URL username are not reconciled."""
        conns = [
            _conn("c1", ip="172.18.0.1"),
            _conn("c2", ip="10.0.0.5", url_identity=True),
        ]
        eligible = eligible_connections(conns)
        assert [c.client_id for c in eligible] == ["c1"]

    def test_url_identity_connection_never_gets_a_user(self):
        """A direct-IPTV client is excluded even when users are available."""
        conns = [_conn("xc1", ip="10.0.0.5", url_identity=True)]
        users = [_user("alice")]
        result = reconcile_channel(conns, users)
        # No eligible connections → no assignments; the user surfaces only
        # at channel level.
        assert result.assignments == ()
        assert [u.user_name for u in result.channel_viewers] == ["alice"]


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
