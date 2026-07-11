"""
E2E happy path for Event Sync (epic ti939, bead ti939.2.4).

Flow covered LIVE against the container at localhost:6100:

  configure  — create an event_sync rule with the shipped default patterns
               (POST /api/channel-pipeline/rules with event_sync_config)
  test       — the Test Patterns panel's endpoint (POST
  patterns     /api/dummy-epg/preview/batch) parses every shipped pattern's
               own example into the expected fields
  preview    — POST /api/channel-pipeline/event-sync-preview returns match
               cards + a summary whose disposition counts reconcile exactly
  manual run — POST /api/channel-pipeline/rules/{id}/run (triggered_by="api",
               one of the two triggers allowed for event_sync in Phase 1B)
               completes and writes an event_sync run-summary line into the
               execution log
  journal    — the rule's lifecycle is journaled; the run's event_sync
               attach provenance is queryable by category + batch_id
               (= execution id)

Honesty note — live vs mocked (recorded per the bead's acceptance criteria):
the live Dispatcharr instance backing this container has NO event group with
auto_channel_sync ON (and toggling Dispatcharr group settings is a hard
project rail ECM must never cross), so the master group here has ZERO
channels and a live run provably attaches nothing.  That makes the manual
run in this file an end-to-end proof of the manual-run path and of the
no-master behavior (0 attaches, 0 channels created, clean summary line) —
NOT of multi-provider stream attachment.  The attachment segment of the
happy path ("master channels show streams from >= 2 providers" + per-attach
journal provenance rows) is covered against a mocked Dispatcharr in
tests/unit/test_event_sync_lifecycle.py and
tests/unit/test_event_sync_attach_execution.py, and by the documented
pre-release manual script in docs/event_sync.md ("Pre-release manual
verification").

Safety rails baked into this file:
  * The master group is chosen ONLY from groups with channel_count == 0, so
    the execute-mode run cannot attach anything even in principle.
  * The preview endpoint is zero-write by design.
  * The test rule is deleted in fixture teardown (finalizer, runs on failure
    too).
  * Nothing here ever touches Dispatcharr group settings.
"""
import json
import time
from pathlib import Path

import pytest

PIPELINE = "/api/channel-pipeline"

# Single source of truth for the shipped patterns (frontend + backend pin
# test both consume this file; see its _comment header).
_SHIPPED_PATTERNS_JSON = (
    Path(__file__).resolve().parents[3]
    / "frontend/src/components/channelPipeline/eventSyncShippedPatterns.json"
)

# Secondary groups: enough streams to exercise the matcher, few enough that
# preview + run stay well under the client timeout.
_MAX_SECONDARY_STREAMS = 300
_RUN_POLL_TIMEOUT_S = 90
_RUN_POLL_INTERVAL_S = 1.0


def _group_stream_count(group: dict) -> int:
    return sum(
        acct.get("stream_count") or 0
        for acct in group.get("m3u_accounts", [])
    )


@pytest.fixture(scope="module")
def event_sync_groups(e2e_client):
    """Pick a safe master + secondary groups from the live instance.

    Master: a group with ZERO channels (on this instance every event group
    has auto_channel_sync OFF, so this always exists).  Zero master channels
    makes the execute-mode run attach-nothing by construction.
    Secondaries: up to 2 groups with 1..N streams, master excluded.
    """
    resp = e2e_client.get("/api/channel-groups")
    if resp.status_code != 200:
        pytest.skip(f"channel-groups unavailable: {resp.status_code}")
    groups = resp.json()
    if not isinstance(groups, list) or not groups:
        pytest.skip("no channel groups on live instance")

    secondaries = sorted(
        (
            g for g in groups
            if 0 < _group_stream_count(g) <= _MAX_SECONDARY_STREAMS
        ),
        key=_group_stream_count,
        reverse=True,
    )[:2]
    secondary_ids = {g["id"] for g in secondaries}

    master = next(
        (
            g for g in groups
            if (g.get("channel_count") or 0) == 0
            and g["id"] not in secondary_ids
        ),
        None,
    )
    if master is None or not secondaries:
        pytest.skip(
            "live instance lacks a zero-channel master candidate or "
            "secondary groups with streams"
        )
    return {"master": master, "secondaries": secondaries}


@pytest.fixture(scope="module")
def event_sync_rule(e2e_client, event_sync_groups):
    """Create the event_sync rule under test; ALWAYS delete it on teardown.

    Omits the ``patterns`` key so the backend's shipped defaults apply —
    the same payload the UI sends when the operator keeps the default
    pattern selection (EventSyncRuleEditor.buildConfig).
    """
    payload = {
        "name": "E2E event_sync happy path (ti939.2.4)",
        "description": (
            "Created by tests/e2e/test_event_sync.py — deleted on teardown"
        ),
        "enabled": True,
        # Placeholder condition/action: the engine ignores both for the
        # event_sync kind, but the rule schema requires one of each (same
        # convention as the frontend editor and the backend unit tests).
        "conditions": [{"type": "always"}],
        "actions": [{"type": "skip"}],
        "event_sync_config": {
            "master_group_id": event_sync_groups["master"]["id"],
            "secondary_group_ids": [
                g["id"] for g in event_sync_groups["secondaries"]
            ],
            # Blast-radius cap for the fixture-drift TOCTOU: the zero-channel
            # master check happens at fixture time, so if the group gained
            # channels before the execute-mode run fires (operator flips
            # auto-sync mid-test, concurrent rule, manually created channel),
            # the worst case is ONE journaled, rollback-reversible attach
            # instead of many. Zero effect on the intended zero-master
            # scenario. Belt to the pre-run re-assert in the manual-run test.
            "max_attach_per_run": 1,
        },
    }
    resp = e2e_client.post(f"{PIPELINE}/rules", json=payload)
    assert resp.status_code == 200, (
        f"rule create failed: {resp.status_code} {resp.text[:500]}"
    )
    rule = resp.json()
    try:
        yield rule
    finally:
        del_resp = e2e_client.delete(f"{PIPELINE}/rules/{rule['id']}")
        assert del_resp.status_code in (200, 204), (
            f"cleanup failed — test rule id={rule['id']} may still exist: "
            f"{del_resp.status_code} {del_resp.text[:300]}"
        )


def _poll_execution(e2e_client, execution_id: int) -> dict:
    """Poll the execution until it leaves pending/running (or time out)."""
    deadline = time.monotonic() + _RUN_POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        resp = e2e_client.get(
            f"{PIPELINE}/executions/{execution_id}",
            params={"include_log": True},
        )
        assert resp.status_code == 200, resp.text[:300]
        execution = resp.json()
        if execution["status"] not in ("pending", "running"):
            return execution
        time.sleep(_RUN_POLL_INTERVAL_S)
    pytest.fail(
        f"execution {execution_id} still "
        f"{execution['status']} after {_RUN_POLL_TIMEOUT_S}s"
    )


class TestEventSyncHappyPath:
    """configure → test patterns → preview → manual run → journal."""

    # -- configure ---------------------------------------------------------

    def test_configure_rule_persists_config_with_defaults_filled(
        self, e2e_client, event_sync_rule, event_sync_groups
    ):
        """The saved rule carries the config with schema defaults filled."""
        resp = e2e_client.get(f"{PIPELINE}/rules/{event_sync_rule['id']}")
        assert resp.status_code == 200
        rule = resp.json()

        config = rule["event_sync_config"]
        assert config["master_group_id"] == event_sync_groups["master"]["id"]
        assert config["secondary_group_ids"] == [
            g["id"] for g in event_sync_groups["secondaries"]
        ]
        # Save-time validation fills these defaults in place (ti939.1.3);
        # the attach threshold is hard-floored at 0.80 (PO decision #2).
        assert config["attach_threshold"] >= 0.80
        assert config["time_window_minutes"] > 0
        assert config["enabled"] is True
        # Shipped defaults in use: the stored contract is that the
        # ``patterns`` key is OMITTED entirely (the validator fills the other
        # defaults in place but never materializes patterns), so backend
        # improvements to DEFAULT_EVENT_PATTERNS flow through without
        # editing saved rules.
        assert "patterns" not in config
        # The fixture's blast-radius cap round-trips.
        assert config["max_attach_per_run"] == 1

    def test_configure_rule_create_is_journaled(
        self, e2e_client, event_sync_rule
    ):
        """Journal provenance starts at the rule's creation."""
        resp = e2e_client.get(
            "/api/journal",
            params={
                "category": "auto_creation",
                "search": event_sync_rule["name"],
            },
        )
        assert resp.status_code == 200
        entries = resp.json()["results"]
        assert any(
            e["entity_id"] == event_sync_rule["id"]
            and e["action_type"] == "create"
            for e in entries
        ), "rule creation not found in journal"

    # -- test patterns -----------------------------------------------------

    def test_shipped_patterns_parse_their_examples(self, e2e_client):
        """The Test Patterns panel endpoint extracts the expected fields.

        Mirrors EventSyncTestPatternsPanel.handleTest: one
        POST /api/dummy-epg/preview/batch per pattern.  Each shipped
        pattern's own example (from eventSyncShippedPatterns.json, the
        single source of truth) must yield the expected title plus date and
        time captures.
        """
        shipped = json.loads(_SHIPPED_PATTERNS_JSON.read_text())
        assert shipped["patterns"], "no shipped patterns found"

        for pattern in shipped["patterns"]:
            resp = e2e_client.post(
                "/api/dummy-epg/preview/batch",
                json={
                    "sample_names": [pattern["example"]],
                    "title_pattern": pattern["title_pattern"],
                    "time_pattern": pattern["time_pattern"],
                    "date_pattern": pattern["date_pattern"],
                },
            )
            assert resp.status_code == 200, (
                f"pattern {pattern['id']}: {resp.status_code} "
                f"{resp.text[:300]}"
            )
            (result,) = resp.json()
            assert result["matched"], f"pattern {pattern['id']} did not match"
            groups = result.get("groups") or {}
            assert groups.get("title") == pattern["expected_title"], (
                f"pattern {pattern['id']}: parsed title "
                f"{groups.get('title')!r} != {pattern['expected_title']!r}"
            )
            # Date + time captures present (the panel renders these as the
            # parsed start; exact tz math is pinned in
            # tests/services/test_event_sync_shipped_frontend_patterns.py).
            assert groups.get("day"), f"pattern {pattern['id']}: no day"
            assert groups.get("month"), f"pattern {pattern['id']}: no month"
            assert groups.get("hour"), f"pattern {pattern['id']}: no hour"
            assert groups.get("minute"), f"pattern {pattern['id']}: no minute"

    # -- preview -----------------------------------------------------------

    def test_preview_returns_match_cards_and_reconciled_summary(
        self, e2e_client, event_sync_rule
    ):
        """Preview (zero-write) returns per-stream cards + exact summary."""
        resp = e2e_client.post(
            f"{PIPELINE}/event-sync-preview",
            json={"rule_id": event_sync_rule["id"]},
        )
        assert resp.status_code == 200, resp.text[:500]
        preview = resp.json()

        # Pre-flight is advisory: on this instance the master group has
        # auto_channel_sync OFF, so ok=False with a teaching failure is the
        # EXPECTED live shape — the preview still runs (by design).
        assert "ok" in preview["preflight"]

        summary = preview["summary"]
        assert summary["secondary_streams"] > 0, (
            "secondary groups unexpectedly empty — group selection fixture "
            "should have prevented this"
        )
        # The four dispositions reconcile exactly with the stream total.
        assert (
            summary["would_attach"]
            + summary["ambiguous_skipped"]
            + summary["unmatched"]
            + summary["parse_failed"]
        ) == summary["secondary_streams"]

        # Match cards: one per secondary stream, each with a disposition.
        assert len(preview["streams"]) == summary["secondary_streams"]
        allowed = {"would_attach", "ambiguous", "unmatched", "parse_failed"}
        for card in preview["streams"]:
            assert card["disposition"] in allowed
            assert card["stream_name"]
            assert "candidates" in card

        # No masters on this instance → nothing may claim would_attach.
        assert summary["master_channels"] == 0
        assert summary["would_attach"] == 0

    # -- manual run --------------------------------------------------------

    def test_manual_run_completes_and_attaches_nothing_without_masters(
        self, e2e_client, event_sync_rule, event_sync_groups
    ):
        """The single-rule run API (triggered_by="api") executes the rule.

        With zero master channels (asserted at fixture time AND re-asserted
        immediately below, closing the fixture-drift TOCTOU) the
        execute-mode run is attach-nothing by construction: it must complete
        cleanly, create no channels, merge no streams, and write the
        event_sync run-summary line into the execution log.
        """
        # Pre-run re-assert: the zero-channel precondition was checked when
        # the fixture picked the group; re-check RIGHT before the only
        # mutating call so drift in between (operator toggles auto-sync,
        # concurrent rule, manual channel) skips loudly instead of running
        # against a changed world. The rule's max_attach_per_run=1 cap is
        # the belt if the group changes in the remaining instants.
        master_id = event_sync_groups["master"]["id"]
        groups_resp = e2e_client.get("/api/channel-groups")
        assert groups_resp.status_code == 200, groups_resp.text[:300]
        master_now = next(
            (g for g in groups_resp.json() if g["id"] == master_id), None
        )
        if master_now is None or (master_now.get("channel_count") or 0) != 0:
            pytest.skip(
                f"master group {master_id} no longer has zero channels "
                f"(now: {master_now and master_now.get('channel_count')}) — "
                "zero-master precondition drifted between fixture and run"
            )

        resp = e2e_client.post(
            f"{PIPELINE}/rules/{event_sync_rule['id']}/run"
        )
        assert resp.status_code == 202, resp.text[:300]
        body = resp.json()
        execution_id = body["execution_id"]
        assert body["rule_id"] == event_sync_rule["id"]

        execution = _poll_execution(e2e_client, execution_id)
        assert execution["status"] == "completed", (
            f"run failed: {execution.get('error_message')}"
        )
        assert execution["triggered_by"] == "api"
        assert execution["mode"] == "execute"
        # ECM never creates or deletes channels in event_sync — and with no
        # master channels there is nothing to merge onto either.
        assert execution["channels_created"] == 0
        assert execution["streams_merged"] == 0

        # The operator's one-line drift detector must be in the log.
        summary_entries = [
            action
            for entry in execution["execution_log"]
            for action in entry.get("actions_executed", [])
            if action.get("type") == "event_sync_summary"
        ]
        assert len(summary_entries) == 1
        assert summary_entries[0]["description"].startswith(
            "event_sync: 0 attached"
        )

        # Stash for the journal test (same class instance is NOT shared
        # across tests — use the class attribute).
        type(self)._execution_id = execution_id

    # -- journal -----------------------------------------------------------

    def test_journal_event_sync_provenance_is_queryable_by_run(
        self, e2e_client
    ):
        """Attach provenance is queryable by category + batch_id (= run id).

        With zero attaches (no masters live) the filtered result must be
        EMPTY — asserting the filter itself works and that the run wrote no
        spurious provenance.  The populated-provenance shape (score, band,
        provider, master channel id/name per attach) is covered against a
        mocked Dispatcharr in tests/unit/test_event_sync_attach_execution.py.
        """
        execution_id = getattr(type(self), "_execution_id", None)
        if execution_id is None:
            pytest.skip("manual-run test did not record an execution id")

        resp = e2e_client.get(
            "/api/journal",
            params={
                "category": "event_sync",
                "batch_id": str(execution_id),
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["results"] == [], (
            "a zero-master run must write no event_sync attach entries, "
            f"got: {body['results'][:3]}"
        )
