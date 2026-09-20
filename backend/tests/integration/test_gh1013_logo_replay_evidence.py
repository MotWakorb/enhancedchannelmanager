"""GH #1013 / PR #1014: duplicate-logo handling proven across real boundaries.

Every test here uses the real ``DispatcharrClient`` over a controlled
``httpx.MockTransport`` that plays Dispatcharr's logo and channel endpoints
(and records every request), so nothing inside ``create_logo`` — the catalog
pre-check, the POST, the post-400 reconciliation, the reuse marker — is
mocked away. The consumers on top of it are the real ones:

* the DBAS logo importer and the real ``run_rollback`` consumer of its
  ``RollbackLedger`` (review item 1);
* ``_materialize_pipeline_plan`` (prepare) then the real
  ``POST /api/auto-creation/run/commit`` over ASGI, with the real replay and
  SQLite persistence (items 2 and 4);
* the real ``POST /api/channels/bulk-commit`` executor (item 5);
* the production logging handlers (item 6).
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

import log_utils
from config import DispatcharrSettings
from dispatcharr_client import DispatcharrClient
from models import ChannelPipelineExecution, ChannelPipelineSnapshot, JournalEntry

CANARY = "SECRET-TOKEN-8f3a9c-canary"
EXISTING_URL = "http://cdn.example/existing.png"


class Upstream:
    """Scripted Dispatcharr: a logo catalog, channels, and a request record."""

    def __init__(self, logos: list[dict] | None = None, channels: dict[int, dict] | None = None):
        self.logos: list[dict] = list(logos or [])
        self.channels: dict[int, dict] = dict(channels or {})
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[tuple[str, str, dict]] = []
        self.next_logo_id = 900
        self.next_channel_id = 101
        # Optional overrides keyed by (METHOD, path) -> callable(request) -> Response
        self.script: dict[tuple[str, str], object] = {}
        # When True, POST /logos/ answers 400 duplicate for a URL already present.
        self.reject_duplicate_logo = True
        # When set, the catalog is hidden from GETs until a logo POST has happened
        # (simulates a row that appears between the pre-check and the POST).
        self.hide_catalog_until_post = False
        self._posted_logo = False

    # -- transport -------------------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        self.calls.append(key)
        override = self.script.get(key)
        if override is not None:
            return override(request)
        return self.default(request)

    def _page(self, rows: list[dict], request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        size = int(request.url.params.get("page_size", "100"))
        chunk = rows[(page - 1) * size: page * size]
        has_next = page * size < len(rows)
        return httpx.Response(200, json={
            "count": len(rows), "next": f"?page={page + 1}" if has_next else None,
            "previous": None, "results": chunk,
        })

    def default(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == "/api/channels/logos/":
            if method == "GET":
                visible = [] if (self.hide_catalog_until_post and not self._posted_logo) else self.logos
                return self._page(visible, request)
            if method == "POST":
                body = json.loads(request.content or b"{}")
                self.bodies.append((method, path, body))
                self._posted_logo = True
                if self.reject_duplicate_logo and any(l.get("url") == body.get("url") for l in self.logos):
                    return httpx.Response(400, json={"url": [
                        f"logo with this url already exists. ({body.get('url')})"
                    ]})
                new_id, self.next_logo_id = self.next_logo_id, self.next_logo_id + 1
                row = {"id": new_id, **body}
                self.logos.append(row)
                return httpx.Response(201, json=row)
        if path.startswith("/api/channels/logos/") and method == "DELETE":
            logo_id = int(path.rstrip("/").rsplit("/", 1)[-1])
            self.logos = [l for l in self.logos if l["id"] != logo_id]
            return httpx.Response(204)
        if path == "/api/channels/channels/":
            if method == "GET":
                return self._page(list(self.channels.values()), request)
            if method == "POST":
                body = json.loads(request.content or b"{}")
                self.bodies.append((method, path, body))
                new_id, self.next_channel_id = self.next_channel_id, self.next_channel_id + 1
                self.channels[new_id] = {"id": new_id, **body}
                return httpx.Response(201, json=self.channels[new_id])
        if path.startswith("/api/channels/channels/") and path.endswith("/"):
            channel_id = int(path.rstrip("/").rsplit("/", 1)[-1])
            if method == "GET":
                return httpx.Response(200, json=self.channels.get(channel_id, {"id": channel_id}))
            if method == "PATCH":
                body = json.loads(request.content or b"{}")
                self.bodies.append((method, path, body))
                self.channels.setdefault(channel_id, {"id": channel_id}).update(body)
                return httpx.Response(200, json=self.channels[channel_id])
            if method == "DELETE":
                self.channels.pop(channel_id, None)
                return httpx.Response(204)
        if path in ("/api/channels/streams/", "/api/channels/groups/", "/api/channels/profiles/") and method == "GET":
            return self._page([], request)
        raise AssertionError(f"unexpected upstream call: {method} {path}")

    # -- helpers ---------------------------------------------------------
    def count(self, method: str, path: str) -> int:
        return sum(1 for m, p in self.calls if m == method and p == path)

    def patches_to(self, channel_id: int) -> list[dict]:
        return [b for m, p, b in self.bodies if m == "PATCH" and p == f"/api/channels/channels/{channel_id}/"]


def _client(upstream: Upstream) -> DispatcharrClient:
    client = DispatcharrClient(
        DispatcharrSettings(url="http://dispatcharr", auth_method="api_key", api_key="k")
    )
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
    return client


# ===========================================================================
# Item 1: reuse never grants DBAS rollback ownership — real client, real
# importer, real rollback consumer.
# ===========================================================================


async def _import_remote_logo(upstream: Upstream, tmp_path, *, name: str = "Remote Logo"):
    from dbas.importers.logos import import_logos
    from dbas.restore_contracts import EntityType, IdRemapTable, RestoreReport, RollbackLedger
    from dbas.restore_orchestrator import run_rollback

    report, ledger, remap = RestoreReport(is_dry_run=False), RollbackLedger(restore_id="t"), IdRemapTable()
    client = _client(upstream)
    try:
        await import_logos(
            archive_logos=[{"id": 42, "name": name, "url": EXISTING_URL}],
            client=client, selected=True, report=report, ledger=ledger, remap=remap,
            content_provider=None,
        )
        rollback = await run_rollback(ledger=ledger, client=client, ledger_dir=tmp_path)
    finally:
        await client._client.aclose()
    return report, ledger, remap, rollback, EntityType


@pytest.mark.asyncio
async def test_precheck_reuse_survives_dbas_rollback(tmp_path):
    upstream = Upstream(logos=[{"id": 765, "name": "Different Name", "url": EXISTING_URL}])

    report, ledger, remap, rollback, EntityType = await _import_remote_logo(upstream, tmp_path)

    cat = report.category(EntityType.LOGO)
    assert cat.created == 0 and cat.skipped == 1
    assert remap.resolve(EntityType.LOGO, 42) == 765
    assert [e for e in ledger.entries if e.entity_type == EntityType.LOGO] == []
    assert upstream.count("POST", "/api/channels/logos/") == 0         # pre-check hit, no POST
    assert rollback.complete is True and rollback.compensated == []
    assert ("DELETE", "/api/channels/logos/765/") not in upstream.calls
    assert [l["id"] for l in upstream.logos] == [765]                    # still there


@pytest.mark.asyncio
async def test_post_400_race_reuse_survives_dbas_rollback(tmp_path):
    upstream = Upstream(logos=[{"id": 765, "name": "Different Name", "url": EXISTING_URL}])
    upstream.hide_catalog_until_post = True   # importer + pre-check miss; the POST collides

    report, ledger, remap, rollback, EntityType = await _import_remote_logo(upstream, tmp_path)

    cat = report.category(EntityType.LOGO)
    assert upstream.count("POST", "/api/channels/logos/") == 1          # the race branch ran
    assert cat.created == 0 and cat.skipped == 1
    assert remap.resolve(EntityType.LOGO, 42) == 765
    assert [e for e in ledger.entries if e.entity_type == EntityType.LOGO] == []
    assert rollback.compensated == []
    assert ("DELETE", "/api/channels/logos/765/") not in upstream.calls
    assert [l["id"] for l in upstream.logos] == [765]


@pytest.mark.asyncio
async def test_genuine_create_is_ledgered_and_compensated_by_dbas_rollback(tmp_path):
    upstream = Upstream(logos=[])

    report, ledger, remap, rollback, EntityType = await _import_remote_logo(upstream, tmp_path)

    cat = report.category(EntityType.LOGO)
    assert cat.created == 1
    assert remap.resolve(EntityType.LOGO, 42) == 900
    assert [e.destination_id for e in ledger.entries if e.entity_type == EntityType.LOGO] == [900]
    assert rollback.complete is True and [e.destination_id for e in rollback.compensated] == [900]
    assert ("DELETE", "/api/channels/logos/900/") in upstream.calls
    assert upstream.logos == []


# ===========================================================================
# Items 2 and 4: prepare -> commit through the real route, real replay, and
# persisted evidence.
# ===========================================================================

COMMIT_URL = "/api/auto-creation/run/commit"


def _payload(writes: list[dict], preconditions: dict | None = None) -> dict:
    return {
        "request": {"m3u_account_ids": None, "rule_ids": [7]},
        "result": {
            "event_sync": [], "planned_review_candidates": [], "execution_log": [],
            "failed_actions": [], "channels_created": 0,
        },
        "write_plan": {
            "writes": [{"kwargs": {}, "event_sync": None, **w} for w in writes],
            "channel_preconditions": preconditions or {},
            "group_preconditions": {}, "profile_preconditions": {},
        },
        "snapshot": [],
    }


async def _prepare_then_commit(async_client, test_engine, upstream: Upstream, payload: dict):
    from routers import channel_pipeline as router
    from services import mutation_plan_store as store

    client = _client(upstream)
    engine = SimpleNamespace(
        client=client, _load_rules=AsyncMock(return_value=[]), _update_rule_stats=AsyncMock(),
    )
    try:
        with patch.object(store, "mutation_plan_store", store.MutationPlanStore()), \
             patch.object(router, "_ensure_engine", AsyncMock(return_value=engine)), \
             patch.object(router, "_compute_pipeline_plan_payload", AsyncMock(return_value=payload)), \
             patch.object(router, "get_session", sessionmaker(bind=test_engine)):
            prepared = await router._materialize_pipeline_plan(router.RunPipelineRequest(rule_ids=[7]))
            response = await async_client.post(COMMIT_URL, json={
                "plan_id": prepared["plan_id"], "plan_hash": prepared["plan_hash"], "phase": "execute",
            })
    finally:
        await client._client.aclose()
    return response


def _execution(test_engine, execution_id: int):
    session = sessionmaker(bind=test_engine)()
    try:
        execution = session.get(ChannelPipelineExecution, execution_id)
        journal = session.query(JournalEntry).filter(JournalEntry.batch_id == str(execution_id)).all()
        snapshot = session.query(ChannelPipelineSnapshot).filter_by(execution_id=execution_id).first()
        return (
            execution,
            [(row.action_type, row.entity_id, row.description) for row in journal],
            snapshot.get_channels_data() if snapshot is not None else {},
        )
    finally:
        session.close()


@pytest.mark.asyncio
async def test_failed_replacement_artwork_leaves_the_existing_logo_and_applies_the_rename(
    async_client, test_engine,
):
    """Item 2: the replacement logo collides upstream and nothing else has it,
    so the create soft-fails; the update must still rename channel 7 and must
    not touch its logo."""
    channel_7 = {"id": 7, "name": "Existing", "logo_id": 99, "streams": []}
    upstream = Upstream(logos=[{"id": 500, "name": "other", "url": "http://cdn.example/new.png"}],
                        channels={7: dict(channel_7)})
    # The colliding row is invisible to lookups (a stale catalog index upstream),
    # so create_logo cannot reconcile it and the write soft-fails.
    upstream.script[("GET", "/api/channels/logos/")] = lambda request: upstream._page([], request)
    payload = _payload(
        [
            {"method": "create_logo", "args": [{"name": "L", "url": "http://cdn.example/new.png"}]},
            {"method": "update_channel", "args": [7, {"logo_id": -1, "name": "Renamed"}]},
        ],
        preconditions={"7": channel_7},
    )

    response = await _prepare_then_commit(async_client, test_engine, upstream, payload)

    assert response.status_code == 202, response.text
    execution_id = response.json()["execution_id"]
    assert upstream.patches_to(7) == [{"name": "Renamed"}]        # logo_id omitted, never null
    assert upstream.channels[7]["logo_id"] == 99                   # existing artwork preserved
    assert upstream.channels[7]["name"] == "Renamed"               # independent field applied

    execution, journal, _ = _execution(test_engine, execution_id)
    assert execution.status == "completed"
    skipped = [w for w in execution.get_warnings() if w["type"] == "replay_write_skipped"]
    assert [(w["index"], w["method"], w["reason"]) for w in skipped] == [(0, "create_logo", "soft_failure")]
    assert not any(action == "create_logo" for action, _, _ in journal)
    assert not any("None" in (description or "") for _, _, description in journal)


@pytest.mark.asyncio
async def test_logo_only_replacement_is_not_sent_and_is_recorded_as_dependency_skipped(
    async_client, test_engine,
):
    channel_7 = {"id": 7, "name": "Existing", "logo_id": 99, "streams": []}
    upstream = Upstream(logos=[{"id": 500, "name": "other", "url": "http://cdn.example/new.png"}],
                        channels={7: dict(channel_7)})
    upstream.script[("GET", "/api/channels/logos/")] = lambda request: upstream._page([], request)
    payload = _payload(
        [
            {"method": "create_logo", "args": [{"name": "L", "url": "http://cdn.example/new.png"}]},
            {"method": "update_channel", "args": [7, {"logo_id": -1}]},
            {"method": "create_channel", "args": [{"name": "New", "logo_id": -1, "streams": [5]}]},
        ],
        preconditions={"7": channel_7},
    )

    response = await _prepare_then_commit(async_client, test_engine, upstream, payload)

    assert response.status_code == 202, response.text
    execution_id = response.json()["execution_id"]
    assert upstream.patches_to(7) == []                            # nothing truthful to send
    assert upstream.channels[7]["logo_id"] == 99
    created = [b for m, p, b in upstream.bodies if m == "POST" and p == "/api/channels/channels/"]
    assert created == [{"name": "New", "streams": [5]}]            # new channel, no logo field

    execution, journal, _ = _execution(test_engine, execution_id)
    assert execution.status == "completed"
    skipped = [w for w in execution.get_warnings() if w["type"] == "replay_write_skipped"]
    assert [(w["index"], w["method"], w["reason"]) for w in skipped] == [
        (0, "create_logo", "soft_failure"), (1, "update_channel", "dependency_skipped"),
    ]
    assert skipped[1]["channel_id"] == 7
    assert [action for action, _, _ in journal] == ["create_channel"]
    assert journal[0][1] == 101


@pytest.mark.asyncio
async def test_true_failing_index_is_persisted_after_a_soft_skip(async_client, test_engine):
    """Item 4: [create_logo skipped, create_channel ok, update_channel fails]
    must persist failed_index 2 in the execution log and the snapshot
    evidence, not len(completed) == 1."""
    upstream = Upstream(logos=[{"id": 500, "name": "other", "url": "http://cdn.example/new.png"}])
    upstream.script[("GET", "/api/channels/logos/")] = lambda request: upstream._page([], request)
    upstream.script[("PATCH", "/api/channels/channels/101/")] = (
        lambda request: httpx.Response(500, json={"detail": "boom"})
    )
    payload = _payload([
        {"method": "create_logo", "args": [{"name": "L", "url": "http://cdn.example/new.png"}]},
        {"method": "create_channel", "args": [{"name": "New", "logo_id": -1, "streams": [5]}]},
        {"method": "update_channel", "args": [-2, {"name": "Renamed"}]},
    ])

    response = await _prepare_then_commit(async_client, test_engine, upstream, payload)

    assert response.status_code in (424, 502), response.text
    assert ("DELETE", "/api/channels/channels/101/") in upstream.calls   # compensation unchanged
    session = sessionmaker(bind=test_engine)()
    try:
        execution = session.query(ChannelPipelineExecution).order_by(ChannelPipelineExecution.id.desc()).first()
        snapshot = session.query(ChannelPipelineSnapshot).filter_by(execution_id=execution.id).one()
    finally:
        session.close()
    assert execution.status == "failed"
    entry = execution.get_execution_log()[0]
    assert entry["type"] == "partial_replay_failure"
    assert entry["failed_index"] == 2
    assert snapshot.get_channels_data()["partial_replay"]["failed_index"] == 2


# ===========================================================================
# Item 5: the real bulk caller over the real client scans the catalog once.
# ===========================================================================


async def _bulk_commit_and_wait(async_client, body, *, max_polls=200):
    response = await async_client.post("/api/channels/bulk-commit", json=body)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    for _ in range(max_polls):
        await asyncio.sleep(0)
        poll = await async_client.get(f"/api/channels/bulk-commit/{job_id}")
        assert poll.status_code == 200, poll.text
        payload = poll.json()
        if payload["status"] == "completed":
            return payload["result"]
        if payload["status"] == "failed":
            return payload
    raise AssertionError("bulk-commit job did not terminate")


@pytest.mark.asyncio
async def test_bulk_create_scans_the_logo_catalog_once_for_many_urls(async_client):
    catalog = [{"id": i, "name": f"logo {i}", "url": f"http://cdn.example/{i}.png"} for i in range(1, 1002)]
    upstream = Upstream(logos=catalog)
    client = _client(upstream)
    ops = [
        {"type": "createChannel", "tempId": -1, "name": "A", "logoUrl": "http://cdn.example/new-a.png"},
        {"type": "createChannel", "tempId": -2, "name": "B", "logoUrl": "http://cdn.example/new-b.png"},
        {"type": "createChannel", "tempId": -3, "name": "C", "logoUrl": "http://cdn.example/new-c.png"},
        {"type": "createChannel", "tempId": -4, "name": "A2", "logoUrl": "http://cdn.example/new-a.png"},
        {"type": "createChannel", "tempId": -5, "name": "E", "logoUrl": "http://cdn.example/500.png"},
    ]
    try:
        with patch("routers.channels.get_client", return_value=client), \
             patch("routers.channels.journal"):
            result = await _bulk_commit_and_wait(
                async_client, {"operations": ops, "continueOnError": True},
            )
    finally:
        await client._client.aclose()

    assert result.get("operationsApplied") == 5, result
    assert upstream.count("GET", "/api/channels/logos/") == 3          # 1001 rows / 500 per page, once
    assert upstream.count("POST", "/api/channels/logos/") == 3         # three distinct new URLs
    channel_posts = [b for m, p, b in upstream.bodies if m == "POST" and p == "/api/channels/channels/"]
    assert len(channel_posts) == 5
    by_name = {b["name"]: b for b in channel_posts}
    assert by_name["A"]["logo_id"] == by_name["A2"]["logo_id"]         # repeated URL reused in-run
    assert by_name["E"]["logo_id"] == 500                              # existing URL from the index


# ===========================================================================
# Item 6: the production logging handlers never see the upstream body.
# ===========================================================================


@pytest.fixture
def production_handlers(tmp_path):
    """Console (main.py's basicConfig format), ring buffer, and — where the
    platform allows — the persistent JSON handler, all on the root logger."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    log_utils._reset_file_logging_for_tests()
    log_utils._reset_sensitive_values_for_tests()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    console_stream = io.StringIO()
    console = logging.StreamHandler(console_stream)
    console.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    root.addHandler(console)
    ring = log_utils.RingBufferHandler(1000)
    ring.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    root.addHandler(ring)
    persistent = log_utils.install_persistent_json_logging(tmp_path, max_bytes=1_000_000, backup_count=2)
    root.setLevel(logging.DEBUG)
    try:
        yield SimpleNamespace(console=console_stream, ring=ring, persistent=persistent, dir=tmp_path)
    finally:
        log_utils._reset_file_logging_for_tests()
        log_utils._reset_sensitive_values_for_tests()
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)


@pytest.mark.asyncio
async def test_logo_create_failure_reaches_no_production_handler_with_the_body(production_handlers):
    registered = "registered-provider-secret-77"
    log_utils.register_sensitive_values(registered)
    leaky_url = f"http://provider.example/logo.png?token={CANARY}&key={registered}"
    upstream = Upstream(logos=[{"id": 500, "name": "other", "url": leaky_url}])
    # No catalog hit for the create's lookups, so the 400 is terminal and the
    # client has to describe the failure itself.
    upstream.script[("GET", "/api/channels/logos/")] = lambda request: upstream._page([], request)
    client = _client(upstream)
    try:
        with pytest.raises(Exception) as error:
            await client.create_logo({"name": "L", "url": leaky_url})
    finally:
        await client._client.aclose()

    assert "duplicate_url" in str(error.value)                        # classification kept
    console = production_handlers.console.getvalue()
    ring = "\n".join(production_handlers.ring.get_lines())
    assert "Logo creation failed" in console and "duplicate_url" in console
    for sink_name, sink in (("console", console), ("ring", ring)):
        assert CANARY not in sink, sink_name
        assert registered not in sink, sink_name
        assert "provider.example" not in sink, sink_name
        assert "already exists" not in sink, sink_name                # the body's own text
    assert CANARY not in str(error.value) and "provider.example" not in str(error.value)

    if production_handlers.persistent is None:
        if sys.platform == "win32":
            pytest.skip("persistent JSON handler needs a POSIX directory descriptor; CI verifies it")
        raise AssertionError("persistent JSON handler did not install")
    snapshot = log_utils.snapshot_persistent_logs(production_handlers.dir, backup_count=2)
    persisted = "\n".join(source.data.decode("utf-8", "replace") for source in snapshot.files)
    assert "Logo creation failed" in persisted
    assert CANARY not in persisted and registered not in persisted and "provider.example" not in persisted
