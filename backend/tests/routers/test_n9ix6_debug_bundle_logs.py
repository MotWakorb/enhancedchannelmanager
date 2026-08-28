"""Rotating-log debug bundle coverage for bead enhancedchannelmanager-n9ix6."""

import json
import logging
import asyncio
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import log_utils
from observability import JsonFormatter, _TraceIdFilter
from tests.routers.test_d0hoc_debug_bundle_credential_leak import (
    XC_PASS,
    XC_USER,
    build_bundle,
    extract_members,
    scan_members,
)


def _json_line(index: int, *, message: str | None = None) -> str:
    timestamp = datetime(2026, 8, 28, 12) + timedelta(seconds=index)
    return json.dumps(
        {
            "ts": timestamp.isoformat(timespec="milliseconds") + "Z",
            "level": "INFO",
            "logger": "bundle.test",
            "msg": message or f"sequence-{index}",
            "trace_id": "-",
        },
        separators=(",", ":"),
    )


def _source(name: str, lines: list[str], *, complete: bool = True):
    data = ("\n".join(lines) + "\n").encode()
    return log_utils.PersistentLogSource(
        name=name,
        data=data,
        expected_bytes=len(data),
        complete=complete,
    )


def _snapshot(
    *sources,
    incomplete=False,
    reason=None,
    handler_degraded=False,
    rotation_saturated=False,
    byte_limit_reached=False,
):
    return log_utils.PersistentLogSnapshot(
        files=tuple(sources),
        incomplete=incomplete,
        reason=reason,
        handler_degraded=handler_degraded,
        rotation_saturated=rotation_saturated,
        byte_limit_reached=byte_limit_reached,
    )


def _ring(lines=(), *, capacity=10000, saturated=False, overwrite_count=0):
    return log_utils.RingBufferSnapshot(
        lines=tuple(lines),
        capacity=capacity,
        saturated=saturated,
        overwrite_count=overwrite_count,
    )


@pytest.mark.asyncio
async def test_bundle_spans_rotated_set_in_order_beyond_ten_thousand_lines(
    test_engine,
):
    all_lines = [_json_line(index) for index in range(10002)]
    snapshot = _snapshot(
        _source("ecm.log.2", all_lines[:4000]),
        _source("ecm.log.1", all_lines[4000:8000]),
        _source("ecm.log", all_lines[8000:]),
    )

    payload = await build_bundle(
        test_engine,
        persistent_log_snapshot=snapshot,
        ring_log_snapshot=_ring(["ring line must not replace persisted history"]),
    )
    members = extract_members(payload)
    captured = members["logs.txt"].decode().splitlines()
    manifest = json.loads(members["manifest.json"])
    metadata = manifest["log_members"]

    assert len(captured) == 10002
    assert [json.loads(line)["msg"] for line in captured] == [
        f"sequence-{index}" for index in range(10002)
    ]
    assert "logs-ring-fallback.txt" not in members
    assert metadata == [
        {
            "member": "logs.txt",
            "source": "rotating_files",
            "source_files": ["ecm.log.2", "ecm.log.1", "ecm.log"],
            "first_timestamp": "2026-08-28T12:00:00.000Z",
            "last_timestamp": "2026-08-28T14:46:41.000Z",
            "line_count": 10002,
            "byte_count": len(members["logs.txt"]),
            "truncated": False,
            "saturated": False,
            "timestamp_parse_failures": 0,
            "rotation_settings": {"max_bytes": 10 * 1024 * 1024, "backup_count": 4},
        }
    ]
    # The stacked hrukw additions and counts stay present and unchanged.
    assert "m3u_group_settings.json" in members
    assert "channel_profiles.json" in members
    assert manifest["m3u_group_setting_count"] == 0
    assert manifest["channel_profile_count"] == 0


@pytest.mark.asyncio
async def test_bundle_uses_applied_policy_and_reports_byte_limit_truncation(
    test_engine,
):
    snapshot = _snapshot(
        _source("ecm.log", [_json_line(1)]),
        byte_limit_reached=True,
    )
    applied_policy = log_utils.PersistentLogPolicy(
        max_bytes=2 * 1024 * 1024,
        backup_count=2,
    )
    snapshot_call = {}

    def observe_snapshot(args, kwargs):
        snapshot_call["thread"] = threading.get_ident()
        snapshot_call["args"] = args
        snapshot_call["kwargs"] = kwargs

    payload = await build_bundle(
        test_engine,
        persistent_log_snapshot=snapshot,
        persistent_log_policy=applied_policy,
        persistent_snapshot_observer=observe_snapshot,
    )
    metadata = json.loads(extract_members(payload)["manifest.json"])["log_members"][0]

    assert metadata["rotation_settings"] == {
        "max_bytes": 2 * 1024 * 1024,
        "backup_count": 2,
    }
    assert metadata["truncated"] is True
    assert metadata["saturated"] is False
    assert snapshot_call["thread"] != threading.get_ident()
    assert snapshot_call["kwargs"] == {
        "backup_count": 2,
        "max_total_bytes": 50 * 1024 * 1024,
    }


@pytest.mark.asyncio
async def test_blocking_snapshot_does_not_stall_the_event_loop(test_engine):
    snapshot = _snapshot(_source("ecm.log", [_json_line(1)]))

    def slow_snapshot(_args, _kwargs):
        time.sleep(0.08)

    build = asyncio.create_task(
        build_bundle(
            test_engine,
            persistent_log_snapshot=snapshot,
            persistent_snapshot_observer=slow_snapshot,
        )
    )
    ticks = 0
    deadline = asyncio.get_running_loop().time() + 0.06
    while asyncio.get_running_loop().time() < deadline:
        ticks += 1
        await asyncio.sleep(0.005)
    payload = await build

    assert ticks >= 5
    assert extract_members(payload)["logs.txt"]


@pytest.mark.asyncio
async def test_ring_only_fallback_reports_exact_saturation_and_parse_failures(
    test_engine,
):
    ring = _ring(
        [
            "2026-08-28 10:00:00,000 - ring - INFO - first",
            "not timestamped",
            "2026-08-28 10:01:00,000 - ring - INFO - last",
        ],
        capacity=3,
        saturated=True,
        overwrite_count=17,
    )

    payload = await build_bundle(
        test_engine,
        persistent_log_snapshot=_snapshot(handler_degraded=True),
        ring_log_snapshot=ring,
    )
    members = extract_members(payload)
    metadata = json.loads(members["manifest.json"])["log_members"]

    assert "logs-ring-fallback.txt" not in members
    assert metadata[0]["member"] == "logs.txt"
    assert metadata[0]["source"] == "ring_buffer"
    assert metadata[0]["source_files"] == []
    assert metadata[0]["first_timestamp"] == "2026-08-28 10:00:00,000"
    assert metadata[0]["last_timestamp"] == "2026-08-28 10:01:00,000"
    assert metadata[0]["timestamp_parse_failures"] == 1
    assert metadata[0]["saturated"] is True
    assert metadata[0]["truncated"] is True
    assert metadata[0]["overwrite_count"] == 17
    assert metadata[0]["fallback_reason"] == "persistent_logging_unavailable"
    assert metadata[0]["possible_overlap"] is False


@pytest.mark.asyncio
async def test_empty_but_available_persistent_set_uses_ring_as_no_files_fallback(
    test_engine,
):
    payload = await build_bundle(
        test_engine,
        persistent_log_snapshot=_snapshot(handler_degraded=False),
        ring_log_snapshot=_ring(["2026-08-28 10:00:00,000 - ring - INFO - only"]),
    )
    members = extract_members(payload)
    metadata = json.loads(members["manifest.json"])["log_members"][0]

    assert metadata["source"] == "ring_buffer"
    assert metadata["fallback_reason"] == "no_persisted_log_files"
    assert "logs-ring-fallback.txt" not in members


@pytest.mark.asyncio
async def test_partial_persisted_snapshot_includes_undeduplicated_ring_fallback(
    test_engine,
):
    persisted = _snapshot(
        _source("ecm.log.1", [_json_line(1), _json_line(2)], complete=False),
        incomplete=True,
        reason="source_read_incomplete",
    )
    ring_line = _json_line(2, message="possible overlap retained")

    payload = await build_bundle(
        test_engine,
        persistent_log_snapshot=persisted,
        ring_log_snapshot=_ring([ring_line]),
    )
    members = extract_members(payload)
    metadata = json.loads(members["manifest.json"])["log_members"]

    assert set(("logs.txt", "logs-ring-fallback.txt")) <= set(members)
    assert len(metadata) == 2
    assert metadata[0]["source"] == "rotating_files"
    assert metadata[0]["truncated"] is True
    assert metadata[0]["fallback_reason"] == "source_read_incomplete"
    assert metadata[0]["possible_overlap"] is True
    assert metadata[1]["source"] == "ring_buffer"
    assert metadata[1]["fallback_reason"] == "source_read_incomplete"
    assert metadata[1]["possible_overlap"] is True
    assert b"possible overlap retained" in members["logs-ring-fallback.txt"]


@pytest.mark.asyncio
async def test_actual_file_is_scrubbed_before_persistence_and_bundle_stays_clean(
    test_engine, tmp_path: Path
):
    message = f"auth rejected user={XC_USER} pass={XC_PASS}"
    log_utils.register_sensitive_values(XC_USER, XC_PASS)
    file_handler = log_utils.InterProcessRotatingJsonHandler(
        tmp_path / "logs" / "ecm.log", max_bytes=1024 * 1024, backup_count=2
    )
    file_handler.setFormatter(JsonFormatter())
    file_handler.addFilter(_TraceIdFilter())
    ring_handler = log_utils.RingBufferHandler(capacity=2)
    ring_handler.setFormatter(JsonFormatter())
    ring_handler.addFilter(_TraceIdFilter())
    record = logging.LogRecord(
        "credential.proof", logging.ERROR, __file__, 0, message, (), None
    )
    file_handler.handle(record)
    ring_handler.handle(record)

    persisted = log_utils.snapshot_persistent_logs(tmp_path, backup_count=2)
    ring = ring_handler.get_snapshot()
    assert all(XC_PASS.encode() not in source.data for source in persisted.files)
    assert all(XC_USER.encode() not in source.data for source in persisted.files)
    assert any(b"***REDACTED***" in source.data for source in persisted.files)
    assert any(XC_PASS in line and XC_USER in line for line in ring.lines)

    payload = await build_bundle(
        test_engine,
        persistent_log_snapshot=persisted,
        ring_log_snapshot=ring,
    )
    members = extract_members(payload)
    assert "logs.txt" in members
    assert "logs-ring-fallback.txt" in members
    assert scan_members(members, XC_PASS) == {}
    assert scan_members(members, XC_USER) == {}
    file_handler.close()
    log_utils._reset_sensitive_values_for_tests()
