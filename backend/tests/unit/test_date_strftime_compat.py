"""Same-source glibc date-format observations must survive the musl migration."""

import json
import os
import time
from datetime import datetime
from pathlib import Path

import date_placeholders
import pytest

FIXTURE = Path(__file__).parents[1] / "fixtures/alpine/strftime-baseline.json"


@pytest.fixture
def timezone():
    original = os.environ.get("TZ")

    def set_zone(zone):
        os.environ["TZ"] = zone
        time.tzset()

    yield set_zone
    if original is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = original
    time.tzset()


def test_portable_formatter_matches_recorded_baseline_directives_flags_and_literals(
    monkeypatch, timezone
):
    fixture = json.loads(FIXTURE.read_text())
    monkeypatch.setattr(date_placeholders, "_GNU_STRFTIME", False)
    for vector in fixture["vectors"]:
        timezone(vector["tz"])
        value = datetime.fromisoformat(vector["date"])
        for fmt, expected in zip(fixture["formats"], vector["expected"], strict=True):
            assert date_placeholders._format_date(value, fmt) == expected, (
                vector["tz"],
                vector["date"],
                fmt,
            )


@pytest.mark.parametrize("ranges", [True, False])
def test_unknown_directive_cannot_disappear_from_condition_patterns(
    monkeypatch, ranges
):
    monkeypatch.setattr(date_placeholders, "_GNU_STRFTIME", False)
    assert date_placeholders.expand_date_placeholders("{date:%Q}", ranges) == "%Q"
    assert (
        date_placeholders.expand_date_placeholders("{today:prefix-%Q-suffix}", ranges)
        == "prefix-%Q-suffix"
    )
    assert date_placeholders.expand_date_placeholders("{date:%%Q}", ranges) == "%Q"
    assert date_placeholders.expand_date_placeholders("{date:%%%Q}", ranges) == "%%Q"


def test_ranges_use_the_same_compatibility_formatter(monkeypatch):
    monkeypatch.setattr(date_placeholders, "_GNU_STRFTIME", False)
    assert date_placeholders.expand_date_placeholders("{date+2:%Q}") == "(%Q|%Q|%Q)"
    assert date_placeholders.expand_date_placeholders("{date-1:%Q}") == "(%Q|%Q)"


def test_legitimate_empty_timezone_output_is_not_treated_as_an_invalid_format(
    monkeypatch,
):
    monkeypatch.setattr(date_placeholders, "_GNU_STRFTIME", False)
    value = datetime(2026, 11, 8)
    for fmt in ["", "%z", "%Z"]:
        assert date_placeholders._format_date(value, fmt) == ""


def test_save_gates_and_real_evaluator_preserve_literal_unknown_directives(monkeypatch):
    from channel_pipeline_evaluator import ConditionEvaluator, StreamContext
    from channel_pipeline_schema import Condition
    from regex_lint import lint_conditions_json

    monkeypatch.setattr(date_placeholders, "_GNU_STRFTIME", False)
    condition = Condition(type="stream_name_matches", value="prefix-{date:%Q}")
    assert condition.validate() == []
    assert (
        lint_conditions_json(
            [{"type": "stream_name_matches", "value": condition.value}]
        )
        == []
    )
    evaluator = ConditionEvaluator()
    assert evaluator.evaluate(condition, StreamContext(1, "prefix-%Q")).matched
    assert not evaluator.evaluate(condition, StreamContext(2, "prefix-")).matched
