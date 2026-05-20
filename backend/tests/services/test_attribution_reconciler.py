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
) -> Connection:
    return Connection(
        client_id=client_id,
        ip_address=ip,
        connected_at=connected_at,
        has_url_identity=url_identity,
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
