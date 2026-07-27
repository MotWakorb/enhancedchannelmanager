"""Stateful two-instance (source-A → dest-B) cross-instance sync TEST HARNESS.

Bead ``enhancedchannelmanager-46pkq`` (epic ``i39wu``). This is the reusable,
shareable harness the convergence / idempotency / partial-failure keystone tests
rest on (``tests/tasks/test_sync_roundtrip.py``), the sync-test analogue of the
DBAS restore round-trip anchor (``tests/dbas/test_restore_roundtrip.py``).

What problem this solves
------------------------
The pre-existing sync engine tests (``test_dbas_sync_engine.py``) mock dest-B as a
bag of independent ``AsyncMock`` create/get methods and then assert on **call
counts** (``create_m3u_account.assert_awaited()``). That proves the engine *calls*
B, but it cannot prove B *converged*: a stateless mock's ``get_*`` always returns
the same canned list regardless of what was just created, so a second ``run_sync``
re-creates everything and "idempotency" can only be asserted against a *different*
hand-built "already converged" mock — not against the real apply.

This harness instead models **B as a real instance**: a :class:`StatefulDispatcharrFake`
that APPLIES the writes it receives. ``create_*`` stores the entity and returns it
with a NEW server-assigned id; a duplicate ``create_*`` (same natural key) raises a
**409 conflict** exactly like Dispatcharr; ``update_*`` mutates the stored row;
``delete_*`` (the rollback compensator) removes it (404 when already gone). That
statefulness is what makes the assertions REAL:

* **Convergence** — after ``run_sync(confirm_apply=True)`` against an empty B you
  can assert ``B.state() == A.state()`` *by natural key*, not by call count.
* **Idempotency** — a SECOND ``run_sync`` against the SAME (now-populated) B is a
  genuine no-op: B's own ``get_*`` now returns what was created, so the importers
  match → ``ALREADY_EXISTS_IDENTICAL`` and zero creates fire. No second mock.
* **Partial failure** — inject a mid-sync write error on B; because B actually
  stored the earlier creates, you can assert the rollback compensated them and B
  is left consistent, then that a clean re-run converges.

Write-API contract fidelity (the bead's least-validated surface)
----------------------------------------------------------------
The dest-B fake models the Dispatcharr WRITE contract the sync path depends on,
captured as fixtures here because no live Dispatcharr-B is reachable in this
environment (see ``docs/testing/dbas-test-env.md`` → cross-instance section):

* ``create_*`` returns the created object echoing the payload PLUS a NEW
  server-assigned ``id`` (B assigns ids; A's ids are never reused on B).
* a DUPLICATE ``create_*`` (same natural key already present) surfaces a **409
  conflict** — :class:`FakeConflictError`, a ``httpx.HTTPStatusError`` carrying a
  real 409 ``Response`` so the importers' status classifiers (which call
  ``raise_for_status`` semantics / parse ``"<thing> failed: <status> - <body>"``)
  treat it correctly.
* ``update_*`` mutates the stored row in place and returns it.
* ``delete_*`` removes the row; deleting an absent id raises a 404
  (:class:`FakeNotFoundError`) — the orchestrator's ``404-as-success`` rollback
  path depends on this shape.
* ``create_channel_group`` takes a NAME STRING (Dispatcharr's quirk); every other
  ``create_*`` takes a payload DICT — mirrored faithfully so the REUSED importers
  run unchanged.

Conventions (``docs/style_guide.md``): ``snake_case``; Google-style docstrings;
lazy ``%``-formatted logging; no secrets in any log line.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from unittest.mock import MagicMock, patch

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Write-contract errors — real httpx.HTTPStatusError so the importer / orchestrator
# status classifiers (raise_for_status semantics, "<thing> failed: <code>" text)
# bucket them exactly as they would a live Dispatcharr.
# ---------------------------------------------------------------------------


def _http_status_error(status_code: int, detail: str) -> httpx.HTTPStatusError:
    """Build an httpx.HTTPStatusError carrying a real Response of ``status_code``.

    The DBAS rollback's ``_status_code_of`` reads ``exc.response.status_code`` for
    an ``httpx.HTTPStatusError`` first (its primary path), and the importers'
    ``upstream_http_exception`` mapper also recognises this shape. Building a real
    Response (not a bare ``Exception``) is what makes the 409/404 fidelity REAL.
    """
    request = httpx.Request("POST", "http://dest-b.fake/api/")
    response = httpx.Response(status_code, json={"detail": detail}, request=request)
    return httpx.HTTPStatusError(detail, request=request, response=response)


class FakeConflictError(httpx.HTTPStatusError):
    """409 raised by a duplicate ``create_*`` (natural key already present on B)."""

    def __init__(self, label: str):
        request = httpx.Request("POST", "http://dest-b.fake/api/")
        response = httpx.Response(
            409, json={"detail": "already exists"}, request=request
        )
        # The importers parse "<thing> failed: <status> - <body>" from str(exc) as a
        # fallback; include the code in the message so BOTH classifier paths work.
        super().__init__(
            "create failed: 409 - %s already exists" % label,
            request=request,
            response=response,
        )


class FakeNotFoundError(httpx.HTTPStatusError):
    """404 raised by ``delete_*`` of an absent id — rollback treats it as success."""

    def __init__(self, entity: str, entity_id: int):
        request = httpx.Request("DELETE", "http://dest-b.fake/api/")
        response = httpx.Response(404, json={"detail": "not found"}, request=request)
        super().__init__(
            "%s delete failed: 404 - id %s not found" % (entity, entity_id),
            request=request,
            response=response,
        )


# ---------------------------------------------------------------------------
# Natural-key helpers — how the REUSED importers decide identity. The fake's
# duplicate detection MUST use the same key the importer match uses, or the
# 409 contract would fire on rows the importer considers distinct (and vice
# versa). Importers match config rows case-insensitively / trimmed on ``name``;
# channels on ``(name, channel_number)``.
# ---------------------------------------------------------------------------


def _norm_name(value: Any) -> Optional[str]:
    """Case-insensitive, whitespace-trimmed name key; None when blank/absent."""
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def _name_key(row: dict) -> Optional[str]:
    return _norm_name(row.get("name"))


def _channel_key(row: dict) -> tuple:
    """A channel's natural key — (normalized name, channel_number-or-None).

    Mirrors the importer's ``(name, channel_number)`` identity. A null number is
    preserved (not coerced) so the collision-safe floor (ruling 1a) — null on BOTH
    sides == ambiguous — still triggers through the harness.
    """
    return (_norm_name(row.get("name")), row.get("channel_number"))


def _stream_key(row: dict) -> Optional[str]:
    """A stream's identity for B — its ``url`` (the matcher's Tier-1 identity)."""
    url = row.get("url")
    return str(url) if url else None


# ---------------------------------------------------------------------------
# One entity collection inside a stateful instance.
# ---------------------------------------------------------------------------


@dataclass
class _Store:
    """A keyed collection of one entity type inside a stateful fake instance.

    Holds rows by server-assigned id and enforces the natural-key uniqueness that
    drives the 409 contract.
    """

    name: str
    key_fn: Callable[[dict], Any]
    rows: dict[int, dict] = field(default_factory=dict)
    _next_id: int = 1
    id_base: int = 1

    def __post_init__(self) -> None:
        self._next_id = self.id_base

    def list(self) -> list[dict]:
        return [dict(r) for r in self.rows.values()]

    def _find_by_key(self, key: Any) -> Optional[int]:
        if key is None:
            return None
        for rid, row in self.rows.items():
            if self.key_fn(row) == key:
                return rid
        return None

    def create(self, payload: dict) -> dict:
        """Store a new row, assigning a fresh id; 409 on a natural-key duplicate."""
        key = self.key_fn(payload)
        if self._find_by_key(key) is not None:
            label = payload.get("name") or self.name
            raise FakeConflictError(str(label))
        new_id = self._next_id
        self._next_id += 1
        row = {**payload, "id": new_id}
        self.rows[new_id] = row
        return dict(row)

    def update(self, entity_id: int, data: dict) -> dict:
        if entity_id not in self.rows:
            raise FakeNotFoundError(self.name, entity_id)
        self.rows[entity_id].update(data)
        return dict(self.rows[entity_id])

    def delete(self, entity_id: int) -> None:
        if entity_id not in self.rows:
            raise FakeNotFoundError(self.name, entity_id)
        del self.rows[entity_id]


# ---------------------------------------------------------------------------
# The stateful Dispatcharr fake — one per instance (A and B).
# ---------------------------------------------------------------------------


class StatefulDispatcharrFake:
    """An in-memory, STATEFUL stand-in for a single Dispatcharr instance.

    Exposes the async client method surface the sync gather + the REUSED DBAS
    importers call (``get_* / create_* / update_* / delete_*``), backed by real
    per-entity stores. Unlike a bare ``AsyncMock``, every write is APPLIED, so the
    instance's reads reflect prior writes — the property the convergence /
    idempotency / partial-failure assertions need.

    Use :meth:`StatefulDispatcharrFake.seeded_source` for a populated source-A and
    :meth:`StatefulDispatcharrFake.empty_dest` for an empty dest-B. The
    ``id_base`` per store is offset per instance so A's ids and B's ids never
    coincide by accident (a test that confused the two would otherwise pass).
    """

    def __init__(self, *, label: str, id_base: int = 1):
        self.label = label
        # Distinct id bases per entity type so a leaked A-id is obvious on B.
        self.m3u_accounts = _Store("m3u_account", _name_key, id_base=id_base + 100)
        self.epg_sources = _Store("epg_source", _name_key, id_base=id_base + 200)
        self.channel_groups = _Store("channel_group", _name_key, id_base=id_base + 300)
        self.channel_profiles = _Store("channel_profile", _name_key, id_base=id_base + 400)
        self.stream_profiles = _Store("stream_profile", _name_key, id_base=id_base + 500)
        self.channels = _Store("channel", _channel_key, id_base=id_base + 600)
        self.streams = _Store("stream", _stream_key, id_base=id_base + 700)

        # Optional write-fault injection: a callable invoked at the start of every
        # mutating call with (method_name, payload). If it raises, the fake raises
        # that error (models a mid-sync upstream failure on B).
        self._fault: Optional[Callable[[str, Any], None]] = None

    # ----- fault injection -------------------------------------------------

    def inject_fault(self, fault: Optional[Callable[[str, Any], None]]) -> None:
        """Install (or clear with ``None``) a write-fault hook for B.

        ``fault(method_name, payload)`` runs before each mutating method applies.
        Raising from it simulates a mid-sync upstream error; the importer/
        orchestrator then drives its failure + compensating-rollback path against
        the REAL state already stored, exactly as it would against a live B.
        """
        self._fault = fault

    def _check_fault(self, method: str, payload: Any) -> None:
        if self._fault is not None:
            self._fault(method, payload)

    # ----- M3U accounts ----------------------------------------------------

    async def get_m3u_accounts(self) -> list:
        return self.m3u_accounts.list()

    async def create_m3u_account(self, data: dict) -> dict:
        self._check_fault("create_m3u_account", data)
        return self.m3u_accounts.create(data)

    async def update_m3u_account(self, account_id: int, data: dict) -> dict:
        self._check_fault("update_m3u_account", data)
        return self.m3u_accounts.update(account_id, data)

    async def delete_m3u_account(self, account_id: int) -> None:
        self.m3u_accounts.delete(account_id)

    async def get_streams(self, page: int = 1, page_size: int = 100, **kwargs) -> dict:
        # The m3u importer probes get_streams(m3u_account=...) to count streams; and
        # the channels importer reads B's standalone streams to match. Return the
        # paginated shape the real client returns.
        rows = self.streams.list()
        m3u_account = kwargs.get("m3u_account")
        if m3u_account is not None:
            rows = [s for s in rows if s.get("m3u_account") == m3u_account]
        return {"count": len(rows), "next": None, "previous": None, "results": rows}

    async def create_stream(self, data: dict) -> dict:
        self._check_fault("create_stream", data)
        return self.streams.create(data)

    async def update_stream(self, stream_id: int, data: dict) -> dict:
        self._check_fault("update_stream", data)
        return self.streams.update(stream_id, data)

    async def delete_stream(self, stream_id: int) -> None:
        self.streams.delete(stream_id)

    # ----- EPG sources -----------------------------------------------------

    async def get_epg_sources(self) -> list:
        return self.epg_sources.list()

    async def create_epg_source(self, data: dict) -> dict:
        self._check_fault("create_epg_source", data)
        return self.epg_sources.create(data)

    async def update_epg_source(self, source_id: int, data: dict) -> dict:
        self._check_fault("update_epg_source", data)
        return self.epg_sources.update(source_id, data)

    async def delete_epg_source(self, source_id: int) -> None:
        self.epg_sources.delete(source_id)

    # ----- channel groups (create takes a NAME STRING) ---------------------

    async def get_channel_groups(self) -> list:
        return self.channel_groups.list()

    async def create_channel_group(self, name: str) -> dict:
        self._check_fault("create_channel_group", name)
        return self.channel_groups.create({"name": name})

    async def update_channel_group(self, group_id: int, data: dict) -> dict:
        self._check_fault("update_channel_group", data)
        return self.channel_groups.update(group_id, data)

    async def delete_channel_group(self, group_id: int) -> None:
        self.channel_groups.delete(group_id)

    # ----- channel profiles ------------------------------------------------

    async def get_channel_profiles(self) -> list:
        return self.channel_profiles.list()

    async def create_channel_profile(self, data: dict) -> dict:
        self._check_fault("create_channel_profile", data)
        return self.channel_profiles.create(data)

    async def update_channel_profile(self, profile_id: int, data: dict) -> dict:
        self._check_fault("update_channel_profile", data)
        return self.channel_profiles.update(profile_id, data)

    async def delete_channel_profile(self, profile_id: int) -> None:
        self.channel_profiles.delete(profile_id)

    async def update_profile_channel(
        self, profile_id: int, channel_id: int, data: dict
    ) -> dict:
        # Profile-membership toggle — best-effort, no store of its own here.
        return {"success": True}

    # ----- stream profiles -------------------------------------------------

    async def get_stream_profiles(self) -> list:
        return self.stream_profiles.list()

    async def create_stream_profile(self, data: dict) -> dict:
        self._check_fault("create_stream_profile", data)
        return self.stream_profiles.create(data)

    async def delete_stream_profile(self, profile_id: int) -> None:
        # Compensating delete for a created stream profile (v1uz9). A 404 on an
        # already-gone id is raised as FakeNotFoundError, which the rollback's
        # 404-as-success path treats as a successful compensation.
        self.stream_profiles.delete(profile_id)

    # ----- channels --------------------------------------------------------

    async def get_channels(
        self, page: int = 1, page_size: int = 100, **kwargs
    ) -> dict:
        rows = self.channels.list()
        return {"count": len(rows), "next": None, "previous": None, "results": rows}

    async def get_channel_streams(self, channel_id: int) -> list:
        ch = self.channels.rows.get(channel_id)
        if not ch:
            return []
        stream_ids = ch.get("streams", []) or []
        return [s for s in self.streams.list() if s.get("id") in stream_ids]

    async def create_channel(self, data: dict) -> dict:
        self._check_fault("create_channel", data)
        return self.channels.create(data)

    async def update_channel(self, channel_id: int, data: dict) -> dict:
        self._check_fault("update_channel", data)
        return self.channels.update(channel_id, data)

    async def delete_channel(self, channel_id: int) -> None:
        self.channels.delete(channel_id)

    # ----- users (NEVER synced, but the rollback dispatch references the
    #        compensator unconditionally) -----------------------------------

    async def delete_user(self, user_id: int) -> None:
        # Users are never synced (D3) so this is never created, hence never a
        # rollback target. It exists only because the orchestrator's
        # ``_delete_dispatch`` builds the FULL EntityType->deleter map eagerly and
        # references ``client.delete_user``. A 404 is the correct "already gone".
        raise FakeNotFoundError("user", user_id)

    # ----- user agents / DVR rules / logos (not in the sync category set, but
    #        _delete_dispatch references their compensators eagerly — kxcjf) ---

    async def delete_user_agent(self, user_agent_id: int) -> None:
        raise FakeNotFoundError("user_agent", user_agent_id)

    async def delete_dvr_rule(self, rule_id: int) -> None:
        raise FakeNotFoundError("dvr_rule", rule_id)

    async def delete_logo(self, logo_id: int) -> None:
        raise FakeNotFoundError("logo", logo_id)

    # ----- state snapshot (the convergence assertion surface) --------------

    def state_by_key(self) -> dict[str, set]:
        """A natural-key snapshot of THIS instance's syncable config + channels.

        The convergence assertion compares ``B.state_by_key()`` against
        ``A.state_by_key()``: equality means every source entity exists on B under
        its natural key (ids differ — B assigns its own — so the comparison is
        key-based, never id-based). Streams are keyed by url; channels by
        ``(name, number)``; config rows by normalized name.
        """
        return {
            "m3u_accounts": {_name_key(r) for r in self.m3u_accounts.list()},
            "epg_sources": {_name_key(r) for r in self.epg_sources.list()},
            "channel_groups": {_name_key(r) for r in self.channel_groups.list()},
            "channel_profiles": {_name_key(r) for r in self.channel_profiles.list()},
            "stream_profiles": {_name_key(r) for r in self.stream_profiles.list()},
            "channels": {_channel_key(r) for r in self.channels.list()},
        }

    def total_rows(self) -> int:
        """Total stored config + channel + stream rows — a quick consistency probe."""
        return sum(
            len(s.rows)
            for s in (
                self.m3u_accounts,
                self.epg_sources,
                self.channel_groups,
                self.channel_profiles,
                self.stream_profiles,
                self.channels,
                self.streams,
            )
        )

    # ----- factories -------------------------------------------------------

    @classmethod
    def empty_dest(cls, *, label: str = "dest-B") -> "StatefulDispatcharrFake":
        """An empty dest-B — every source entity is a fresh create."""
        return cls(label=label, id_base=5000)

    @classmethod
    def seeded_source(
        cls,
        *,
        label: str = "source-A",
        m3u_password: str = "SEED-M3U-SECRET",
        epg_api_key: str = "SEED-EPG-SECRET",
        with_embedded_streams: bool = False,
    ) -> "StatefulDispatcharrFake":
        """A populated source-A holding a production-shaped config + channels.

        Carries plaintext credential fields (``password`` on the M3U account,
        ``api_key`` on the EPG source) so the redaction end-to-end assertion is
        REAL: those literal secrets must NEVER reach B (D2). Also seeds a config +
        one non-colliding channel (``CNN``, number 5) so the channel slice
        converges.

        Args:
            with_embedded_streams: when ``True``, the seeded channel carries an
                embedded stream. NOTE: that stream's ``m3u_account`` FK is A's id,
                which does not resolve on a fresh B, so the channels importer
                (correctly, per production behaviour) synthesizes an "ECM Custom
                Streams" account on B to hold the orphan. That means strict
                ``A.state_by_key() == B.state_by_key()`` will NOT hold (B has the
                extra synthetic account) — use ``False`` (the default) for the
                strict config+channel convergence assertion, and ``True`` only for
                the dedicated channels+streams slice test that expects the synth.
        """
        fake = cls(label=label, id_base=1)
        # M3U account with a SECRET that must not reach B (D2).
        fake.m3u_accounts.create(
            {"name": "Provider A", "username": "operator", "password": m3u_password,
             "server_url": "http://provider-a.test/playlist.m3u"}
        )
        # EPG source with a SECRET api_key (D2).
        fake.epg_sources.create(
            {"name": "EPG One", "source_type": "xmltv", "m3u_account": None,
             "api_key": epg_api_key, "url": "http://epg-one.test/guide.xml"}
        )
        fake.channel_groups.create({"name": "News"})
        fake.channel_groups.create({"name": "Sports"})
        fake.channel_profiles.create({"name": "Default Profile"})
        fake.stream_profiles.create({"name": "Proxy Profile", "command": "ffmpeg"})

        if with_embedded_streams:
            stream = fake.streams.create(
                {"name": "CNN HD", "url": "http://provider-a.test/cnn.m3u8",
                 "m3u_account": 101}
            )
            fake.channels.create(
                {"name": "CNN", "channel_number": 5, "streams": [stream["id"]]}
            )
        else:
            # A non-colliding channel (non-null number) with NO embedded streams,
            # so strict state equality holds (no synthetic-account side effect).
            fake.channels.create(
                {"name": "CNN", "channel_number": 5, "streams": []}
            )
        return fake


# ---------------------------------------------------------------------------
# SyncTarget stand-in.
# ---------------------------------------------------------------------------


def make_sync_target(
    *, credential_version: int = 1, fuzzy_stream_matching: bool = False
) -> MagicMock:
    """A fake ``SyncTarget`` row — enabled, fresh, never-insecure.

    Mirrors the shape ``run_sync`` reads (``id``/``name``/``base_url``/
    ``credentials``/``enabled``/``token_revoked_at``/``credential_version``/
    ``insecure``/``fuzzy_stream_matching``).
    """
    target = MagicMock()
    target.id = 7
    target.name = "DR Box"
    target.base_url = "http://dr-box.lan:9191"
    target.enabled = True
    target.insecure = False
    target.token_revoked_at = None
    target.credential_version = credential_version
    target.credentials = "encrypted-blob"
    target.fuzzy_stream_matching = fuzzy_stream_matching
    return target


# ---------------------------------------------------------------------------
# The harness — wires A + B into run_sync and applies the right patch seams.
# ---------------------------------------------------------------------------


class SyncHarness:
    """A reusable stateful two-instance (A → B) sync driver.

    Wires a seeded source-A and a stateful dest-B into the REAL engine seam:

    * the LOCAL gather (``routers.backup.get_client``) is patched to return A, so
      ``build_live_source_plan`` reads A's real config + channels;
    * the remote-client factory (``dbas_sync_engine.make_remote_client``) is
      patched to return B, so the REUSED orchestrator + importers run against B's
      real stateful stores;
    * the freshness gate (``dbas_sync_engine.sync_freshness_reason``) is patched to
      whatever ``freshness_reason`` is set (default ``None`` = fresh) so the
      A/B convergence path is exercised without a DB session.

    Everything else is the UNCHANGED production path: ``run_sync`` → the redacted
    live-source plan → ``run_restore`` → the importers → B's stateful writes.

    Usage::

        harness = SyncHarness(
            source=StatefulDispatcharrFake.seeded_source(),
            dest=StatefulDispatcharrFake.empty_dest(),
        )
        report = await harness.run(confirm_apply=True, ledger_dir=tmp_path)
        assert harness.dest.state_by_key() == harness.source.state_by_key()
    """

    def __init__(
        self,
        *,
        source: StatefulDispatcharrFake,
        dest: StatefulDispatcharrFake,
        target: Optional[MagicMock] = None,
        freshness_reason: Optional[str] = None,
    ):
        self.source = source
        self.dest = dest
        self.target = target or make_sync_target(
            fuzzy_stream_matching=getattr(
                target, "fuzzy_stream_matching", False
            ) if target else False
        )
        self.freshness_reason = freshness_reason

    @contextmanager
    def _patched(self):
        """Apply the three engine seams (local gather / remote factory / freshness)."""
        # Imported lazily so importing the harness module never drags the engine in
        # at collection time (keeps the fixture import cheap + cycle-free).
        from routers import backup as backup_mod
        from tasks import dbas_sync_engine as engine

        with patch.object(backup_mod, "get_client", return_value=self.source), \
             patch.object(engine, "make_remote_client", return_value=self.dest), \
             patch.object(
                 engine, "sync_freshness_reason", return_value=self.freshness_reason
             ):
            yield

    async def run(self, *, confirm_apply: bool = False, ledger_dir=None, **kwargs):
        """Drive one ``run_sync`` cycle A → B and return the RestoreReport.

        ``confirm_apply=False`` (default) is a counts-only dry-run (zero writes to
        B); ``True`` applies source-wins. ``ledger_dir`` should be a ``tmp_path`` so
        the durable rollback ledger never touches the real CONFIG_DIR.
        """
        from tasks.dbas_sync_engine import run_sync

        with self._patched():
            return await run_sync(
                self.target,
                confirm_apply=confirm_apply,
                session=MagicMock(),
                ledger_dir=ledger_dir,
                **kwargs,
            )
