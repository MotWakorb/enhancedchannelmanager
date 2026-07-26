"""
Integration tests for the cloud-target endpoints (``routers/cloud_targets.py``).

The Export tab (playlist profiles, generate/preview/download, publish,
history) was removed (beads vrrxv / 1w428) and its cloud-target endpoints were
relocated from ``/api/export/cloud-targets`` to ``/api/cloud-targets``. The
cloud-target surface remains because DBAS backup and the ``list_cloud_targets``
MCP tool depend on it; these tests cover that surviving surface.

``TestWebDAVEndToEnd`` (bead 0i2vt.8) covers the operator-reachable WebDAV
path end to end: create via the API with REAL credential encryption →
saved-target test-connection through the REAL adapter + SSRF chokepoint
(hermetic DNS, fake transport) → upload through the REAL adapter with the
credentials decrypted from the stored row. Only DNS and the HTTP transport
are faked — crypto, router, factory, and adapter are all real.
"""
import base64
import ipaddress
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from cloud_storage import webdav_adapter as webdav_mod
from security import ssrf


def _patch_dns(*ips):
    """Patch the resolver inside security.ssrf to return ``ips`` (hermetic SSRF)."""
    return patch.object(
        ssrf, "_resolve", lambda host, port: [ipaddress.ip_address(i) for i in ips]
    )


class TestCloudTargetIntegration:
    """Test cloud target CRUD and connection testing."""

    @pytest.mark.asyncio
    @patch("routers.cloud_targets.journal")
    @patch("routers.cloud_targets.encrypt_credentials", return_value="encrypted")
    @patch("routers.cloud_targets.decrypt_credentials", return_value={"bucket_name": "test", "access_key_id": "AKIA1234"})
    async def test_create_lists_with_masked_creds(self, mock_decrypt, mock_encrypt, mock_journal, async_client):
        """Created target should appear in list with masked credentials."""
        resp = await async_client.post("/api/cloud-targets", json={
            "name": "Test S3",
            "provider_type": "s3",
            "credentials": {"bucket_name": "test", "access_key_id": "AKIA12345678"},
            "upload_path": "/exports",
        })
        assert resp.status_code == 201
        target_id = resp.json()["id"]

        resp = await async_client.get("/api/cloud-targets")
        assert resp.status_code == 200
        targets = resp.json()
        assert len(targets) == 1
        assert targets[0]["name"] == "Test S3"
        # Credentials should be masked
        creds = targets[0]["credentials"]
        assert "AKIA12345678" not in json.dumps(creds)

        # Delete
        resp = await async_client.delete(f"/api/cloud-targets/{target_id}")
        assert resp.status_code == 204

    @pytest.mark.asyncio
    @patch("routers.cloud_targets.get_adapter")
    async def test_test_connection_inline(self, mock_get_adapter, async_client):
        """Inline connection test should use provided credentials."""
        from cloud_storage import ConnectionTestResult
        mock_adapter = AsyncMock()
        mock_adapter.test_connection.return_value = ConnectionTestResult(
            success=True, message="Connected", provider_info={"bucket": "test"}
        )
        mock_get_adapter.return_value = mock_adapter

        resp = await async_client.post("/api/cloud-targets/test", json={
            "provider_type": "s3",
            "credentials": {"bucket_name": "test", "access_key_id": "key", "secret_access_key": "secret"},
        })
        assert resp.status_code == 200
        assert resp.json()["success"] is True


class TestWebDAVTlsPolicy:
    """PR #743 review item 2 (0i2vt.8): ONE TLS policy, derived from the
    top-level ``insecure`` flag only.

    Before this fix the policy was split-brain: the documented first-class
    ``target.insecure`` column was absent from create/update/UI, while an
    arbitrary ``credentials.insecure`` key could silently disable verification
    for saved/inline tests — and scheduled uploads overrode it from
    ``target.insecure``, so test behavior could differ from upload behavior.
    """

    @pytest.mark.asyncio
    @patch("routers.cloud_targets.journal")
    @patch("routers.cloud_targets.encrypt_credentials", return_value="enc")
    @patch("routers.cloud_targets.decrypt_credentials", return_value={"base_url": "https://dav.example.com/x"})
    async def test_create_persists_top_level_insecure_flag(
        self, mock_decrypt, mock_encrypt, mock_journal, async_client, test_session
    ):
        from export_models import CloudStorageTarget

        resp = await async_client.post("/api/cloud-targets", json={
            "name": "Self-signed NAS",
            "provider_type": "webdav",
            "credentials": {"base_url": "https://dav.example.com/x"},
            "insecure": True,
        })
        assert resp.status_code == 201, resp.text
        assert resp.json()["insecure"] is True
        row = test_session.query(CloudStorageTarget).filter(
            CloudStorageTarget.id == resp.json()["id"]
        ).first()
        assert row.insecure is True

        # List echoes the flag (persistence round-trip through the API).
        listed = (await async_client.get("/api/cloud-targets")).json()
        assert listed[0]["insecure"] is True

    @pytest.mark.asyncio
    @patch("routers.cloud_targets.journal")
    @patch("routers.cloud_targets.encrypt_credentials", return_value="enc")
    async def test_create_defaults_to_verified_tls(
        self, mock_encrypt, mock_journal, async_client, test_session
    ):
        from export_models import CloudStorageTarget

        resp = await async_client.post("/api/cloud-targets", json={
            "name": "Default TLS",
            "provider_type": "webdav",
            "credentials": {"base_url": "https://dav.example.com/x"},
        })
        assert resp.status_code == 201
        row = test_session.query(CloudStorageTarget).filter(
            CloudStorageTarget.id == resp.json()["id"]
        ).first()
        assert row.insecure is False

    @pytest.mark.asyncio
    @patch("routers.cloud_targets.journal")
    @patch("routers.cloud_targets.encrypt_credentials", return_value="enc")
    @patch("routers.cloud_targets.decrypt_credentials", return_value={"base_url": "https://dav.example.com/x"})
    async def test_update_top_level_insecure_flag(
        self, mock_decrypt, mock_encrypt, mock_journal, async_client, test_session
    ):
        from export_models import CloudStorageTarget

        create = await async_client.post("/api/cloud-targets", json={
            "name": "To Harden",
            "provider_type": "webdav",
            "credentials": {"base_url": "https://dav.example.com/x"},
            "insecure": True,
        })
        target_id = create.json()["id"]

        resp = await async_client.patch(
            f"/api/cloud-targets/{target_id}", json={"insecure": False}
        )
        assert resp.status_code == 200
        assert resp.json()["insecure"] is False
        test_session.expire_all()
        row = test_session.query(CloudStorageTarget).filter(
            CloudStorageTarget.id == target_id
        ).first()
        assert row.insecure is False

    @pytest.mark.asyncio
    async def test_credentials_insecure_is_rejected_at_every_request_surface(self, async_client):
        """`insecure` inside the credentials dict is RESERVED — a request smuggling
        it is refused (422) so an accidental credential key can never weaken TLS."""
        creds = {"base_url": "https://dav.example.com/x", "insecure": True}
        create = await async_client.post("/api/cloud-targets", json={
            "name": "Smuggler", "provider_type": "webdav", "credentials": creds,
        })
        assert create.status_code == 422
        assert "insecure" in create.text

        inline = await async_client.post("/api/cloud-targets/test", json={
            "provider_type": "webdav", "credentials": creds,
        })
        assert inline.status_code == 422

        with patch("routers.cloud_targets.journal"), \
             patch("routers.cloud_targets.encrypt_credentials", return_value="enc"):
            ok = await async_client.post("/api/cloud-targets", json={
                "name": "Legit", "provider_type": "webdav",
                "credentials": {"base_url": "https://dav.example.com/x"},
            })
        update = await async_client.patch(
            f"/api/cloud-targets/{ok.json()['id']}", json={"credentials": creds}
        )
        assert update.status_code == 422

    @pytest.mark.asyncio
    @patch("routers.cloud_targets.get_adapter")
    @patch("routers.cloud_targets.journal")
    @patch("routers.cloud_targets.encrypt_credentials", return_value="enc")
    async def test_saved_test_derives_tls_from_target_flag_not_stored_credentials(
        self, mock_encrypt, mock_journal, mock_get_adapter, async_client
    ):
        """Saved-test parity with scheduled uploads: the adapter's TLS policy
        comes from ``target.insecure`` — a stale ``insecure`` key inside the
        STORED credentials (legacy row) is stripped and overridden."""
        from cloud_storage import ConnectionTestResult

        mock_adapter = AsyncMock()
        mock_adapter.test_connection.return_value = ConnectionTestResult(
            success=True, message="ok", provider_info={}
        )
        mock_get_adapter.return_value = mock_adapter

        create = await async_client.post("/api/cloud-targets", json={
            "name": "Legacy Row", "provider_type": "webdav",
            "credentials": {"base_url": "https://dav.example.com/x"},
            "insecure": False,
        })
        target_id = create.json()["id"]

        # The stored row decrypts to credentials that SMUGGLE insecure=True —
        # exactly the legacy shape the review flagged. target.insecure=False
        # must win (same override the scheduled upload path applies).
        with patch(
            "routers.cloud_targets.decrypt_credentials",
            return_value={"base_url": "https://dav.example.com/x", "insecure": True},
        ):
            resp = await async_client.post(f"/api/cloud-targets/{target_id}/test")
        assert resp.status_code == 200
        adapter_creds = mock_get_adapter.call_args.args[1]
        assert adapter_creds["insecure"] is False
        # Verified TLS -> no insecure audit row.
        audit_types = [
            c.kwargs.get("action_type") for c in mock_journal.log_entry.call_args_list
        ]
        assert "cloud_test_insecure_tls" not in audit_types

    @pytest.mark.asyncio
    @patch("routers.cloud_targets.get_adapter")
    @patch("routers.cloud_targets.journal")
    @patch("routers.cloud_targets.encrypt_credentials", return_value="enc")
    @patch("routers.cloud_targets.decrypt_credentials", return_value={"base_url": "https://dav.example.com/x"})
    async def test_saved_test_with_insecure_target_wires_flag_and_audits(
        self, mock_decrypt, mock_encrypt, mock_journal, mock_get_adapter, async_client
    ):
        from cloud_storage import ConnectionTestResult

        mock_adapter = AsyncMock()
        mock_adapter.test_connection.return_value = ConnectionTestResult(
            success=True, message="ok", provider_info={}
        )
        mock_get_adapter.return_value = mock_adapter

        create = await async_client.post("/api/cloud-targets", json={
            "name": "Self-signed", "provider_type": "webdav",
            "credentials": {"base_url": "https://dav.example.com/x"},
            "insecure": True,
        })
        target_id = create.json()["id"]

        resp = await async_client.post(f"/api/cloud-targets/{target_id}/test")
        assert resp.status_code == 200
        adapter_creds = mock_get_adapter.call_args.args[1]
        assert adapter_creds["insecure"] is True
        # The insecure-TLS audit row fires on the test path the same as uploads.
        audit_calls = [
            c for c in mock_journal.log_entry.call_args_list
            if c.kwargs.get("action_type") == "cloud_test_insecure_tls"
        ]
        assert len(audit_calls) == 1
        assert audit_calls[0].kwargs.get("entity_id") == target_id

    @pytest.mark.asyncio
    @patch("routers.cloud_targets.get_adapter")
    @patch("routers.cloud_targets.journal")
    async def test_inline_test_top_level_insecure_opt_in_and_audit(
        self, mock_journal, mock_get_adapter, async_client
    ):
        from cloud_storage import ConnectionTestResult

        mock_adapter = AsyncMock()
        mock_adapter.test_connection.return_value = ConnectionTestResult(
            success=True, message="ok", provider_info={}
        )
        mock_get_adapter.return_value = mock_adapter

        # Default: verification ON, no audit row.
        resp = await async_client.post("/api/cloud-targets/test", json={
            "provider_type": "webdav",
            "credentials": {"base_url": "https://dav.example.com/x"},
        })
        assert resp.status_code == 200
        assert mock_get_adapter.call_args.args[1]["insecure"] is False
        assert not [
            c for c in mock_journal.log_entry.call_args_list
            if c.kwargs.get("action_type") == "cloud_test_insecure_tls"
        ]

        # Explicit opt-in: flag reaches the adapter AND the audit row fires.
        resp = await async_client.post("/api/cloud-targets/test", json={
            "provider_type": "webdav",
            "credentials": {"base_url": "https://dav.example.com/x"},
            "insecure": True,
        })
        assert resp.status_code == 200
        assert mock_get_adapter.call_args.args[1]["insecure"] is True
        audit_calls = [
            c for c in mock_journal.log_entry.call_args_list
            if c.kwargs.get("action_type") == "cloud_test_insecure_tls"
        ]
        assert len(audit_calls) == 1


class _FakeWebDAVClient:
    """Async-context fake for ``pinned_async_client`` — records requests.

    Answers PROPFIND (test_connection) and PUT (upload) with a 2xx response so
    the REAL WebDAVAdapter logic on either side of the transport runs.
    """

    def __init__(self):
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def _ok(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        return resp

    async def request(self, method, url, **kwargs):
        self.requests.append({"method": method, "url": url, **kwargs})
        return self._ok()

    async def put(self, url, content=None, **kwargs):
        self.requests.append({
            "method": "PUT", "url": url,
            "content_is_bytes": isinstance(content, (bytes, bytearray)),
            **kwargs,
        })
        return self._ok()


class TestWebDAVEndToEnd:
    """WebDAV is operator-reachable end to end (bead 0i2vt.8).

    Layer: API integration + real adapter — only DNS + HTTP transport faked.
    """

    @pytest.mark.asyncio
    @patch("routers.cloud_targets.journal")
    async def test_create_test_connection_upload(self, mock_journal, async_client, test_session, tmp_path):
        from cloud_storage import get_adapter
        from cloud_storage.crypto import decrypt_credentials, reset_key_cache
        from export_models import CloudStorageTarget

        password = "hunter2-webdav-secret"
        reset_key_cache()
        try:
            with patch("cloud_storage.crypto.KEY_FILE", tmp_path / ".test_key"), \
                 patch("cloud_storage.crypto.CONFIG_DIR", tmp_path):
                # --- 1. Create a webdav target via the API (REAL encryption) ---
                resp = await async_client.post("/api/cloud-targets", json={
                    "name": "NAS WebDAV",
                    "provider_type": "webdav",
                    "credentials": {
                        "base_url": "https://dav.example.com/remote.php/dav/files/ecm",
                        "username": "ecm",
                        "password": password,
                    },
                    "upload_path": "/ecm-backups",
                })
                assert resp.status_code == 201, resp.text
                created = resp.json()
                assert created["provider_type"] == "webdav"
                # Echoed credentials are masked — plaintext never comes back.
                assert password not in json.dumps(created["credentials"])
                target_id = created["id"]

                # Stored row is encrypted, not plaintext JSON.
                row = test_session.query(CloudStorageTarget).filter(
                    CloudStorageTarget.id == target_id
                ).first()
                assert password not in row.credentials

                # --- 2. Saved-target test-connection: real decrypt → real ---
                # adapter → real SSRF preresolve (hermetic DNS) → fake PROPFIND.
                fake = _FakeWebDAVClient()
                with _patch_dns("93.184.216.34"), \
                     patch.object(webdav_mod, "pinned_async_client", return_value=fake):
                    resp = await async_client.post(f"/api/cloud-targets/{target_id}/test")
                assert resp.status_code == 200
                body = resp.json()
                assert body["success"] is True, body
                propfind = fake.requests[0]
                assert propfind["method"] == "PROPFIND"
                # Connect URL is pinned to the validated IP, not the hostname.
                assert "93.184.216.34" in propfind["url"]
                # Basic auth was built from the credentials decrypted off the
                # stored row — proves the encrypt→persist→decrypt round trip.
                expected_auth = "Basic " + base64.b64encode(
                    f"ecm:{password}".encode()
                ).decode("ascii")
                assert propfind["headers"]["Authorization"] == expected_auth

                # --- 3. Upload path: stored row → decrypt → factory → real ---
                # WebDAVAdapter.upload (streamed PUT via the fake transport).
                creds = decrypt_credentials(row.credentials)
                assert creds["password"] == password
                adapter = get_adapter(row.provider_type, creds)
                artifact = tmp_path / "ecm-backup-e2e.zip"
                artifact.write_bytes(b"PK\x03\x04" + b"x" * 4096)

                fake_upload = _FakeWebDAVClient()
                with _patch_dns("93.184.216.34"), \
                     patch.object(webdav_mod, "pinned_async_client", return_value=fake_upload):
                    result = await adapter.upload(
                        artifact, f"{row.upload_path}/{artifact.name}"
                    )
                assert result.success is True, result.error
                put = fake_upload.requests[0]
                assert put["method"] == "PUT"
                # Streamed from disk, not buffered into RAM.
                assert put["content_is_bytes"] is False
                assert "93.184.216.34" in put["url"]
                assert "ecm-backup-e2e.zip" in put["url"]
                assert put["headers"]["Authorization"] == expected_auth
                assert result.file_size == artifact.stat().st_size
        finally:
            reset_key_cache()

    @pytest.mark.asyncio
    @patch("routers.cloud_targets.journal")
    async def test_update_accepts_webdav_provider_type(self, mock_journal, async_client):
        """The PATCH surface accepts provider_type=webdav (Literal parity)."""
        with patch("routers.cloud_targets.encrypt_credentials", return_value="enc"):
            create_resp = await async_client.post("/api/cloud-targets", json={
                "name": "To Repoint",
                "provider_type": "s3",
                "credentials": {"bucket_name": "b", "access_key_id": "a", "secret_access_key": "s"},
            })
        target_id = create_resp.json()["id"]

        with patch("routers.cloud_targets.encrypt_credentials", return_value="enc2"), \
             patch("routers.cloud_targets.decrypt_credentials", return_value={"base_url": "https://dav.example.com/x"}):
            resp = await async_client.patch(f"/api/cloud-targets/{target_id}", json={
                "provider_type": "webdav",
                "credentials": {"base_url": "https://dav.example.com/x"},
            })
        assert resp.status_code == 200
        assert resp.json()["provider_type"] == "webdav"

    @pytest.mark.asyncio
    async def test_inline_test_accepts_webdav_provider_type(self, async_client):
        """Inline /test accepts webdav and routes through the real gate + adapter."""
        fake = _FakeWebDAVClient()
        with _patch_dns("93.184.216.34"), \
             patch.object(webdav_mod, "pinned_async_client", return_value=fake):
            resp = await async_client.post("/api/cloud-targets/test", json={
                "provider_type": "webdav",
                "credentials": {"base_url": "https://dav.example.com/files"},
            })
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert fake.requests[0]["method"] == "PROPFIND"
