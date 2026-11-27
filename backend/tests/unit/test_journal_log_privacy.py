"""Journal log lines carry identifiers only, never row content.

An entity name, description or before/after value is operator or upstream data
and may embed a credential; the journal row keeps it, the log does not. A
database error's text repeats the bound parameters, so failures log the
exception type rather than the exception.
"""
import logging
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

NAME = "journal-entity-name-unregistered-5c0e"
DESCRIPTION = "journal-description-unregistered-91aa"
VALUE = "journal-value-unregistered-d2f7"


def row():
    return {
        "category": "channel",
        "action_type": "update",
        "entity_id": 42,
        "entity_name": NAME,
        "description": DESCRIPTION,
        "before_value": {"name": VALUE},
        "after_value": {"name": VALUE + "-after"},
    }


def assert_no_row_content(caplog):
    text = caplog.text
    assert "[JOURNAL]" in text, "the path under test must log"
    for value in (NAME, DESCRIPTION, VALUE):
        assert value not in text


def db_error():
    return OperationalError(
        "INSERT INTO journal_entries (entity_name, description) VALUES (?, ?)",
        (NAME, DESCRIPTION + VALUE),
        Exception("database is locked"),
    )


def test_log_entry_debug_lines_name_ids_not_entity(test_session, caplog):
    from journal import log_entry

    with (
        patch("journal.get_session", return_value=test_session),
        caplog.at_level(logging.DEBUG, logger="journal"),
    ):
        entry = log_entry(**row())
    assert entry is not None and entry.entity_name == NAME
    assert_no_row_content(caplog)
    assert "category=channel action=update" in caplog.text
    assert "entity_id=42" in caplog.text


def test_log_entry_failure_logs_the_error_type_only(caplog):
    from journal import log_entry

    session = MagicMock()
    session.commit.side_effect = db_error()
    with (
        patch("journal.get_session", return_value=session),
        caplog.at_level(logging.DEBUG, logger="journal"),
    ):
        assert log_entry(**row()) is None
    assert_no_row_content(caplog)
    assert "OperationalError" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_log_entries_failure_logs_the_error_type_only(caplog):
    from journal import log_entries

    session = MagicMock()
    session.commit.side_effect = db_error()
    with (
        patch("journal.get_session", return_value=session),
        caplog.at_level(logging.DEBUG, logger="journal"),
    ):
        assert log_entries([row(), row()]) is False
    assert_no_row_content(caplog)
    assert "OperationalError" in caplog.text
    assert "count=2" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("batch", [False, True])
def test_successful_writes_still_log_identifiers(test_session, caplog, batch):
    from journal import log_entries, log_entry

    with (
        patch("journal.get_session", return_value=test_session),
        caplog.at_level(logging.DEBUG, logger="journal"),
    ):
        if batch:
            assert log_entries([row()]) is True
        else:
            assert log_entry(**row()) is not None
    assert_no_row_content(caplog)
