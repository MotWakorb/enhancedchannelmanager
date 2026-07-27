"""
Unit tests for the built-in tag seed sync (database._populate_builtin_tags).

Covers the Quality Tags UHD extension (bead enhancedchannelmanager-lecyo):
fresh installs must seed modern UHD labels (2160P, 3840P, 4320P, 8K), and
the startup top-up sync must skip -- not duplicate or mutate -- tags a user
already added via the tag-group API (the live-instance scenario where 3840P
and 2160P exist with is_builtin=0).
"""
from sqlalchemy import text

from database import _populate_builtin_tags

# The pre-lecyo seed contents, kept verbatim so the tests catch accidental
# removals as well as verifying the UHD additions.
LEGACY_QUALITY_TAGS = [
    "HD", "FHD", "UHD", "4K", "SD",
    "1080P", "1080I", "720P", "480P",
    "HEVC", "H264", "H265",
]
UHD_QUALITY_TAGS = ["2160P", "3840P", "4320P", "8K"]


def _quality_tag_rows(conn):
    """Return {value: (case_sensitive, enabled, is_builtin)} for Quality Tags."""
    rows = conn.execute(text(
        "SELECT value, case_sensitive, enabled, is_builtin FROM tags "
        "WHERE group_id = (SELECT id FROM tag_groups WHERE name = 'Quality Tags')"
    )).fetchall()
    return {row[0]: (row[1], row[2], row[3]) for row in rows}


class TestQualityTagsSeed:
    def test_fresh_db_seeds_legacy_and_uhd_quality_tags(self, test_engine):
        with test_engine.connect() as conn:
            _populate_builtin_tags(conn)
            tags = _quality_tag_rows(conn)

        for value in LEGACY_QUALITY_TAGS + UHD_QUALITY_TAGS:
            assert value in tags, f"missing seeded quality tag {value}"
            case_sensitive, enabled, is_builtin = tags[value]
            assert case_sensitive == 0
            assert enabled == 1
            assert is_builtin == 1

    def test_seed_contains_no_duplicate_values(self, test_engine):
        with test_engine.connect() as conn:
            _populate_builtin_tags(conn)
            rows = conn.execute(text(
                "SELECT value, COUNT(*) FROM tags "
                "WHERE group_id = (SELECT id FROM tag_groups WHERE name = 'Quality Tags') "
                "GROUP BY value HAVING COUNT(*) > 1"
            )).fetchall()
        assert rows == []

    def test_rerun_is_idempotent(self, test_engine):
        with test_engine.connect() as conn:
            _populate_builtin_tags(conn)
            first = _quality_tag_rows(conn)
            _populate_builtin_tags(conn)
            second = _quality_tag_rows(conn)
        assert first == second

    def test_topup_skips_existing_user_added_tags(self, test_engine):
        """Mirror of the live DB: 3840P/2160P were added via the tag-group API
        (is_builtin=0) before the seed knew about them. The startup sync must
        leave those rows untouched (no duplicate, is_builtin stays 0) while
        still topping up the genuinely missing UHD tags as built-ins."""
        with test_engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO tag_groups (name, description, is_builtin, created_at, updated_at) "
                "VALUES ('Quality Tags', 'Video quality indicators (HD, 4K, etc.)', 1, "
                "datetime('now'), datetime('now'))"
            ))
            group_id = conn.execute(text(
                "SELECT id FROM tag_groups WHERE name = 'Quality Tags'"
            )).fetchone()[0]
            for value in LEGACY_QUALITY_TAGS:
                conn.execute(text(
                    "INSERT INTO tags (group_id, value, case_sensitive, enabled, is_builtin) "
                    "VALUES (:group_id, :value, 0, 1, 1)"
                ), {"group_id": group_id, "value": value})
            # User-added via API before the seed caught up
            for value in ("3840P", "2160P"):
                conn.execute(text(
                    "INSERT INTO tags (group_id, value, case_sensitive, enabled, is_builtin) "
                    "VALUES (:group_id, :value, 0, 1, 0)"
                ), {"group_id": group_id, "value": value})
            conn.commit()

            _populate_builtin_tags(conn)
            tags = _quality_tag_rows(conn)
            duplicate_rows = conn.execute(text(
                "SELECT value, COUNT(*) FROM tags WHERE group_id = :group_id "
                "GROUP BY value HAVING COUNT(*) > 1"
            ), {"group_id": group_id}).fetchall()

        assert duplicate_rows == []
        # Pre-existing user-added rows are untouched
        assert tags["3840P"] == (0, 1, 0)
        assert tags["2160P"] == (0, 1, 0)
        # Genuinely missing UHD tags are topped up as built-ins
        assert tags["4320P"] == (0, 1, 1)
        assert tags["8K"] == (0, 1, 1)
