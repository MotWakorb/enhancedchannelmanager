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
