"""Cross-language parity gate for the frontend's SHIPPED Event Sync patterns.

The Event Sync rule editor ships a small set of default parse patterns
(bead ti939.1.5) defined in
``frontend/src/components/channelPipeline/eventSyncShippedPatterns.json`` —
the single source of truth consumed by ``eventSyncDefaults.ts``. These
regexes execute in the BACKEND matcher (``parse_event_name`` via
``extract_groups`` → ``safe_regex``), so their correctness can only be
proven here, against the real parser.

This suite exists because of the PR #614 review blocker: the original
generic no-"@" variants let the slot-prefix branch (``\\d{2}\\s*:``) consume
through the TIME's own hour-colon, so "Yankees vs Red Sox 11 Jul 06:00 PM
ET" parsed to title ``'00 PM ET'`` — and every same-minute event collapsed
to the SAME garbage title, scoring 1.0 ATTACH across different fixtures
(the 1,341-incident shape). Pinning every shipped example against the real
matcher makes that class of bug fail CI instead of shipping silently, and
pins the "verbatim copy of DEFAULT_EVENT_PATTERNS" claim permanently.
"""
import json
from datetime import datetime
from pathlib import Path

import pytest

from services.event_sync_matcher import DEFAULT_EVENT_PATTERNS, parse_event_name

# backend/tests/services/... -> parents[2] = backend/, parents[3] = repo root.
_FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "frontend" / "src" / "components" / "channelPipeline"
    / "eventSyncShippedPatterns.json"
)


def _load_fixture() -> dict:
    # A missing fixture is a hard failure, not a skip — the gate exists to
    # prove the shipped frontend patterns against the real matcher.
    assert _FIXTURE_PATH.is_file(), (
        f"shipped-pattern fixture not found at {_FIXTURE_PATH} — if the file "
        f"moved, update this test AND eventSyncDefaults.ts together"
    )
    with open(_FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


_FIXTURE = _load_fixture()
_PATTERNS = _FIXTURE["patterns"]
_NOW = datetime.fromisoformat(_FIXTURE["pinned_now"])
_TZ = _FIXTURE["event_timezone"]


def _pattern_dict(entry: dict) -> dict:
    return {
        "name": entry["id"],
        "title_pattern": entry["title_pattern"],
        "time_pattern": entry["time_pattern"],
        "date_pattern": entry["date_pattern"],
    }


def test_builtin_entries_are_verbatim_copies_of_matcher_defaults():
    """builtin: true fixture entries == DEFAULT_EVENT_PATTERNS, exactly.

    The editor omits the ``patterns`` key from saved configs when the
    selection is exactly the built-ins, so the backend defaults apply — that
    only stays honest while the strings are byte-identical.
    """
    builtins = {e["id"]: e for e in _PATTERNS if e["builtin"]}
    defaults = {p["name"]: p for p in DEFAULT_EVENT_PATTERNS}
    assert set(builtins) == set(defaults), (
        "builtin fixture ids must match DEFAULT_EVENT_PATTERNS names"
    )
    for name, default in defaults.items():
        entry = builtins[name]
        assert entry["title_pattern"] == default["title_pattern"], name
        assert entry["time_pattern"] == default["time_pattern"], name
        assert entry["date_pattern"] == default["date_pattern"], name


@pytest.mark.parametrize(
    "entry", _PATTERNS, ids=[e["id"] for e in _PATTERNS]
)
def test_shipped_example_parses_to_expected_title_and_start(entry):
    """Every shipped pattern parses ITS OWN example string correctly.

    Runs the example through the real ``parse_event_name`` with the
    fixture's pinned ``now`` (deterministic year inference) and asserts the
    exact title and tz-aware start the UI promises the operator.
    """
    parsed = parse_event_name(
        entry["example"],
        [_pattern_dict(entry)],
        event_timezone=_TZ,
        now=_NOW,
    )
    assert parsed.title == entry["expected_title"], (
        f"{entry['id']}: example {entry['example']!r} parsed to title "
        f"{parsed.title!r}, expected {entry['expected_title']!r}"
    )
    assert parsed.start is not None, (
        f"{entry['id']}: example {entry['example']!r} produced no start — "
        f"the shipped pattern cannot parse its own example"
    )
    assert parsed.start == datetime.fromisoformat(entry["expected_start"]), (
        f"{entry['id']}: parsed start {parsed.start.isoformat()} != "
        f"expected {entry['expected_start']}"
    )


@pytest.mark.parametrize(
    "entry",
    [e for e in _PATTERNS if not e["builtin"]],
    ids=[e["id"] for e in _PATTERNS if not e["builtin"]],
)
def test_generic_patterns_keep_same_minute_events_distinct(entry):
    """Regression for the PR #614 blocker: no time-colon title collapse.

    The broken generic title regex reduced every same-minute event to one
    garbage suffix title ('00 PM ET'), which then fuzzy-scored 1.0 ATTACH
    across different fixtures. Two different same-minute events must parse
    to their own distinct team titles.
    """
    pattern = _pattern_dict(entry)
    if entry["id"] == "title-day-first-date-no-at":
        name_a = "Yankees vs Red Sox 11 Jul 06:00 PM ET"
        name_b = "Lakers vs Celtics 11 Jul 06:00 PM ET"
        title_a, title_b = "Yankees vs Red Sox", "Lakers vs Celtics"
    else:
        name_a = "Lyon vs Marseille Jan 17 02:45 PM ET"
        name_b = "Ajax vs Feyenoord Jan 17 02:45 PM ET"
        title_a, title_b = "Lyon vs Marseille", "Ajax vs Feyenoord"

    parsed_a = parse_event_name(name_a, [pattern], event_timezone=_TZ, now=_NOW)
    parsed_b = parse_event_name(name_b, [pattern], event_timezone=_TZ, now=_NOW)
    assert parsed_a.title == title_a
    assert parsed_b.title == title_b
    assert parsed_a.title != parsed_b.title
    # Both sides carry team splits — the token rail stays engaged.
    assert parsed_a.teams is not None
    assert parsed_b.teams is not None


def test_generic_patterns_strip_two_digit_slot_prefixes():
    """The slot-prefix branch still works where it should (letter follows).

    The blocker fix adds a ``(?!\\d)`` lookahead so the prefix cannot
    terminate at a time's hour-colon; a real "NN :" slot prefix followed by
    a title must still be stripped.
    """
    entry = next(e for e in _PATTERNS if e["id"] == "title-day-first-date-no-at")
    parsed = parse_event_name(
        "Fubo Sports Network 07 : Yankees vs Red Sox 11 Jul 06:00 PM ET",
        [_pattern_dict(entry)],
        event_timezone=_TZ,
        now=_NOW,
    )
    assert parsed.title == "Yankees vs Red Sox"
    assert parsed.start is not None
