"""y3m6o.1 review BLOCK — prove the channel-profile membership CONTRACT the
diff-only-writes optimization rests on, using REAL recorded Dispatcharr data.

The optimization builds a ``channel_id -> {enabled profile_ids}`` map from
``get_channel_profiles()[*].channels`` and treats a channel ABSENT from a
profile's ``channels`` as NOT enabled there (so an idempotent reconcile skips
the no-op write). If ``channels`` ever OMITTED an actually-enabled/auto-joined
membership, an existing channel's unselected profiles would NOT be disabled —
the exact GH #720 over-inclusion regression. Mocked-shape unit tests cannot
catch a wrong premise; only a REAL fixture can.

The fixture ``dispatcharr_channel_profiles_mixed_membership.json`` is the live
``get_channel_profiles()`` response captured after explicitly enabling one probe
channel in some profiles and DISABLING it in others (a mixed membership,
including an enable-after-disable flip) on a real v0.27.x instance. These tests
assert the map built from that real response reports the probe channel as a
member of EXACTLY the enabled profiles and NONE of the disabled ones — the
"complete-enabled-only membership" contract.
"""
import json
from pathlib import Path

import pytest

from channel_pipeline_engine import build_channel_profile_membership

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures" / "y3m6o1"
    / "dispatcharr_channel_profiles_mixed_membership.json"
)


@pytest.fixture(scope="module")
def recorded():
    return json.loads(FIXTURE.read_text())


def test_fixture_records_a_genuinely_mixed_membership(recorded):
    """Guard the fixture itself: it must exercise BOTH enabled and disabled
    memberships on the probe channel, or it proves nothing."""
    assert recorded["enabled_profile_ids"], "fixture has no enabled profiles"
    assert recorded["disabled_profile_ids"], "fixture has no disabled profiles"
    # No overlap between the enabled and disabled partitions.
    assert not (
        set(recorded["enabled_profile_ids"])
        & set(recorded["disabled_profile_ids"])
    )


def test_map_reports_complete_enabled_only_membership(recorded):
    """The map built from the REAL response reports the probe channel as a
    member of EXACTLY the profiles it was ENABLED in — no disabled profile
    leaks in (that would be the #720 over-inclusion premise failure)."""
    probe = recorded["probe_channel_id"]
    enabled = set(recorded["enabled_profile_ids"])
    disabled = set(recorded["disabled_profile_ids"])

    membership = build_channel_profile_membership(recorded["profiles"])

    assert membership.get(probe, set()) == enabled, (
        "channel-profile membership CONTRACT VIOLATED: get_channel_profiles() "
        "channels no longer enumerates exactly the enabled memberships — the "
        "diff-only-writes optimization would recreate GH #720. See BLOCK."
    )
    # Explicit both-directions assertions for a loud failure message.
    assert membership.get(probe, set()).isdisjoint(disabled), (
        "a DISABLED profile still lists the channel in its `channels` — "
        "absence-means-disabled premise is false"
    )
    for pid in enabled:
        assert pid in membership.get(probe, set()), (
            f"an ENABLED profile {pid} is missing the channel from `channels`"
        )


def test_disabled_profiles_do_not_list_the_probe_channel(recorded):
    """Directly against the raw recorded profiles: each DISABLED profile's
    `channels` list must NOT contain the probe channel."""
    probe = recorded["probe_channel_id"]
    by_id = {p["id"]: set(p.get("channels") or []) for p in recorded["profiles"]}
    for pid in recorded["disabled_profile_ids"]:
        assert probe not in by_id.get(pid, set()), (
            f"probe channel {probe} appears in DISABLED profile {pid}'s channels"
        )
    for pid in recorded["enabled_profile_ids"]:
        assert probe in by_id.get(pid, set()), (
            f"probe channel {probe} missing from ENABLED profile {pid}'s channels"
        )
