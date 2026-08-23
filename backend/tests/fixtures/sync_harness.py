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

import base64
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from unittest.mock import MagicMock, patch

import httpx

logger = logging.getLogger(__name__)

# A real 1x1 PNG. The logos importer decodes and validates the bytes it is
# handed, so a hosted-logo fetch has to return an image an image library
# accepts, not a placeholder string.
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


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
        # Guide rows are not a synced entity. They model independently minted
        # ids on A/B so channel-link tests must resolve by the portable tvg_id.
        self.epg_data = _Store("epg_data", _name_key, id_base=id_base + 250)
        self.channel_groups = _Store("channel_group", _name_key, id_base=id_base + 300)
        self.channel_profiles = _Store("channel_profile", _name_key, id_base=id_base + 400)
        self.stream_profiles = _Store("stream_profile", _name_key, id_base=id_base + 500)
        self.channels = _Store("channel", _channel_key, id_base=id_base + 600)
        self.streams = _Store("stream", _stream_key, id_base=id_base + 700)
        # Logos (bead 7ipq2.1): keyed by normalized name — the importer's tier-2
        # match key, and the natural key upload_logo_file writes under.
        self.logos = _Store("logo", _name_key, id_base=id_base + 800)
        # What ``fetch_logo_image`` serves for a HOSTED logo — a real 1x1 PNG,
        # the smallest thing the logos importer's post-decode validator accepts.
        self.hosted_logo_bytes: bytes = _PNG_BYTES
        # Every bulk_delete_logos invocation is recorded so tests can pin the
        # sync-path invariant: the destructive pre-step NEVER fires (ADR-013 S9).
        self.bulk_logo_delete_calls: list[list[int]] = []
        # PER-ACCOUNT PROVIDER GROUP SELECTION (bead …-avrix), modelled the way
        # Dispatcharr models it (0.29.0 ``dispatcharr_channels_channelgroupm3uaccount``,
        # written by ``PATCH /api/m3u/accounts/<id>/group-settings/`` as a
        # bulk_create UPSERT on ``(channel_group, m3u_account)``). This is the
        # setting that decides WHAT AN ACCOUNT INGESTS: on the live measured
        # account, 2 of 777 groups enabled is the difference between 316 channels
        # and the provider's whole 53,661-stream catalogue. Keyed
        # ``(m3u_account_id, channel_group_id) -> row``.
        self.group_settings: dict[tuple[int, int], dict] = {}
        # Every PROVIDER-touching M3U call, recorded so a test can pin ADR-013
        # S9: the sync path applies the destination's own group settings and
        # triggers NO provider refresh. Recorded rather than absent so the
        # assertion can actually fail — a method the fake does not define would
        # raise AttributeError, which is a different (and weaker) signal.
        self.m3u_refresh_calls: list[int] = []
        self.m3u_patch_calls: list[tuple[int, dict]] = []
        # Channel-profile MEMBERSHIP, modelled the way Dispatcharr models it
        # (0.29.0 ``apps/channels``): ``ChannelProfileMembership.enabled``
        # defaults to ``True``; the ``post_save`` signal on ChannelProfile
        # bulk-creates a row for every existing channel, and the channel-create
        # view bulk-creates a row on every existing profile with
        # ``enabled=True`` whenever ``channel_profile_ids`` is omitted — which
        # is what every ECM create does. So on a real instance a membership is
        # "ENABLED unless something explicitly disabled it", and only the
        # exceptions need storing. Modelling it this way is what makes the
        # enable-everything DEFAULT — the thing bead …-38c5a is about — real in
        # the harness instead of an assumption.
        self.disabled_memberships: set[tuple[int, int]] = set()

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
        # ``M3UAccountSerializer`` embeds the account's per-group settings under
        # ``channel_groups`` (confirmed live on 0.29.0: A's XC account serialized
        # 777 entries, 2 ``enabled``). The gather reads this shape, so the fake
        # has to produce it or a test can never see the selection cross.
        rows = self.m3u_accounts.list()
        for row in rows:
            account_id = row.get("id")
            # ``to_representation`` projects the four preference booleans out of
            # the blob and onto the top level — the ONLY route by which they can
            # reach a create payload (see ``_PREFERENCE_DEFAULTS``).
            custom = row.get("custom_properties") or {}
            for key, default in self._PREFERENCE_DEFAULTS.items():
                row[key] = custom.get(key, default)
            row["channel_groups"] = [
                dict(entry)
                for (acc_id, _group_id), entry in sorted(self.group_settings.items())
                if acc_id == account_id
            ]
        return rows

    def set_group_selection(self, account_id: int, entries: list[dict]) -> None:
        """Seed an account's per-group selection (test-side helper, not an API)."""
        for entry in entries:
            group_id = entry["channel_group"]
            self.group_settings[(account_id, group_id)] = {
                "channel_group": group_id,
                "enabled": entry.get("enabled", True),
                "auto_channel_sync": entry.get("auto_channel_sync", False),
                **{
                    k: entry[k]
                    for k in ("auto_sync_channel_start", "auto_sync_channel_end",
                              "custom_properties")
                    if entry.get(k) is not None
                },
            }

    def enabled_group_ids(self, account_id: int) -> set[int]:
        """The group ids this account would ingest from — the operator's choice."""
        return {
            gid
            for (acc_id, gid), entry in self.group_settings.items()
            if acc_id == account_id and entry.get("enabled")
        }

    async def update_m3u_group_settings(self, account_id: int, data: dict) -> dict:
        """UPSERT the per-group settings — the real endpoint's exact semantics.

        Dispatcharr 0.29.0 ``apps/m3u/api_views.py::update_group_settings``
        validates the auto-sync ranges then ``bulk_create(..., update_conflicts=
        True, unique_fields=["channel_group", "m3u_account"])``. It triggers NO
        refresh and opens no socket to the provider, which is why the sync path
        may call it under ADR-013 S9.
        """
        self._check_fault("update_m3u_group_settings", data)
        for entry in data.get("group_settings") or []:
            group_id = entry.get("channel_group")
            if not group_id:
                continue
            self.group_settings[(account_id, group_id)] = {
                "channel_group": group_id,
                "enabled": entry.get("enabled", True),
                "auto_channel_sync": entry.get("auto_channel_sync", False),
                **{
                    k: entry[k]
                    for k in ("auto_sync_channel_start", "auto_sync_channel_end",
                              "custom_properties")
                    if entry.get(k) is not None
                },
            }
        return {"message": "Group settings updated successfully"}

    async def refresh_m3u_account(self, account_id: int) -> dict:
        """PROVIDER-TOUCHING. Recorded so ADR-013 S9 can be asserted, not assumed."""
        self._check_fault("refresh_m3u_account", account_id)
        self.m3u_refresh_calls.append(account_id)
        return {"success": True}

    async def patch_m3u_account(self, account_id: int, data: dict) -> dict:
        """PATCH an account — the restore path's is_active toggle, and the
        sync path's field convergence (bead ``…-zszjd``).

        MODELS ``M3UAccountSerializer.update``, read off Dispatcharr 0.29.0 on
        2026-08-23, because two of its behaviours decide whether a convergence
        test can fail at all:

        * **The four preference booleans are POPPED off the top level and written
          into ``custom_properties``** — the same asymmetry ``create`` has (see
          ``_PREFERENCE_DEFAULTS``). A fake that stored them at the top level
          would be contradicted by ``get_m3u_accounts``, which projects them
          back OUT of the blob, so the round trip has to go through the blob or
          the value never survives a read.
        * **``custom_properties`` is MERGED, not replaced**:
          ``custom_props = {**existing_custom, **incoming_custom}``. A key the
          destination holds and the source does not therefore SURVIVES a PATCH.
          That is the honest ceiling on convergence through this endpoint, and
          modelling replacement instead would let a test assert a deletion the
          real API cannot perform.

        Unlike ``update`` the booleans are only written when PRESENT — a partial
        PATCH that omits them must not reset them to the create-time defaults.
        """
        self._check_fault("patch_m3u_account", data)
        self.m3u_patch_calls.append((account_id, dict(data)))
        payload = dict(data)
        existing = self.m3u_accounts.rows.get(account_id) or {}
        existing_custom = dict(existing.get("custom_properties") or {})
        incoming_custom = dict(payload.pop("custom_properties", None) or {})
        merged_custom = {**existing_custom, **incoming_custom}
        touched_custom = bool(incoming_custom)
        for key in self._PREFERENCE_DEFAULTS:
            if key in payload:
                merged_custom[key] = payload.pop(key)
                touched_custom = True
        if touched_custom:
            payload["custom_properties"] = merged_custom
        return self.m3u_accounts.update(account_id, payload)

    # THE FOUR PREFERENCE BOOLEANS AND WHERE THEY REALLY LIVE (bead …-avrix).
    # On Dispatcharr 0.29.0 (``apps/m3u/serializers.py``, read 2026-08-22) each
    # of these is a ``write_only`` top-level serializer field whose STORAGE is
    # ``custom_properties``. The two directions are asymmetric, and modelling
    # only one of them makes a test that cannot fail:
    #
    # * ``to_representation`` PROJECTS them from the blob back onto the top
    #   level, with these defaults for an account whose blob omits them;
    # * ``create`` POPS them from the top level with these same defaults and
    #   writes them into the blob, OVERWRITING whatever the incoming
    #   ``custom_properties`` carried. So a payload that forwards the blob but
    #   drops the top-level fields silently lands on the DEFAULTS — and for the
    #   three auto-enable flags the default is ``True``, the setting that makes
    #   a replica ingest a provider's entire catalogue.
    #
    # ``enable_vod`` defaults ``False`` and the other three ``True``; that
    # asymmetry is real and is what lets a test tell a crossed value from a
    # defaulted one without contriving anything.
    _PREFERENCE_DEFAULTS = {
        "enable_vod": False,
        "auto_enable_new_groups_live": True,
        "auto_enable_new_groups_vod": True,
        "auto_enable_new_groups_series": True,
    }

    async def create_m3u_account(self, data: dict) -> dict:
        self._check_fault("create_m3u_account", data)
        payload = dict(data)
        custom = dict(payload.get("custom_properties") or {})
        for key, default in self._PREFERENCE_DEFAULTS.items():
            custom[key] = payload.pop(key, default)
        payload["custom_properties"] = custom
        return self.m3u_accounts.create(payload)

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

    async def get_epg_data(self, max_results: int = 200_000) -> list:
        return self.epg_data.list()[:max_results]

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
        # ``ChannelProfileSerializer.channels`` is the list of ENABLED channel
        # ids — re-confirmed on 0.29.0 (``get_channels`` filters
        # ``enabled=True``, and the ``enabled_memberships`` prefetch it prefers
        # carries the same filter). Absence from this list IS the exclusion.
        channel_ids = list(self.channels.rows)
        rows = []
        for row in self.channel_profiles.list():
            profile_id = row.get("id")
            row["channels"] = [
                cid
                for cid in channel_ids
                if (profile_id, cid) not in self.disabled_memberships
            ]
            rows.append(row)
        return rows

    async def create_channel_profile(self, data: dict) -> dict:
        self._check_fault("create_channel_profile", data)
        return self.channel_profiles.create(data)

    async def update_channel_profile(self, profile_id: int, data: dict) -> dict:
        self._check_fault("update_channel_profile", data)
        return self.channel_profiles.update(profile_id, data)

    async def delete_channel_profile(self, profile_id: int) -> None:
        self.channel_profiles.delete(profile_id)
        self._forget_memberships(profile_id=profile_id)

    async def update_profile_channel(
        self, profile_id: int, channel_id: int, data: dict
    ) -> dict:
        """``PATCH /api/channels/profiles/<p>/channels/<c>/`` — APPLIED.

        Models 0.29.0's ``UpdateChannelMembershipAPIView.patch``: the membership
        row is CREATED when absent, then the payload's ``enabled`` is applied;
        an unknown profile or channel is a 404 (``get_object_or_404``). It used
        to return ``{"success": True}`` and store nothing, which meant a test
        could only ever assert that the call was MADE — never what the
        destination profile ended up enabling. That is exactly the blind spot
        bead …-38c5a's defect lived in.
        """
        self._check_fault(
            "update_profile_channel",
            {"profile_id": profile_id, "channel_id": channel_id, **(data or {})},
        )
        if profile_id not in self.channel_profiles.rows:
            raise FakeNotFoundError("channel_profile", profile_id)
        if channel_id not in self.channels.rows:
            raise FakeNotFoundError("channel", channel_id)
        enabled = bool((data or {}).get("enabled", True))
        if enabled:
            self.disabled_memberships.discard((profile_id, channel_id))
        else:
            self.disabled_memberships.add((profile_id, channel_id))
        return {"channel": channel_id, "enabled": enabled}

    def _forget_memberships(
        self, *, profile_id: int | None = None, channel_id: int | None = None
    ) -> None:
        """Drop the membership exceptions a deleted row cascades away."""
        self.disabled_memberships = {
            (pid, cid)
            for (pid, cid) in self.disabled_memberships
            if not (
                (profile_id is not None and pid == profile_id)
                or (channel_id is not None and cid == channel_id)
            )
        }

    def set_membership(self, profile_id: int, channel_id: int, enabled: bool) -> None:
        """Seed one membership directly (test setup; no fault hook, no 404)."""
        if enabled:
            self.disabled_memberships.discard((profile_id, channel_id))
        else:
            self.disabled_memberships.add((profile_id, channel_id))

    def enabled_channel_names(self, profile_name: str) -> set:
        """The channel NAMES one profile ENABLES on this instance.

        The cross-instance assertion surface: A's ids and B's ids never
        coincide, so "the replica enables exactly what the source enables" can
        only be stated by name. Raises rather than returning an empty set when
        the profile is absent — an assertion that silently compares two empty
        sets is the false green this helper exists to prevent.
        """
        key = _norm_name(profile_name)
        profile_id = None
        for row_id, row in self.channel_profiles.rows.items():
            if _name_key(row) == key:
                profile_id = row_id
                break
        if profile_id is None:
            raise AssertionError(
                "no channel profile named %r on %s" % (profile_name, self.label)
            )
        return {
            str(row.get("name"))
            for row_id, row in self.channels.rows.items()
            if (profile_id, row_id) not in self.disabled_memberships
        }

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
        self._forget_memberships(channel_id=channel_id)

    # ----- users (NEVER synced, but the rollback dispatch references the
    #        compensator unconditionally) -----------------------------------

    async def delete_user(self, user_id: int) -> None:
        # Users are never synced (D3) so this is never created, hence never a
        # rollback target. It exists only because the orchestrator's
        # ``_delete_dispatch`` builds the FULL EntityType->deleter map eagerly and
        # references ``client.delete_user``. A 404 is the correct "already gone".
        raise FakeNotFoundError("user", user_id)

    # ----- user agents / DVR rules (not in the sync category set, but
    #        _delete_dispatch references their compensators eagerly — kxcjf) ---

    async def delete_user_agent(self, user_agent_id: int) -> None:
        raise FakeNotFoundError("user_agent", user_agent_id)

    async def delete_dvr_rule(self, rule_id: int) -> None:
        raise FakeNotFoundError("dvr_rule", rule_id)

    # ----- logos (bead 7ipq2.1 — the opt-in sync slice's write surface) -----

    async def get_all_logos_paginated(self, page_size: int = 500) -> list:
        return self.logos.list()

    async def create_logo(self, data: dict) -> dict:
        """``POST /api/channels/logos/`` — create a logo from a ``{name, url}``.

        The re-create-BY-URL path (``importers.logos._create_logo_from_url``).
        A logo whose ``url`` is an absolute http(s) address has no bytes to
        upload: Dispatcharr's Logo model IS ``{name, url}``, so the replica's
        row is restored by pointing at the same address. Modelling this write is
        what lets a test drive the REMOTE-url logo shape (bead …-sgrez) — on a
        real XC-sourced instance the overwhelming majority — end to end.
        """
        self._check_fault("create_logo", dict(data))
        return self.logos.create(
            {"name": data.get("name"), "url": data.get("url")}
        )

    async def upload_logo_file(
        self, name: str, filename: str, data: bytes, content_type: str
    ) -> dict:
        """Store an uploaded logo; the row carries the url the tier-3 (file)
        match reads, so a re-run matches what a previous run uploaded."""
        self._check_fault("upload_logo_file", {"name": name, "filename": filename})
        return self.logos.create(
            {"name": name, "url": "/data/logos/%s" % filename}
        )

    async def fetch_logo_image(self, logo_id: int) -> Optional[bytes]:
        """``GET /api/channels/logos/<id>/cache/`` — the hosted logo's BYTES.

        A Dispatcharr-HOSTED logo (a ``url`` naming a path inside Dispatcharr's
        own volume, which is what ECM's Logo Manager writes) has its image bytes
        NOWHERE ELSE: not in ECM's ``uploads/logos`` dir, not in the plan. Bead
        ``…-cfxml`` taught the sync gather to fetch them from here, one at a
        time, at import time (D8). Modelling that read is what lets a test drive
        the real hosted path end to end instead of the ECM-local file path,
        which is the one shape the live defect never took.
        """
        if logo_id not in self.logos.rows:
            return None
        return self.hosted_logo_bytes

    async def delete_logo(self, logo_id: int) -> None:
        # Real compensating delete (rollback target for an uploaded logo);
        # 404 (FakeNotFoundError) when already gone — the rollback's
        # 404-as-success shape.
        self.logos.delete(logo_id)

    async def bulk_delete_logos(self, logo_ids: list[int]) -> dict:
        # The DESTRUCTIVE pre-step. Recorded so tests can assert the sync path
        # NEVER invokes it (clear_existing is hard-disabled in the sync step).
        self.bulk_logo_delete_calls.append(list(logo_ids))
        for logo_id in list(logo_ids):
            if logo_id in self.logos.rows:
                del self.logos.rows[logo_id]
        return {"deleted": len(logo_ids)}

    def logo_names(self) -> set:
        """Normalized logo names on this instance — the logo convergence key."""
        return {_name_key(r) for r in self.logos.list()}

    def channel_logo_name(self, channel_name: str) -> Optional[str]:
        """The NAME of the logo one channel points at on THIS instance.

        The cross-instance assertion surface for the channel→logo BINDING (bead
        ``…-xgbjm``). A's logo ids and B's logo ids never coincide, so "the
        replica's channel carries the CORRESPONDING logo" is only sayable by
        NAME — asserting on the id would pass for a fix that forwarded A's id
        onto a B row that happens to share the number.

        ``None`` means the channel carries no logo, which is the broken state
        this helper was written to catch. Two conditions RAISE instead, because
        both are distinct failures that must never read as "no logo":

        * the channel is absent — an assertion comparing two ``None``s from two
          missing channels is the false green ``enabled_channel_names`` guards
          against for the same reason;
        * the channel's ``logo_id`` names a logo THIS instance does not have —
          a DANGLING binding, which is exactly what forwarding a source id at
          create time would produce.
        """
        row = None
        for candidate in self.channels.rows.values():
            if str(candidate.get("name")) == channel_name:
                row = candidate
                break
        if row is None:
            raise AssertionError(
                "no channel named %r on %s" % (channel_name, self.label)
            )
        logo_id = row.get("logo_id")
        if logo_id is None:
            return None
        logo = self.logos.rows.get(logo_id)
        if logo is None:
            raise AssertionError(
                "channel %r on %s points at logo id=%r, which does not exist "
                "there (a dangling binding)" % (channel_name, self.label, logo_id)
            )
        return str(logo.get("name"))

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
                self.logos,
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
        epg_password: str = "SEED-EPG-SECRET",
        with_embedded_streams: bool = False,
    ) -> "StatefulDispatcharrFake":
        """A populated source-A holding a production-shaped config + channels.

        Carries plaintext credential fields (``password`` on the M3U account,
        ``password`` on the EPG source) so the redaction end-to-end assertion is
        REAL against the fields Dispatcharr actually exposes. The EPG seed was
        ``api_key`` until bead ``…-fmtg0``; Dispatcharr REMOVED that field from
        ``EPGSource`` in its ``epg/0024`` migration and replaced it with
        ``username``/``password`` (``docs/dispatcharr_api.md`` says so, and the
        live 0.29.0 ``epg_epgsource`` table has no ``api_key`` column), so a
        fixture seeding it was asserting redaction of a field that cannot
        occur. Also seeds a config +
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
        # EPG source with a SECRET password (D2) — the credential field
        # ``EPGSourceSerializer`` actually carries.
        fake.epg_sources.create(
            {"name": "EPG One", "source_type": "xmltv", "m3u_account": None,
             "password": epg_password, "url": "http://epg-one.test/guide.xml"}
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
    *,
    credential_version: int = 1,
    fuzzy_stream_matching: bool = False,
    sync_logos: bool = False,
) -> MagicMock:
    """A fake ``SyncTarget`` row — enabled, fresh, never-insecure.

    Mirrors the shape ``run_sync`` reads (``id``/``name``/``base_url``/
    ``credentials``/``enabled``/``token_revoked_at``/``credential_version``/
    ``insecure``/``fuzzy_stream_matching``/``sync_logos``).
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
    target.sync_logos = sync_logos
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
        config_dir=None,
    ):
        self.source = source
        self.dest = dest
        self.target = target or make_sync_target(
            fuzzy_stream_matching=getattr(
                target, "fuzzy_stream_matching", False
            ) if target else False
        )
        self.freshness_reason = freshness_reason
        # Optional CONFIG_DIR override (a tmp_path): the logo slice (7ipq2.1)
        # gathers source logo FILES from <CONFIG_DIR>/uploads/logos — pointing
        # this at a tmp dir gives a test real on-disk source logos without
        # touching the real config partition. None leaves CONFIG_DIR alone
        # (safe for targets that never opt into sync_logos).
        self.config_dir = config_dir

    @contextmanager
    def _patched(self):
        """Apply the engine seams (local gather / remote factory / freshness /
        optional logo source dir)."""
        # Imported lazily so importing the harness module never drags the engine in
        # at collection time (keeps the fixture import cheap + cycle-free).
        from contextlib import ExitStack

        from routers import backup as backup_mod
        from tasks import dbas_sync_engine as engine

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(backup_mod, "get_client", return_value=self.source)
            )
            stack.enter_context(
                patch.object(engine, "make_remote_client", return_value=self.dest)
            )
            stack.enter_context(
                patch.object(
                    engine, "sync_freshness_reason", return_value=self.freshness_reason
                )
            )
            if self.config_dir is not None:
                stack.enter_context(
                    patch.object(backup_mod, "CONFIG_DIR", self.config_dir)
                )
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
