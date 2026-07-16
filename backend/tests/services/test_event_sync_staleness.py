"""
Stream-name staleness signal for Event Sync (bead enhancedchannelmanager-jqwfq).

``services.event_sync_staleness`` answers "was this exact stream name already
present before local midnight today?" from ``M3USnapshot.groups_data``. These
tests pin the fail-open contract the demote rail depends on:

* positive membership is the ONLY definitive (``True``) answer;
* absence from a CAPPED group list is inconclusive (``None``) — the cap can
  only cause missed detections, never false demotes;
* no qualifying snapshot / unknown provider / uncaptured group → ``None``;
* the timezone boundary is local midnight in ``DEFAULT_EVENT_TIMEZONE``
  (America/New_York), converted to the snapshots' naive-UTC convention;
* a REAL captured ``groups_data`` blob (container journal.db, account with a
  500-capped "USA | Flo Sports" group — the exact field-reported provider
  class) exercises the cap fail-open path against real data shape.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
import pytz

from models import M3USnapshot
from services.event_sync_matcher import DEFAULT_EVENT_TIMEZONE
from services.event_sync_staleness import (
    SNAPSHOT_MAX_STREAM_NAMES,
    local_midnight_utc,
    name_seen_before_today,
    previous_day_names,
)

TZ = pytz.timezone(DEFAULT_EVENT_TIMEZONE)

# 2026-07-16 12:00 local (EDT, UTC-4) → local midnight = 2026-07-16 04:00 UTC.
LOCAL_NOON = TZ.localize(datetime(2026, 7, 16, 12, 0, 0))
MIDNIGHT_UTC = datetime(2026, 7, 16, 4, 0, 0)

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures" / "event_sync" / "m3u_snapshot_groups_data_capped.json"
)


def _snapshot(session, account_id: int, when: datetime, groups: list[dict]):
    row = M3USnapshot(
        m3u_account_id=account_id,
        snapshot_time=when,
        total_streams=sum(g.get("stream_count", 0) for g in groups),
    )
    row.set_groups_data({"groups": groups})
    session.add(row)
    session.commit()
    return row


def _group(name: str, names: list[str]) -> dict:
    return {
        "name": name,
        "stream_count": len(names),
        "is_stale": False,
        "stream_names": names,
    }


class TestLocalMidnightUtc:
    def test_edt_noon_maps_to_4am_utc(self):
        assert local_midnight_utc(LOCAL_NOON) == MIDNIGHT_UTC

    def test_est_winter_maps_to_5am_utc(self):
        winter_noon = TZ.localize(datetime(2026, 1, 15, 12, 0, 0))
        assert local_midnight_utc(winter_noon) == datetime(2026, 1, 15, 5, 0)

    def test_result_is_naive(self):
        assert local_midnight_utc(LOCAL_NOON).tzinfo is None


class TestPreviousDayNames:
    def test_latest_qualifying_snapshot_wins(self, test_session):
        # Older pre-midnight snapshot has OLD-NAME; the latest pre-midnight
        # one has NEW-NAME — membership must come from the latest.
        _snapshot(test_session, 7, datetime(2026, 7, 14, 12, 0),
                  [_group("Sports", ["OLD-NAME"])])
        _snapshot(test_session, 7, datetime(2026, 7, 16, 2, 30),
                  [_group("Sports", ["NEW-NAME"])])
        lookup = previous_day_names([7], MIDNIGHT_UTC, db=test_session)
        assert lookup[7]["Sports"] == frozenset({"NEW-NAME"})

    def test_post_midnight_snapshot_does_not_qualify(self, test_session):
        # 00:10 local today = 04:10 UTC — AFTER the boundary: today's own
        # capture must never mark today's names as "seen before today".
        _snapshot(test_session, 7, datetime(2026, 7, 16, 4, 10),
                  [_group("Sports", ["TODAY-NAME"])])
        assert previous_day_names([7], MIDNIGHT_UTC, db=test_session) == {}

    def test_2350_local_yesterday_qualifies(self, test_session):
        # 23:50 local yesterday = 03:50 UTC today — BEFORE the boundary.
        _snapshot(test_session, 7, datetime(2026, 7, 16, 3, 50),
                  [_group("Sports", ["LATE-NAME"])])
        lookup = previous_day_names([7], MIDNIGHT_UTC, db=test_session)
        assert lookup[7]["Sports"] == frozenset({"LATE-NAME"})

    def test_accounts_are_isolated(self, test_session):
        _snapshot(test_session, 7, datetime(2026, 7, 16, 2, 0),
                  [_group("Sports", ["A7"])])
        _snapshot(test_session, 8, datetime(2026, 7, 16, 2, 0),
                  [_group("Sports", ["A8"])])
        lookup = previous_day_names([7, 8], MIDNIGHT_UTC, db=test_session)
        assert lookup[7]["Sports"] == frozenset({"A7"})
        assert lookup[8]["Sports"] == frozenset({"A8"})

    def test_groups_without_stream_names_are_omitted(self, test_session):
        # Disabled/legacy groups carry counts but no stream_names list —
        # they must map to "unknown", not an empty definitive set.
        _snapshot(test_session, 7, datetime(2026, 7, 16, 2, 0), [
            {"name": "NoNames", "stream_count": 12, "is_stale": False},
            _group("Sports", ["X"]),
        ])
        lookup = previous_day_names([7], MIDNIGHT_UTC, db=test_session)
        assert "NoNames" not in lookup[7]
        assert "Sports" in lookup[7]

    def test_corrupt_groups_data_is_unknown(self, test_session):
        row = M3USnapshot(
            m3u_account_id=7,
            snapshot_time=datetime(2026, 7, 16, 2, 0),
            total_streams=0,
            groups_data="{not json",
        )
        test_session.add(row)
        test_session.commit()
        assert previous_day_names([7], MIDNIGHT_UTC, db=test_session) == {}

    def test_empty_account_list_reads_nothing(self, test_session):
        assert previous_day_names([], MIDNIGHT_UTC, db=test_session) == {}


class TestNameSeenBeforeToday:
    LOOKUP = {
        7: {
            "Sports": frozenset({"Boxing 01 : Fury vs. Usyk 6PM"}),
            "Capped": frozenset(
                f"Slot {i}" for i in range(SNAPSHOT_MAX_STREAM_NAMES)
            ),
        },
    }

    def test_membership_hit_is_definitive(self):
        assert name_seen_before_today(
            self.LOOKUP, 7, "Sports", "Boxing 01 : Fury vs. Usyk 6PM",
        ) is True

    def test_absence_from_uncapped_group_is_fresh(self):
        assert name_seen_before_today(
            self.LOOKUP, 7, "Sports", "Boxing 01 : Canelo vs. Crawford 8PM",
        ) is False

    def test_absence_from_capped_group_is_unknown(self):
        # FAIL OPEN: a list at the cap proves nothing about names beyond it.
        assert name_seen_before_today(
            self.LOOKUP, 7, "Capped", "Slot 9999",
        ) is None

    def test_membership_in_capped_group_still_definitive(self):
        assert name_seen_before_today(
            self.LOOKUP, 7, "Capped", "Slot 0",
        ) is True

    def test_unknown_provider_is_unknown(self):
        assert name_seen_before_today(self.LOOKUP, None, "Sports", "X") is None

    def test_account_without_snapshot_is_unknown(self):
        assert name_seen_before_today(self.LOOKUP, 99, "Sports", "X") is None

    def test_uncaptured_group_is_unknown(self):
        assert name_seen_before_today(self.LOOKUP, 7, "Disabled", "X") is None

    def test_missing_group_name_is_unknown(self):
        assert name_seen_before_today(self.LOOKUP, 7, None, "X") is None


class TestCapConstantParity:
    def test_mirrors_snapshot_writer_cap(self):
        # SNAPSHOT_MAX_STREAM_NAMES is deliberately NOT imported from
        # tasks.m3u_refresh (module import registers the task); this pin
        # keeps the two constants from drifting silently.
        from tasks.m3u_refresh import MAX_STREAM_NAMES
        assert SNAPSHOT_MAX_STREAM_NAMES == MAX_STREAM_NAMES


class TestRealFixture:
    """Recorded ``groups_data`` blob from a live journal.db (account 17,
    snapshot 197, 2026-07-16 02:34 UTC), truncated to three groups: two
    small uncapped groups and the REAL 500-capped "USA | Flo Sports" group
    — the exact provider class from the field report (dateless "NO EVENT" /
    event slot names)."""

    @pytest.fixture()
    def seeded(self, test_session):
        groups = json.loads(FIXTURE.read_text())["groups"]
        _snapshot(test_session, 17, datetime(2026, 7, 16, 2, 34), groups)
        return previous_day_names([17], MIDNIGHT_UTC, db=test_session)

    def test_capped_group_is_at_the_cap(self, seeded):
        assert len(seeded[17]["USA | Flo Sports"]) == SNAPSHOT_MAX_STREAM_NAMES

    def test_real_member_name_is_stale_suspect(self, seeded):
        assert name_seen_before_today(
            seeded, 17, "USA | Flo Sports", "Flo Sports 99: NO EVENT",
        ) is True

    def test_absent_name_in_capped_group_fails_open(self, seeded):
        assert name_seen_before_today(
            seeded, 17, "USA | Flo Sports",
            "Flo Sports 1234: Definitely Not Captured 9PM",
        ) is None

    def test_absent_name_in_uncapped_group_reads_fresh(self, seeded):
        groups = json.loads(FIXTURE.read_text())["groups"]
        uncapped = next(
            g["name"] for g in groups
            if len(g["stream_names"]) < SNAPSHOT_MAX_STREAM_NAMES
        )
        assert name_seen_before_today(
            seeded, 17, uncapped, "A name the provider never listed",
        ) is False
