"""Router tests for SyncTarget CRUD (bead enhancedchannelmanager-vigbu, epic i39wu).

Covers, per the bead's acceptance criteria:
* CRUD happy paths (create / list / get / update / delete);
* credentials are MASKED in every response (never plaintext, never ciphertext);
* credential_version bumps when credentials change on update but NOT on a
  rename / enable-toggle;
* base_url with a non-http scheme is rejected (config-time validation);
* the admin gate is enforced when auth is enabled.

Conventions (backend/CLAUDE.md): mock at the router module level, drive through
the ``async_client`` conftest fixture, and verify persisted state via
``test_session``.
"""
import pytest

from cloud_storage.crypto import decrypt_credentials
from export_models import SyncTarget


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

class TestCreateSyncTarget:
    @pytest.mark.asyncio
    async def test_creates_target_and_masks_credentials(self, async_client, test_session):
        resp = await async_client.post(
            "/api/sync-targets",
            json={
                "name": "dispatcharr-b",
                "base_url": "https://remote.example.com",
                "credentials": {"token": "supersecrettoken12345"},
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "dispatcharr-b"
        assert body["base_url"] == "https://remote.example.com"
        assert body["enabled"] is True
        assert body["credential_version"] == 1
        # Persisted-sync-state columns default to NULL / False on a fresh row.
        assert body["last_full_sync_at"] is None
        assert body["last_outcome"] is None
        assert body["last_source_fingerprint"] is None
        assert body["fuzzy_stream_matching"] is False
        # Logo sync is OPT-IN (bead 7ipq2.1) — default OFF on a fresh target.
        assert body["sync_logos"] is False
        # Credentials masked — never the plaintext value.
        assert body["credentials"] == {"token": "***2345"}
        assert "supersecrettoken12345" not in str(body)

        # Stored ciphertext decrypts back to the original (encrypted at rest).
        row = test_session.query(SyncTarget).filter_by(name="dispatcharr-b").first()
        assert row is not None
        assert decrypt_credentials(row.credentials) == {"token": "supersecrettoken12345"}
        # Ciphertext is not the plaintext.
        assert "supersecrettoken12345" not in row.credentials

    @pytest.mark.asyncio
    async def test_duplicate_name_conflicts(self, async_client):
        payload = {"name": "dupe", "base_url": "https://a.example.com", "credentials": {}}
        first = await async_client.post("/api/sync-targets", json=payload)
        assert first.status_code == 201
        second = await async_client.post("/api/sync-targets", json=payload)
        assert second.status_code == 409

    @pytest.mark.asyncio
    async def test_non_http_scheme_rejected(self, async_client):
        resp = await async_client.post(
            "/api/sync-targets",
            json={"name": "bad", "base_url": "ftp://internal.example.com", "credentials": {}},
        )
        assert resp.status_code == 422  # pydantic field_validator -> 422

    @pytest.mark.asyncio
    async def test_unparseable_base_url_rejected(self, async_client):
        resp = await async_client.post(
            "/api/sync-targets",
            json={"name": "bad2", "base_url": "not a url at all", "credentials": {}},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_base_url_without_host_rejected(self, async_client):
        resp = await async_client.post(
            "/api/sync-targets",
            json={"name": "bad3", "base_url": "https://", "credentials": {}},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# List / Get
# ---------------------------------------------------------------------------

class TestListGetSyncTargets:
    @pytest.mark.asyncio
    async def test_list_returns_masked_targets(self, async_client):
        await async_client.post(
            "/api/sync-targets",
            json={"name": "t1", "base_url": "https://t1.example.com",
                  "credentials": {"token": "tokenvalue123456"}},
        )
        resp = await async_client.get("/api/sync-targets")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["credentials"] == {"token": "***3456"}
        assert "tokenvalue123456" not in str(items)

    @pytest.mark.asyncio
    async def test_get_single_target(self, async_client):
        created = await async_client.post(
            "/api/sync-targets",
            json={"name": "single", "base_url": "https://s.example.com",
                  "credentials": {"token": "abcdefgh12345678"}},
        )
        tid = created.json()["id"]
        resp = await async_client.get(f"/api/sync-targets/{tid}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "single"
        assert resp.json()["credentials"] == {"token": "***5678"}

    @pytest.mark.asyncio
    async def test_get_missing_returns_404(self, async_client):
        resp = await async_client.get("/api/sync-targets/99999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Update — credential_version bump semantics
# ---------------------------------------------------------------------------

class TestUpdateSyncTarget:
    @pytest.mark.asyncio
    async def test_credential_change_bumps_version(self, async_client, test_session):
        created = await async_client.post(
            "/api/sync-targets",
            json={"name": "bump", "base_url": "https://b.example.com",
                  "credentials": {"token": "originaltoken123"}},
        )
        tid = created.json()["id"]
        assert created.json()["credential_version"] == 1

        resp = await async_client.put(
            f"/api/sync-targets/{tid}",
            json={"credentials": {"token": "rotatedtoken4567"}},
        )
        assert resp.status_code == 200
        assert resp.json()["credential_version"] == 2
        assert resp.json()["credentials"] == {"token": "***4567"}

        test_session.expire_all()
        row = test_session.query(SyncTarget).filter_by(id=tid).first()
        assert row.credential_version == 2
        assert decrypt_credentials(row.credentials) == {"token": "rotatedtoken4567"}

    @pytest.mark.asyncio
    async def test_rename_does_not_bump_version(self, async_client):
        created = await async_client.post(
            "/api/sync-targets",
            json={"name": "rename-me", "base_url": "https://r.example.com",
                  "credentials": {"token": "stabletoken12345"}},
        )
        tid = created.json()["id"]

        resp = await async_client.put(f"/api/sync-targets/{tid}", json={"name": "renamed"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "renamed"
        # No credentials write -> version unchanged.
        assert resp.json()["credential_version"] == 1

    @pytest.mark.asyncio
    async def test_enable_toggle_does_not_bump_version(self, async_client):
        created = await async_client.post(
            "/api/sync-targets",
            json={"name": "toggle", "base_url": "https://tg.example.com",
                  "credentials": {"token": "anothertoken1234"}},
        )
        tid = created.json()["id"]

        resp = await async_client.put(f"/api/sync-targets/{tid}", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        assert resp.json()["credential_version"] == 1

    @pytest.mark.asyncio
    async def test_update_fuzzy_and_insecure_flags(self, async_client):
        created = await async_client.post(
            "/api/sync-targets",
            json={"name": "flags", "base_url": "https://f.example.com", "credentials": {}},
        )
        tid = created.json()["id"]
        resp = await async_client.put(
            f"/api/sync-targets/{tid}",
            json={"fuzzy_stream_matching": True, "insecure": True, "sync_logos": True},
        )
        assert resp.status_code == 200
        assert resp.json()["fuzzy_stream_matching"] is True
        assert resp.json()["insecure"] is True
        assert resp.json()["sync_logos"] is True
        assert resp.json()["credential_version"] == 1  # metadata-only

    @pytest.mark.asyncio
    async def test_correction_leaves_the_row_settings_alone(self, async_client, test_session):
        """A base_url + credentials correction must not disturb sync_logos/enabled.

        Bead ``…-a3lby``. Correcting a mistyped base URL or password used to mean
        DELETE AND RECREATE, which reset ``sync_logos`` to its default OFF (the
        control bead ``…-8gnik`` shipped) and handed the replacement the deleted
        target's execution history, because that history is keyed on a REUSABLE
        target id (bead ``…-5dp92``).

        THE INVARIANT this pins (the specification; base_url and credentials are
        examples of it): any field an operator can set at creation can be
        corrected afterwards without destroying the target, and correcting one
        must leave the settings they set elsewhere exactly where they were.

        The mechanism is that :class:`SyncTargetUpdate` is a PARTIAL update whose
        unnamed fields are left untouched — so an unconditional write of any of
        them would reintroduce the reset through the correction path. That is the
        mutant this test is red against.
        """
        created = await async_client.post(
            "/api/sync-targets",
            json={
                "name": "typo",
                "base_url": "https://b.exmaple.com",
                "credentials": {"username": "admin", "password": "wrongpassword1"},
                "enabled": False,
                "sync_logos": True,
                "fuzzy_stream_matching": True,
            },
        )
        tid = created.json()["id"]

        resp = await async_client.put(
            f"/api/sync-targets/{tid}",
            json={
                "name": "corrected",
                "base_url": "https://b.example.com",
                "insecure": False,
                "credentials": {"username": "admin", "password": "rightpassword1"},
            },
        )
        assert resp.status_code == 200
        body = resp.json()

        # The correction landed...
        assert body["name"] == "corrected"
        assert body["base_url"] == "https://b.example.com"
        assert body["credential_version"] == 2

        # ...and nothing the operator set on the row moved with it.
        assert body["sync_logos"] is True
        assert body["enabled"] is False
        assert body["fuzzy_stream_matching"] is True

        test_session.expire_all()
        row = test_session.query(SyncTarget).filter_by(id=tid).first()
        assert row.base_url == "https://b.example.com"
        assert row.sync_logos is True
        assert row.enabled is False
        assert row.fuzzy_stream_matching is True
        assert decrypt_credentials(row.credentials) == {
            "username": "admin",
            "password": "rightpassword1",
        }

    @pytest.mark.asyncio
    async def test_credentials_omitted_leaves_the_stored_secret_untouched(
        self, async_client, test_session
    ):
        """Correcting only the base_url must not touch the stored credentials.

        Bead ``…-a3lby``: the UI leaves its credential boxes blank to mean "keep
        what is stored", which only works because an omitted ``credentials`` is
        not written. A partial write here would be worse than a no-op — the
        backend REPLACES the dict rather than merging into it.
        """
        created = await async_client.post(
            "/api/sync-targets",
            json={
                "name": "keep-creds",
                "base_url": "https://k.exmaple.com",
                "credentials": {"username": "admin", "password": "originalpass12"},
            },
        )
        tid = created.json()["id"]

        resp = await async_client.put(
            f"/api/sync-targets/{tid}", json={"base_url": "https://k.example.com"}
        )
        assert resp.status_code == 200
        assert resp.json()["base_url"] == "https://k.example.com"
        assert resp.json()["credential_version"] == 1  # no credentials write

        test_session.expire_all()
        row = test_session.query(SyncTarget).filter_by(id=tid).first()
        assert decrypt_credentials(row.credentials) == {
            "username": "admin",
            "password": "originalpass12",
        }

    @pytest.mark.asyncio
    async def test_update_rejects_non_http_base_url(self, async_client):
        created = await async_client.post(
            "/api/sync-targets",
            json={"name": "url-update", "base_url": "https://u.example.com", "credentials": {}},
        )
        tid = created.json()["id"]
        resp = await async_client.put(
            f"/api/sync-targets/{tid}", json={"base_url": "file:///etc/passwd"}
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_missing_returns_404(self, async_client):
        resp = await async_client.put("/api/sync-targets/99999", json={"enabled": False})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

class TestDeleteSyncTarget:
    @pytest.mark.asyncio
    async def test_delete_removes_target(self, async_client, test_session):
        created = await async_client.post(
            "/api/sync-targets",
            json={"name": "to-delete", "base_url": "https://d.example.com", "credentials": {}},
        )
        tid = created.json()["id"]
        resp = await async_client.delete(f"/api/sync-targets/{tid}")
        assert resp.status_code == 204
        assert test_session.query(SyncTarget).filter_by(id=tid).first() is None

    @pytest.mark.asyncio
    async def test_delete_missing_returns_404(self, async_client):
        resp = await async_client.delete("/api/sync-targets/99999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Admin gate
# ---------------------------------------------------------------------------

class TestSyncTargetAdminGate:
    """Every write/read endpoint is admin-gated when auth is enabled. With auth
    disabled (the default test posture), the gate passes through (anonymous)."""

    @pytest.mark.asyncio
    async def test_non_admin_is_forbidden_when_auth_enabled(self, async_client):
        from fastapi import HTTPException, status
        from main import app
        from auth import RequireAdminIfEnabled as _prebuilt

        async def _reject() -> None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required",
            )

        app.dependency_overrides[_prebuilt.dependency] = _reject
        try:
            resp = await async_client.post(
                "/api/sync-targets",
                json={"name": "blocked", "base_url": "https://x.example.com", "credentials": {}},
            )
        finally:
            app.dependency_overrides.pop(_prebuilt.dependency, None)

        assert resp.status_code == 403
        assert "admin" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_anonymous_allowed_when_auth_disabled(self, async_client):
        # Default test posture: auth disabled -> gate passes through.
        resp = await async_client.get("/api/sync-targets")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# One-time credential provisioning (bead wd20y — ADR-013 S10-S13)
# ---------------------------------------------------------------------------

class _ProvClient:
    """A destination that records what the provisioning routes wrote to it."""

    def __init__(self, fail=False):
        self.fail = fail
        self.writes: list[tuple[int, dict]] = []

    async def get_m3u_accounts(self):
        return [{"id": 101, "name": "Provider XC"}]

    async def get_epg_sources(self):
        return []

    async def patch_m3u_account(self, account_id, data):
        if self.fail:
            raise RuntimeError("destination refused")
        self.writes.append((account_id, data))
        return {"id": account_id}

    async def update_epg_source(self, source_id, data):  # pragma: no cover
        return {"id": source_id}

    async def close(self):
        return None


_SOURCE_SECTIONS = {
    "m3u_accounts": [
        {
            "id": 1,
            "name": "Provider XC",
            "account_type": "XC",
            "server_url": "http://xc.example.com",
            "username": "provider-user",
            "password": "provider-pass-4471",
        }
    ],
    "epg_sources": [],
}


@pytest.fixture
def provisioning_seams(monkeypatch):
    """Patch the WRITER's two seams (the local gather + the remote client).

    Patched on ``tasks.dbas_sync_provisioning``, not on the router: the router
    holds no client and no gather, deliberately — the writer lives under
    ``backend/tasks/`` so the SSRF chokepoint guard's ``dbas_sync*.py`` glob
    covers it (threat model §11.5.4 item 2).
    """
    from unittest.mock import AsyncMock

    import tasks.dbas_sync_provisioning as prov

    client = _ProvClient()
    monkeypatch.setattr(
        prov, "_gather_dispatcharr_sections", AsyncMock(return_value=_SOURCE_SECTIONS)
    )
    monkeypatch.setattr(prov, "make_remote_client", lambda target: client)
    return client


async def _make_target(async_client, **over):
    payload = {
        "name": over.pop("name", "standby-b"),
        "base_url": "https://b.example.com",
        "credentials": {"token": "sync-target-token-1234"},
    }
    payload.update(over)
    resp = await async_client.post("/api/sync-targets", json=payload)
    assert resp.status_code == 201
    return resp.json()


class TestProvisionCredentialsRoute:
    @pytest.mark.asyncio
    async def test_provisioning_writes_to_B_and_records_the_marker(
        self, async_client, test_session, provisioning_seams
    ):
        target = await _make_target(async_client)
        resp = await async_client.post(
            f"/api/sync-targets/{target['id']}/provision-credentials", json={}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["succeeded"] is True
        assert body["accounts_written"] == 1
        assert sorted(body["fields_written"]) == ["password", "username"]
        assert provisioning_seams.writes == [
            (101, {"username": "provider-user", "password": "provider-pass-4471"})
        ]

        row = test_session.query(SyncTarget).filter_by(id=target["id"]).first()
        assert row.credentials_provisioned_at is not None

    @pytest.mark.asyncio
    async def test_the_response_carries_no_credential_value(
        self, async_client, provisioning_seams
    ):
        target = await _make_target(async_client)
        resp = await async_client.post(
            f"/api/sync-targets/{target['id']}/provision-credentials", json={}
        )
        assert "provider-pass-4471" not in resp.text
        assert "provider-user" not in resp.text

    @pytest.mark.asyncio
    async def test_a_destination_failure_is_502_and_leaves_the_marker_unset(
        self, async_client, test_session, monkeypatch
    ):
        from unittest.mock import AsyncMock

        import tasks.dbas_sync_provisioning as prov

        monkeypatch.setattr(
            prov,
            "_gather_dispatcharr_sections",
            AsyncMock(return_value=_SOURCE_SECTIONS),
        )
        monkeypatch.setattr(prov, "make_remote_client", lambda t: _ProvClient(fail=True))
        target = await _make_target(async_client)
        resp = await async_client.post(
            f"/api/sync-targets/{target['id']}/provision-credentials", json={}
        )
        assert resp.status_code == 502
        detail = resp.json()["detail"]
        assert detail["succeeded"] is False
        assert detail["failed"][0]["name"] == "Provider XC"

        row = test_session.query(SyncTarget).filter_by(id=target["id"]).first()
        assert row.credentials_provisioned_at is None

    @pytest.mark.asyncio
    async def test_missing_target_is_404(self, async_client):
        resp = await async_client.post(
            "/api/sync-targets/99999/provision-credentials", json={}
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_the_marker_is_exposed_on_the_read_shape(
        self, async_client, provisioning_seams
    ):
        target = await _make_target(async_client)
        before = await async_client.get(f"/api/sync-targets/{target['id']}")
        assert before.json()["credentials_provisioned_at"] is None
        await async_client.post(
            f"/api/sync-targets/{target['id']}/provision-credentials", json={}
        )
        after = await async_client.get(f"/api/sync-targets/{target['id']}")
        assert after.json()["credentials_provisioned_at"] is not None


class TestInsecureGateAtTheServiceLayer:
    """INV-4: refused symmetrically, in BOTH orderings, on every surface.

    A UI guard satisfies neither surface — the MCP ``update_sync_target`` tool
    calls this same route, and ``insecure`` has been editable on
    ``PUT /api/sync-targets/{id}`` since the router's first commit.
    """

    @pytest.mark.asyncio
    async def test_ordering_one_provision_then_enable_insecure(
        self, async_client, test_session, provisioning_seams
    ):
        target = await _make_target(async_client)
        await async_client.post(
            f"/api/sync-targets/{target['id']}/provision-credentials", json={}
        )
        resp = await async_client.put(
            f"/api/sync-targets/{target['id']}", json={"insecure": True}
        )
        assert resp.status_code == 409
        assert "TLS verification" in resp.json()["detail"]

        row = test_session.query(SyncTarget).filter_by(id=target["id"]).first()
        assert row.insecure is False, "a refused write must leave the row untouched"

    @pytest.mark.asyncio
    async def test_ordering_two_insecure_then_provision(
        self, async_client, provisioning_seams
    ):
        target = await _make_target(async_client, insecure=True)
        resp = await async_client.post(
            f"/api/sync-targets/{target['id']}/provision-credentials", json={}
        )
        assert resp.status_code == 409
        assert "must never cross an unverified connection" in resp.json()["detail"]
        assert provisioning_seams.writes == [], (
            "a refused provisioning must not reach the destination at all"
        )

    @pytest.mark.asyncio
    async def test_clearing_insecure_is_always_allowed_even_when_provisioned(
        self, async_client, test_session, provisioning_seams
    ):
        target = await _make_target(async_client)
        await async_client.post(
            f"/api/sync-targets/{target['id']}/provision-credentials", json={}
        )
        resp = await async_client.put(
            f"/api/sync-targets/{target['id']}", json={"insecure": False}
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_an_OBSERVED_credential_refuses_insecure_with_no_marker(
        self, async_client, test_session
    ):
        """The credential ECM did NOT write — row D16, reachable today.

        No provisioning has ever run, so ``credentials_provisioned_at`` is NULL.
        A cycle observed a credential on the replica's own account rows, which is
        what the operator's by-hand recovery leaves behind.
        """
        from datetime import datetime, timezone

        target = await _make_target(async_client)
        row = test_session.query(SyncTarget).filter_by(id=target["id"]).first()
        row.destination_credential_observed_at = datetime.now(timezone.utc)
        test_session.commit()

        resp = await async_client.put(
            f"/api/sync-targets/{target['id']}", json={"insecure": True}
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert "did not write" in detail
        assert "cannot de-provision what it did not provision" in detail

    @pytest.mark.asyncio
    async def test_the_gate_applies_to_the_MCP_surface_too(
        self, async_client, test_session, provisioning_seams
    ):
        """The MCP tools call these same routes, so one predicate covers both.

        Asserted by driving the route as the MCP principal resolves it, rather
        than by asserting a shared function exists — a shared function that the
        MCP path did not reach would pass the second check and fail the first.
        """
        from main import app
        from auth import ResolveIsMcpServicePrincipalIfEnabled as _mcp

        target = await _make_target(async_client)
        await async_client.post(
            f"/api/sync-targets/{target['id']}/provision-credentials", json={}
        )

        async def _is_mcp() -> bool:
            return True

        app.dependency_overrides[_mcp.dependency] = _is_mcp
        try:
            resp = await async_client.put(
                f"/api/sync-targets/{target['id']}", json={"insecure": True}
            )
        finally:
            app.dependency_overrides.pop(_mcp.dependency, None)
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_an_unprovisioned_target_may_still_set_insecure(self, async_client):
        """The default posture is unchanged for a target that holds nothing."""
        target = await _make_target(async_client)
        resp = await async_client.put(
            f"/api/sync-targets/{target['id']}", json={"insecure": True}
        )
        assert resp.status_code == 200
        assert resp.json()["insecure"] is True


class TestDeprovisionCredentialsRoute:
    @pytest.mark.asyncio
    async def test_a_successful_deprovision_clears_B_then_the_marker(
        self, async_client, test_session, provisioning_seams
    ):
        target = await _make_target(async_client)
        await async_client.post(
            f"/api/sync-targets/{target['id']}/provision-credentials", json={}
        )
        resp = await async_client.post(
            f"/api/sync-targets/{target['id']}/deprovision-credentials"
        )
        assert resp.status_code == 200
        assert provisioning_seams.writes[-1] == (101, {"username": "", "password": ""})
        row = test_session.query(SyncTarget).filter_by(id=target["id"]).first()
        assert row.credentials_provisioned_at is None

    @pytest.mark.asyncio
    async def test_deprovision_always_states_what_it_cannot_guarantee(
        self, async_client, provisioning_seams
    ):
        target = await _make_target(async_client)
        await async_client.post(
            f"/api/sync-targets/{target['id']}/provision-credentials", json={}
        )
        resp = await async_client.post(
            f"/api/sync-targets/{target['id']}/deprovision-credentials"
        )
        assert "NOT revocation" in resp.json()["residual_statement"]

    @pytest.mark.asyncio
    async def test_a_failed_deprovision_is_502_and_insecure_stays_refused(
        self, async_client, test_session, monkeypatch, provisioning_seams
    ):
        """INV-9 end to end, through the surface the operator actually uses."""
        import tasks.dbas_sync_provisioning as prov

        target = await _make_target(async_client)
        await async_client.post(
            f"/api/sync-targets/{target['id']}/provision-credentials", json={}
        )

        monkeypatch.setattr(prov, "make_remote_client", lambda t: _ProvClient(fail=True))
        resp = await async_client.post(
            f"/api/sync-targets/{target['id']}/deprovision-credentials"
        )
        assert resp.status_code == 502
        assert resp.json()["detail"]["failed"][0]["name"] == "Provider XC"

        row = test_session.query(SyncTarget).filter_by(id=target["id"]).first()
        assert row.credentials_provisioned_at is not None

        blocked = await async_client.put(
            f"/api/sync-targets/{target['id']}", json={"insecure": True}
        )
        assert blocked.status_code == 409

    @pytest.mark.asyncio
    async def test_deprovision_on_a_missing_target_is_404(self, async_client):
        resp = await async_client.post(
            "/api/sync-targets/99999/deprovision-credentials"
        )
        assert resp.status_code == 404
