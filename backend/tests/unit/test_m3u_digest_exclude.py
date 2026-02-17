"""
Tests for M3U digest exclude pattern filtering.

Verifies that group and stream exclude regex patterns correctly filter
changes out of the digest before rendering/sending.
"""
import json
import re
import pytest

from models import M3UChangeLog, M3UDigestSettings


# ---------------------------------------------------------------------------
# _FilteredChange proxy
# ---------------------------------------------------------------------------

class TestFilteredChange:
    """Test the _FilteredChange proxy class."""

    def test_import(self):
        from tasks.m3u_digest import _FilteredChange
        assert _FilteredChange is not None

    def test_overrides_count_and_stream_names(self, test_session):
        """Proxy should return kept names and adjusted count."""
        from tasks.m3u_digest import _FilteredChange

        change = M3UChangeLog(
            m3u_account_id=1,
            change_type="streams_added",
            group_name="Sports",
            count=3,
        )
        change.set_stream_names(["ESPN HD", "PPV Fight Night", "Fox Sports"])
        test_session.add(change)
        test_session.commit()

        proxy = _FilteredChange(change, ["ESPN HD", "Fox Sports"])
        assert proxy.get_stream_names() == ["ESPN HD", "Fox Sports"]
        assert proxy.count == 2

    def test_delegates_other_attrs(self, test_session):
        """Proxy should delegate non-overridden attributes to the original."""
        from tasks.m3u_digest import _FilteredChange

        change = M3UChangeLog(
            m3u_account_id=42,
            change_type="streams_removed",
            group_name="Movies",
            count=5,
        )
        test_session.add(change)
        test_session.commit()

        proxy = _FilteredChange(change, ["Kept Stream"])
        assert proxy.m3u_account_id == 42
        assert proxy.change_type == "streams_removed"
        assert proxy.group_name == "Movies"


# ---------------------------------------------------------------------------
# M3UDigestSettings getter/setter for exclude patterns
# ---------------------------------------------------------------------------

class TestDigestSettingsExcludePatterns:
    """Test model getter/setter for exclude pattern fields."""

    def test_get_empty(self, test_session):
        settings = M3UDigestSettings(enabled=False, frequency="daily")
        test_session.add(settings)
        test_session.commit()

        assert settings.get_exclude_group_patterns() == []
        assert settings.get_exclude_stream_patterns() == []

    def test_set_and_get_group_patterns(self, test_session):
        settings = M3UDigestSettings(enabled=False, frequency="daily")
        test_session.add(settings)
        test_session.commit()

        settings.set_exclude_group_patterns(["ESPN\\+", "PPV.*"])
        test_session.commit()

        assert settings.get_exclude_group_patterns() == ["ESPN\\+", "PPV.*"]

    def test_set_and_get_stream_patterns(self, test_session):
        settings = M3UDigestSettings(enabled=False, frequency="daily")
        test_session.add(settings)
        test_session.commit()

        settings.set_exclude_stream_patterns(["League Pass", "UFC \\d+"])
        test_session.commit()

        assert settings.get_exclude_stream_patterns() == ["League Pass", "UFC \\d+"]

    def test_set_empty_list_clears(self, test_session):
        settings = M3UDigestSettings(enabled=False, frequency="daily")
        settings.set_exclude_group_patterns(["foo"])
        test_session.add(settings)
        test_session.commit()

        settings.set_exclude_group_patterns([])
        test_session.commit()

        assert settings.get_exclude_group_patterns() == []

    def test_to_dict_includes_exclude_patterns(self, test_session):
        settings = M3UDigestSettings(enabled=False, frequency="daily")
        settings.set_exclude_group_patterns(["abc"])
        settings.set_exclude_stream_patterns(["xyz"])
        test_session.add(settings)
        test_session.commit()

        d = settings.to_dict()
        assert d["exclude_group_patterns"] == ["abc"]
        assert d["exclude_stream_patterns"] == ["xyz"]


# ---------------------------------------------------------------------------
# Filtering logic (unit-level, exercising the regex matching)
# ---------------------------------------------------------------------------

class TestExcludeFilterLogic:
    """Test the exclude filtering logic extracted from the digest task.

    Rather than running the full async execute(), we replicate the filtering
    block from m3u_digest.py to test it in isolation.
    """

    @staticmethod
    def _apply_exclude_filters(changes, group_patterns_raw, stream_patterns_raw):
        """
        Replicate the filtering block from M3UDigestTask.execute() for testing.
        """
        from tasks.m3u_digest import _FilteredChange

        if not group_patterns_raw and not stream_patterns_raw:
            return changes

        group_regexes = []
        for p in group_patterns_raw:
            try:
                group_regexes.append(re.compile(p, re.IGNORECASE))
            except re.error:
                pass

        stream_regexes = []
        for p in stream_patterns_raw:
            try:
                stream_regexes.append(re.compile(p, re.IGNORECASE))
            except re.error:
                pass

        filtered = []
        for change in changes:
            if group_regexes and change.group_name:
                if any(rx.search(change.group_name) for rx in group_regexes):
                    continue

            if stream_regexes and change.change_type in ("streams_added", "streams_removed"):
                original_names = change.get_stream_names()
                if original_names:
                    kept = [n for n in original_names
                            if not any(rx.search(n) for rx in stream_regexes)]
                    if not kept:
                        continue
                    if len(kept) < len(original_names):
                        change = _FilteredChange(change, kept)

            filtered.append(change)
        return filtered

    def _make_change(self, session, change_type, group_name, stream_names=None, count=None):
        c = M3UChangeLog(
            m3u_account_id=1,
            change_type=change_type,
            group_name=group_name,
            count=count or (len(stream_names) if stream_names else 1),
        )
        if stream_names:
            c.set_stream_names(stream_names)
        session.add(c)
        session.commit()
        return c

    def test_group_exclude_drops_matching_group(self, test_session):
        """Changes whose group_name matches an exclude pattern are dropped."""
        c1 = self._make_change(test_session, "group_added", "ESPN+ Events")
        c2 = self._make_change(test_session, "group_added", "Sports HD")

        result = self._apply_exclude_filters([c1, c2], ["ESPN\\+"], [])
        assert len(result) == 1
        assert result[0].group_name == "Sports HD"

    def test_group_exclude_case_insensitive(self, test_session):
        """Group pattern matching is case-insensitive."""
        c1 = self._make_change(test_session, "group_removed", "espn+ events")

        result = self._apply_exclude_filters([c1], ["ESPN\\+"], [])
        assert len(result) == 0

    def test_group_exclude_partial_match(self, test_session):
        """Pattern can match anywhere in the group name (search, not fullmatch)."""
        c1 = self._make_change(test_session, "group_added", "US | PPV Boxing")

        result = self._apply_exclude_filters([c1], ["PPV"], [])
        assert len(result) == 0

    def test_stream_exclude_drops_all_matching(self, test_session):
        """When all streams in a change match, the entire change is dropped."""
        c1 = self._make_change(
            test_session, "streams_added", "Sports",
            stream_names=["PPV Fight 1", "PPV Fight 2"],
        )

        result = self._apply_exclude_filters([c1], [], ["PPV.*"])
        assert len(result) == 0

    def test_stream_exclude_partial_keeps_remaining(self, test_session):
        """When only some streams match, the change is kept with filtered names."""
        c1 = self._make_change(
            test_session, "streams_added", "Sports",
            stream_names=["PPV Fight 1", "ESPN HD", "PPV Fight 2"],
        )

        result = self._apply_exclude_filters([c1], [], ["PPV"])
        assert len(result) == 1
        assert result[0].get_stream_names() == ["ESPN HD"]
        assert result[0].count == 1

    def test_stream_exclude_case_insensitive(self, test_session):
        """Stream pattern matching is case-insensitive."""
        c1 = self._make_change(
            test_session, "streams_removed", "Events",
            stream_names=["ppv main event"],
        )

        result = self._apply_exclude_filters([c1], [], ["PPV"])
        assert len(result) == 0

    def test_both_group_and_stream_patterns(self, test_session):
        """Group and stream patterns can work simultaneously."""
        c1 = self._make_change(test_session, "group_added", "ESPN+ Specials")
        c2 = self._make_change(
            test_session, "streams_added", "Movies",
            stream_names=["PPV Blockbuster", "Regular Movie"],
        )
        c3 = self._make_change(test_session, "group_added", "News")

        result = self._apply_exclude_filters([c1, c2, c3], ["ESPN\\+"], ["PPV"])
        assert len(result) == 2
        # c1 dropped by group pattern
        # c2 kept but PPV stream filtered out
        assert result[0].group_name == "Movies"
        assert result[0].get_stream_names() == ["Regular Movie"]
        assert result[0].count == 1
        # c3 passes through
        assert result[1].group_name == "News"

    def test_no_patterns_passes_all(self, test_session):
        """Empty pattern lists should pass all changes through."""
        c1 = self._make_change(test_session, "group_added", "ESPN+")
        c2 = self._make_change(test_session, "streams_added", "PPV", stream_names=["PPV 1"])

        result = self._apply_exclude_filters([c1, c2], [], [])
        assert len(result) == 2

    def test_invalid_pattern_skipped(self, test_session):
        """Invalid regex patterns are skipped gracefully."""
        c1 = self._make_change(test_session, "group_added", "Sports")

        # [unclosed is invalid regex — should not crash, should not filter
        result = self._apply_exclude_filters([c1], ["[unclosed"], [])
        assert len(result) == 1

    def test_group_exclude_does_not_affect_stream_changes(self, test_session):
        """Group pattern on a streams_added change filters by group_name, not stream names."""
        c1 = self._make_change(
            test_session, "streams_added", "ESPN+ Live",
            stream_names=["Game 1", "Game 2"],
        )

        result = self._apply_exclude_filters([c1], ["ESPN\\+"], [])
        assert len(result) == 0  # Dropped because group_name matches

    def test_stream_exclude_does_not_affect_group_changes(self, test_session):
        """Stream patterns only apply to streams_added/streams_removed, not group changes."""
        c1 = self._make_change(test_session, "group_added", "PPV Events")

        result = self._apply_exclude_filters([c1], [], ["PPV"])
        assert len(result) == 1  # group_added is not filtered by stream patterns
