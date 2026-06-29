"""
Tests for cloud storage framework: adapters, crypto, and cloud target API.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.fernet import Fernet

from cloud_storage import get_adapter, ConnectionTestResult
from cloud_storage.crypto import encrypt_credentials, decrypt_credentials, reset_key_cache


# =============================================================================
# Adapter factory
# =============================================================================


class TestAdapterFactory:
    def test_returns_s3_adapter(self):
        adapter = get_adapter("s3", {
            "bucket_name": "test", "access_key_id": "ak", "secret_access_key": "sk"
        })
        from cloud_storage.s3_adapter import S3Adapter
        assert isinstance(adapter, S3Adapter)

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown"):
            get_adapter("ftp", {})


# =============================================================================
# S3 Adapter
# =============================================================================


class TestS3Adapter:
    def _make_adapter(self, **overrides):
        creds = {
            "bucket_name": "my-bucket",
            "access_key_id": "AKID",
            "secret_access_key": "SECRET",
        }
        creds.update(overrides)
        return get_adapter("s3", creds)

    @pytest.mark.asyncio
    async def test_upload_success(self, tmp_path):
        adapter = self._make_adapter()
        test_file = tmp_path / "test.m3u"
        test_file.write_text("#EXTM3U\n")

        mock_client = MagicMock()
        with patch.object(adapter, "_get_client", return_value=mock_client):
            result = await adapter.upload(test_file, "exports/test.m3u")

        assert result.success is True
        assert result.file_size > 0
        assert "my-bucket" in result.remote_url
        mock_client.upload_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_failure(self, tmp_path):
        adapter = self._make_adapter()
        test_file = tmp_path / "test.m3u"
        test_file.write_text("#EXTM3U\n")

        mock_client = MagicMock()
        mock_client.upload_file.side_effect = Exception("Access denied")
        with patch.object(adapter, "_get_client", return_value=mock_client):
            result = await adapter.upload(test_file, "exports/test.m3u")

        assert result.success is False
        assert "Access denied" in result.error

    @pytest.mark.asyncio
    async def test_test_connection_success(self):
        adapter = self._make_adapter()
        mock_client = MagicMock()
        mock_client.get_bucket_location.return_value = {"LocationConstraint": "us-west-2"}
        with patch.object(adapter, "_get_client", return_value=mock_client):
            result = await adapter.test_connection()

        assert result.success is True
        assert result.provider_info["bucket"] == "my-bucket"

    @pytest.mark.asyncio
    async def test_test_connection_failure(self):
        adapter = self._make_adapter()
        mock_client = MagicMock()
        mock_client.head_bucket.side_effect = Exception("Bucket not found")
        with patch.object(adapter, "_get_client", return_value=mock_client):
            result = await adapter.test_connection()

        assert result.success is False

    @pytest.mark.asyncio
    async def test_delete(self):
        adapter = self._make_adapter()
        mock_client = MagicMock()
        with patch.object(adapter, "_get_client", return_value=mock_client):
            ok = await adapter.delete("exports/old.m3u")
        assert ok is True
        mock_client.delete_object.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_files(self):
        adapter = self._make_adapter()
        mock_client = MagicMock()
        mock_client.list_objects_v2.return_value = {
            "Contents": [{"Key": "a.m3u"}, {"Key": "b.xml"}]
        }
        with patch.object(adapter, "_get_client", return_value=mock_client):
            files = await adapter.list_files("exports/")
        assert files == ["a.m3u", "b.xml"]

    @pytest.mark.asyncio
    async def test_list_files_paginates_beyond_1000(self):
        """list_objects_v2 caps a single response at 1000 keys; list_files must
        follow the ContinuationToken so retention sees the FULL keyset (bead
        0i2vt.9). A two-page response returns all keys from both pages."""
        adapter = self._make_adapter()
        mock_client = MagicMock()
        page1 = {
            "Contents": [{"Key": "k%04d" % i} for i in range(1000)],
            "IsTruncated": True,
            "NextContinuationToken": "TOKEN-1",
        }
        page2 = {
            "Contents": [{"Key": "k%04d" % i} for i in range(1000, 1500)],
            "IsTruncated": False,
        }
        mock_client.list_objects_v2.side_effect = [page1, page2]
        with patch.object(adapter, "_get_client", return_value=mock_client):
            files = await adapter.list_files("backups/")

        assert len(files) == 1500
        assert files[0] == "k0000"
        assert files[-1] == "k1499"
        # Second call must carry the continuation token from page 1.
        second_call = mock_client.list_objects_v2.call_args_list[1]
        assert second_call.kwargs.get("ContinuationToken") == "TOKEN-1"

    @pytest.mark.asyncio
    async def test_custom_endpoint(self):
        adapter = self._make_adapter(endpoint_url="https://minio.local:9000")
        assert adapter.endpoint_url == "https://minio.local:9000"


# =============================================================================
# OneDrive Adapter (httpx-based, no extra deps)
# =============================================================================


class TestOneDriveAdapter:
    def _make_adapter(self):
        return get_adapter("onedrive", {
            "client_id": "cid", "client_secret": "csec", "tenant_id": "tid",
        })

    @pytest.mark.asyncio
    async def test_test_connection_success(self):
        adapter = self._make_adapter()

        mock_resp_token = MagicMock()
        mock_resp_token.json.return_value = {"access_token": "tok123"}
        mock_resp_token.raise_for_status = MagicMock()

        mock_resp_root = MagicMock()
        mock_resp_root.json.return_value = {"name": "My Drive"}
        mock_resp_root.raise_for_status = MagicMock()

        with patch("cloud_storage.onedrive_adapter.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp_token
            mock_client.get.return_value = mock_resp_root
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await adapter.test_connection()

        assert result.success is True
        assert "My Drive" in result.message


# =============================================================================
# GDrive adapter — token_uri SSRF chokepoint (SEC-1, bead ...-uomwu)
# =============================================================================


class TestGDriveTokenUriSSRF:
    """The service-account ``token_uri`` is operator-supplied and is hit by
    google-auth during the token exchange. It MUST be routed through the .5 SSRF
    chokepoint in ``_validate_endpoint``, BEFORE any service/socket is built —
    otherwise a malicious SA JSON with token_uri pointing at the cloud-metadata
    endpoint triggers an unvalidated outbound request (SEC-1)."""

    def _adapter(self, token_uri):
        sa_info = {
            "type": "service_account",
            "client_email": "svc@example.iam.gserviceaccount.com",
            "private_key": "-----BEGIN PRIVATE KEY-----\\nx\\n-----END PRIVATE KEY-----\\n",
        }
        if token_uri is not None:
            sa_info["token_uri"] = token_uri
        return get_adapter("gdrive", {
            "service_account_json": json.dumps(sa_info),
            "folder_id": "folder123",
        })

    def test_validate_endpoint_rejects_imds_token_uri(self):
        """A token_uri pointing at the cloud-metadata IP (169.254.169.254) is
        denied by the chokepoint before any service is built."""
        from cloud_storage.upload_security import SSRFError

        adapter = self._adapter("http://169.254.169.254/computeMetadata/v1/token")
        with pytest.raises(SSRFError):
            adapter._validate_endpoint()

    def test_validate_endpoint_rejects_imds_token_uri_no_service_built(self):
        """The SSRF refusal happens BEFORE _get_service — no Google client is
        ever constructed for a malicious token_uri."""
        from cloud_storage.upload_security import SSRFError

        adapter = self._adapter("http://169.254.169.254/token")
        with patch.object(adapter, "_get_service") as mock_get_service:
            with pytest.raises(SSRFError):
                adapter._validate_endpoint()
        mock_get_service.assert_not_called()

    @pytest.mark.asyncio
    async def test_upload_refuses_malicious_token_uri(self):
        """End-to-end: upload() surfaces a masked failure (never raises) and
        never builds the service for a metadata-pointing token_uri."""
        adapter = self._adapter("http://169.254.169.254/token")
        with patch.object(adapter, "_get_service") as mock_get_service:
            from pathlib import Path
            result = await adapter.upload(Path("/nonexistent"), "exports/x.zip")
        assert result.success is False
        mock_get_service.assert_not_called()

    def test_validate_endpoint_allows_default_google_token_uri(self):
        """A normal SA JSON (well-known Google token_uri, or none) passes the
        chokepoint — the legitimate path is not broken."""
        # Explicit well-known token_uri.
        self._adapter("https://oauth2.googleapis.com/token")._validate_endpoint()
        # Omitted token_uri -> falls back to the well-known default.
        self._adapter(None)._validate_endpoint()


# =============================================================================
# Deferred providers gated at the config-UI test surface (SEC-4, bead ...-uomwu)
# =============================================================================


class TestDeferredProviderGate:
    """OneDrive/Dropbox adapters are DEFERRED (not routed through the SSRF
    chokepoint). The config-UI Test-Connection surface must not exercise them."""

    def test_supported_providers_excludes_deferred(self):
        from cloud_storage import SUPPORTED_PROVIDERS

        assert "s3" in SUPPORTED_PROVIDERS
        assert "gdrive" in SUPPORTED_PROVIDERS
        assert "webdav" in SUPPORTED_PROVIDERS
        assert "dropbox" not in SUPPORTED_PROVIDERS
        assert "onedrive" not in SUPPORTED_PROVIDERS

    @pytest.mark.asyncio
    async def test_inline_test_refuses_deferred_dropbox(self, async_client):
        """A 'Test Connection' for a deferred provider returns a non-silent
        'not supported' result without building/exercising the adapter."""
        with patch("routers.export.get_adapter") as mock_get_adapter:
            response = await async_client.post("/api/export/cloud-targets/test", json={
                "provider_type": "dropbox",
                "credentials": {"access_token": "tok"},
            })
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert "not supported" in body["message"].lower()
        mock_get_adapter.assert_not_called()


# =============================================================================
# Credential encryption
# =============================================================================


class TestCrypto:
    def test_round_trip(self, tmp_path):
        reset_key_cache()
        with patch("cloud_storage.crypto.KEY_FILE", tmp_path / ".test_key"):
            with patch("cloud_storage.crypto.CONFIG_DIR", tmp_path):
                original = {"access_key": "AKID123", "secret": "supersecret"}
                encrypted = encrypt_credentials(original)
                assert encrypted != json.dumps(original)  # Not plaintext
                decrypted = decrypt_credentials(encrypted)
                assert decrypted == original
                reset_key_cache()

    def test_key_persists(self, tmp_path):
        reset_key_cache()
        key_file = tmp_path / ".test_key"
        with patch("cloud_storage.crypto.KEY_FILE", key_file):
            with patch("cloud_storage.crypto.CONFIG_DIR", tmp_path):
                data = {"key": "value"}
                encrypted = encrypt_credentials(data)
                assert key_file.exists()

                # Reset and decrypt with persisted key
                reset_key_cache()
                decrypted = decrypt_credentials(encrypted)
                assert decrypted == data
                reset_key_cache()


# =============================================================================
# Key bootstrap integrity (mode 0600 + ownership) — bead 0i2vt.2
# =============================================================================


def _fake_owner_stat(target_path, fake_uid):
    """Patch Path.stat so `target_path` reports a foreign owner uid.

    Returns a patch context manager. The real mode bits are preserved (so the
    mode-repair branch behaves normally); only st_uid is overridden, and only
    for the target path — every other Path.stat() call delegates to the real
    implementation so chmod/replace/tempfile internals are unaffected.
    """
    import os
    import pathlib

    real_stat = pathlib.Path.stat
    target_str = str(target_path)

    class _FakeStatResult:
        def __init__(self, src):
            self._src = src

        def __getattr__(self, name):
            if name == "st_uid":
                return fake_uid
            return getattr(self._src, name)

    def _stat_side_effect(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        if str(self) == target_str:
            return _FakeStatResult(result)
        return result

    return patch.object(pathlib.Path, "stat", _stat_side_effect)


class TestKeyIntegrity:
    """Verify the Fernet key bootstrap integrity check.

    Decisions under test (PO-resolved, bead 0i2vt.2):
      - Wrong MODE -> repair to 0600 on load.
      - Existing key with live ciphertext + drifted mode -> repaired, NOT
        regenerated, ciphertext still decrypts.
      - Wrong OWNER + unfixable (non-root) -> fail-closed, no regenerate/thrash.
      - No key (first boot) -> generate 0600 atomically (no world-readable window).
      - Decrypt with broken key -> fail-soft (returns None, never raises).
    """

    def _patched(self, tmp_path):
        """Context managers patching KEY_FILE + CONFIG_DIR to a tmp location."""
        key_file = tmp_path / ".export_key"
        return key_file

    def test_existing_key_mode_repaired_to_0600(self, tmp_path):
        import os
        import stat as stat_mod
        from cloud_storage import crypto

        reset_key_cache()
        key_file = tmp_path / ".export_key"
        # Pre-create a valid key with a drifted (world/group-readable) mode.
        key_file.write_bytes(Fernet.generate_key())
        key_file.chmod(0o644)
        assert stat_mod.S_IMODE(os.stat(key_file).st_mode) == 0o644

        with patch("cloud_storage.crypto.KEY_FILE", key_file):
            with patch("cloud_storage.crypto.CONFIG_DIR", tmp_path):
                crypto._load_or_generate_key()

        assert stat_mod.S_IMODE(os.stat(key_file).st_mode) == 0o600
        reset_key_cache()

    def test_drifted_mode_with_live_ciphertext_repairs_not_regenerates(self, tmp_path):
        """The key MUST NOT be regenerated — existing ciphertext must still decrypt."""
        import os
        import stat as stat_mod
        from cloud_storage import crypto

        reset_key_cache()
        key_file = tmp_path / ".export_key"

        with patch("cloud_storage.crypto.KEY_FILE", key_file):
            with patch("cloud_storage.crypto.CONFIG_DIR", tmp_path):
                # First boot: generate a key and encrypt live credentials.
                original = {"access_key": "AKID123", "secret": "supersecret"}
                encrypted = encrypt_credentials(original)
                key_bytes_before = key_file.read_bytes()

                # Simulate a filesystem permission regression.
                key_file.chmod(0o644)
                reset_key_cache()

                # Reload: mode must be repaired, key must NOT change.
                decrypted = decrypt_credentials(encrypted)

        assert stat_mod.S_IMODE(os.stat(key_file).st_mode) == 0o600
        assert key_file.read_bytes() == key_bytes_before, "key was regenerated — data loss!"
        assert decrypted == original, "live ciphertext no longer decrypts after repair"
        reset_key_cache()

    def test_wrong_owner_unfixable_non_root_fails_closed(self, tmp_path):
        """Non-root process + key owned by another uid -> fail-closed, no regen."""
        import os
        from cloud_storage import crypto

        reset_key_cache()
        key_file = tmp_path / ".export_key"
        key_file.write_bytes(Fernet.generate_key())
        key_file.chmod(0o600)
        key_bytes_before = key_file.read_bytes()

        fake_uid = os.stat(key_file).st_uid + 4242  # an owner that is not us
        fake_stat = _fake_owner_stat(key_file, fake_uid)

        with patch("cloud_storage.crypto.KEY_FILE", key_file):
            with patch("cloud_storage.crypto.CONFIG_DIR", tmp_path):
                # Force a non-root process context and a foreign owner.
                with patch("cloud_storage.crypto.os.getuid", return_value=1000):
                    with fake_stat:
                        with pytest.raises(RuntimeError, match="ownership"):
                            crypto._load_or_generate_key()

        # Must NOT have regenerated / thrashed the key.
        assert key_file.read_bytes() == key_bytes_before
        reset_key_cache()

    def test_wrong_owner_root_context_does_not_thrash(self, tmp_path):
        """Root process + key owned by another uid -> proceed (ownership advisory)."""
        import os
        from cloud_storage import crypto

        reset_key_cache()
        key_file = tmp_path / ".export_key"
        key_file.write_bytes(Fernet.generate_key())
        key_file.chmod(0o600)
        key_bytes_before = key_file.read_bytes()

        fake_uid = os.stat(key_file).st_uid + 4242
        fake_stat = _fake_owner_stat(key_file, fake_uid)

        with patch("cloud_storage.crypto.KEY_FILE", key_file):
            with patch("cloud_storage.crypto.CONFIG_DIR", tmp_path):
                with patch("cloud_storage.crypto.os.getuid", return_value=0):
                    with fake_stat:
                        # Should NOT raise and should NOT regenerate.
                        key = crypto._load_or_generate_key()

        assert key == key_bytes_before.strip()
        assert key_file.read_bytes() == key_bytes_before
        reset_key_cache()

    def test_first_boot_generates_0600_atomically(self, tmp_path):
        """No key present -> generate with 0600 and never a world-readable window."""
        import os
        import stat as stat_mod
        from cloud_storage import crypto

        reset_key_cache()
        key_file = tmp_path / ".export_key"
        assert not key_file.exists()

        observed_modes = []
        real_replace = os.replace

        def _spy_replace(src, dst):
            # At the instant the key becomes visible under its real path, capture
            # the mode of the source temp file — it must already be 0600.
            observed_modes.append(stat_mod.S_IMODE(os.stat(src).st_mode))
            return real_replace(src, dst)

        with patch("cloud_storage.crypto.KEY_FILE", key_file):
            with patch("cloud_storage.crypto.CONFIG_DIR", tmp_path):
                with patch("cloud_storage.crypto.os.replace", side_effect=_spy_replace):
                    crypto._load_or_generate_key()

        assert key_file.exists()
        assert stat_mod.S_IMODE(os.stat(key_file).st_mode) == 0o600
        # The file was 0600 *before* it became visible under the real path.
        assert observed_modes == [0o600], (
            f"key was exposed with non-0600 mode during create: {observed_modes}"
        )
        reset_key_cache()

    def test_decrypt_broken_key_fails_soft(self, tmp_path):
        """A garbled key / undecryptable blob returns None, never raises."""
        reset_key_cache()
        key_file = tmp_path / ".export_key"

        with patch("cloud_storage.crypto.KEY_FILE", key_file):
            with patch("cloud_storage.crypto.CONFIG_DIR", tmp_path):
                # Encrypt with a real key first.
                encrypted = encrypt_credentials({"a": "b"})
                reset_key_cache()

                # Corrupt the key file in place (garbled key).
                key_file.write_bytes(b"not-a-valid-fernet-key")
                key_file.chmod(0o600)

                # Decrypt must fail-soft -> None, not raise.
                result = decrypt_credentials(encrypted)

        assert result is None
        reset_key_cache()

    def test_decrypt_garbled_ciphertext_fails_soft(self, tmp_path):
        """A corrupted ciphertext blob with a valid key returns None, never raises."""
        reset_key_cache()
        key_file = tmp_path / ".export_key"

        with patch("cloud_storage.crypto.KEY_FILE", key_file):
            with patch("cloud_storage.crypto.CONFIG_DIR", tmp_path):
                # Valid key, but feed garbage ciphertext.
                result = decrypt_credentials("this-is-not-a-valid-token")

        assert result is None
        reset_key_cache()


# =============================================================================
# Cloud Target API
# =============================================================================


class TestCloudTargetCRUD:
    @pytest.mark.asyncio
    async def test_list_empty(self, async_client):
        response = await async_client.get("/api/export/cloud-targets")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_create_target(self, mock_journal, async_client):
        with patch("routers.export.encrypt_credentials", return_value="encrypted_blob"):
            response = await async_client.post("/api/export/cloud-targets", json={
                "name": "My S3",
                "provider_type": "s3",
                "credentials": {"bucket_name": "test", "access_key_id": "AK", "secret_access_key": "SK"},
                "upload_path": "/exports",
            })
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "My S3"
        assert data["provider_type"] == "s3"
        # Credentials should be masked
        assert "AK" not in json.dumps(data["credentials"])

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_create_duplicate_name_returns_409(self, mock_journal, async_client):
        with patch("routers.export.encrypt_credentials", return_value="enc"):
            await async_client.post("/api/export/cloud-targets", json={
                "name": "Dup", "provider_type": "s3", "credentials": {"bucket_name": "b", "access_key_id": "a", "secret_access_key": "s"},
            })
            response = await async_client.post("/api/export/cloud-targets", json={
                "name": "Dup", "provider_type": "s3", "credentials": {"bucket_name": "b", "access_key_id": "a", "secret_access_key": "s"},
            })
        assert response.status_code == 409

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_delete_target(self, mock_journal, async_client):
        with patch("routers.export.encrypt_credentials", return_value="enc"):
            create_resp = await async_client.post("/api/export/cloud-targets", json={
                "name": "Delete Me", "provider_type": "s3",
                "credentials": {"bucket_name": "b", "access_key_id": "a", "secret_access_key": "s"},
            })
        target_id = create_resp.json()["id"]
        response = await async_client.delete(f"/api/export/cloud-targets/{target_id}")
        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_not_found(self, async_client):
        response = await async_client.delete("/api/export/cloud-targets/9999")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_update_target(self, mock_journal, async_client):
        with patch("routers.export.encrypt_credentials", return_value="enc"):
            create_resp = await async_client.post("/api/export/cloud-targets", json={
                "name": "Original", "provider_type": "s3",
                "credentials": {"bucket_name": "b", "access_key_id": "a", "secret_access_key": "s"},
            })
        target_id = create_resp.json()["id"]

        with patch("routers.export.encrypt_credentials", return_value="enc2"):
            with patch("routers.export.decrypt_credentials", return_value={"bucket_name": "new-bucket"}):
                response = await async_client.patch(f"/api/export/cloud-targets/{target_id}", json={
                    "name": "Updated",
                })
        assert response.status_code == 200
        assert response.json()["name"] == "Updated"


class TestCloudTargetTestConnection:
    @pytest.mark.asyncio
    @patch("routers.export.get_adapter")
    @patch("routers.export.decrypt_credentials", return_value={"bucket_name": "b"})
    @patch("routers.export.journal")
    async def test_saved_target(self, mock_journal, mock_decrypt, mock_get_adapter, async_client):
        # Create a target first
        with patch("routers.export.encrypt_credentials", return_value="enc"):
            create_resp = await async_client.post("/api/export/cloud-targets", json={
                "name": "Test Target", "provider_type": "s3",
                "credentials": {"bucket_name": "b", "access_key_id": "a", "secret_access_key": "s"},
            })
        target_id = create_resp.json()["id"]

        mock_adapter = AsyncMock()
        mock_adapter.test_connection.return_value = ConnectionTestResult(
            success=True, message="Connected", provider_info={"bucket": "b"}
        )
        mock_get_adapter.return_value = mock_adapter

        response = await async_client.post(f"/api/export/cloud-targets/{target_id}/test")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["message"] == "Connected"

    @pytest.mark.asyncio
    async def test_saved_target_not_found(self, async_client):
        response = await async_client.post("/api/export/cloud-targets/9999/test")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @patch("routers.export.get_adapter")
    async def test_inline_credentials(self, mock_get_adapter, async_client):
        mock_adapter = AsyncMock()
        mock_adapter.test_connection.return_value = ConnectionTestResult(
            success=True, message="OK", provider_info={}
        )
        mock_get_adapter.return_value = mock_adapter

        response = await async_client.post("/api/export/cloud-targets/test", json={
            "provider_type": "s3",
            "credentials": {"bucket_name": "b", "access_key_id": "a", "secret_access_key": "s"},
        })
        assert response.status_code == 200
        assert response.json()["success"] is True

    @pytest.mark.asyncio
    @patch("routers.export.get_adapter")
    async def test_connection_failure(self, mock_get_adapter, async_client):
        mock_adapter = AsyncMock()
        mock_adapter.test_connection.return_value = ConnectionTestResult(
            success=False, message="Access denied"
        )
        mock_get_adapter.return_value = mock_adapter

        response = await async_client.post("/api/export/cloud-targets/test", json={
            "provider_type": "s3",
            "credentials": {"bucket_name": "b", "access_key_id": "a", "secret_access_key": "s"},
        })
        assert response.status_code == 200
        assert response.json()["success"] is False

    @pytest.mark.asyncio
    @patch("routers.export.get_adapter")
    async def test_inline_adapter_exception_sanitizes_message(
        self, mock_get_adapter, async_client
    ):
        """CodeQL py/stack-trace-exposure (#1353): inline cloud target test
        MUST NOT echo str(e) — adapter exception messages can include URLs,
        tenant IDs, and token fragments. The client receives the exception
        class only.
        """
        secret = "AccessKey=AKIASECRET123 Bucket=internal://prod/db.sqlite"
        mock_get_adapter.side_effect = RuntimeError(secret)

        response = await async_client.post("/api/export/cloud-targets/test", json={
            "provider_type": "s3",
            "credentials": {
                "bucket_name": "b",
                "access_key_id": "a",
                "secret_access_key": "s",
            },
        })
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        # Sanitization contract: only the class name leaks, not the message.
        assert body["message"] == "Connection test failed (RuntimeError)"
        assert "AKIASECRET123" not in body["message"]
        assert "internal://" not in body["message"]

    @pytest.mark.asyncio
    @patch("routers.export.get_adapter")
    async def test_inline_import_error_sanitizes_path(
        self, mock_get_adapter, async_client
    ):
        """CodeQL py/stack-trace-exposure (#1352): missing-dependency replies
        MUST surface only the missing module name (e.g. "msal", "boto3"), not
        the str(e) form which on some platforms can include interpreter paths.
        """
        # ImportError preserves .name when constructed with name=...
        err = ImportError("No module named 'fakedep' from /opt/secret/path")
        err.name = "fakedep"
        mock_get_adapter.side_effect = err

        response = await async_client.post("/api/export/cloud-targets/test", json={
            "provider_type": "s3",
            "credentials": {
                "bucket_name": "b",
                "access_key_id": "a",
                "secret_access_key": "s",
            },
        })
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert body["message"] == "Missing dependency: fakedep"
        assert "/opt/secret" not in body["message"]

    @pytest.mark.asyncio
    @patch("routers.export.get_adapter")
    @patch("routers.export.decrypt_credentials", return_value={"bucket_name": "b"})
    @patch("routers.export.journal")
    async def test_saved_adapter_exception_sanitizes_message(
        self, mock_journal, mock_decrypt, mock_get_adapter, async_client
    ):
        """CodeQL py/stack-trace-exposure (#1351): saved cloud target test
        MUST sanitize adapter exception messages (same contract as inline).
        """
        with patch("routers.export.encrypt_credentials", return_value="enc"):
            create_resp = await async_client.post(
                "/api/export/cloud-targets",
                json={
                    "name": "Sanitize Target",
                    "provider_type": "s3",
                    "credentials": {
                        "bucket_name": "b",
                        "access_key_id": "a",
                        "secret_access_key": "s",
                    },
                },
            )
        target_id = create_resp.json()["id"]

        secret = "TenantID=00000000-1111-2222-3333-444444444444 Token=eyJSECRET"
        mock_get_adapter.side_effect = RuntimeError(secret)

        response = await async_client.post(
            f"/api/export/cloud-targets/{target_id}/test"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert body["message"] == "Connection test failed (RuntimeError)"
        assert "Token=" not in body["message"]
        assert "TenantID=" not in body["message"]


# =============================================================================
# Credential masking
# =============================================================================


class TestCredentialMasking:
    def test_masks_long_values(self):
        from routers.export import _mask_credentials
        creds = {"access_key_id": "AKIAIOSFODNN7EXAMPLE", "short": "abc"}
        masked = _mask_credentials(creds)
        assert masked["access_key_id"] == "***MPLE"
        assert masked["short"] == "***"

    def test_masks_nested_dicts(self):
        from routers.export import _mask_credentials
        creds = {"nested": {"secret": "verylongsecretvalue"}}
        masked = _mask_credentials(creds)
        assert masked["nested"]["secret"] == "***alue"

    def test_preserves_non_string_values(self):
        from routers.export import _mask_credentials
        creds = {"port": 443, "enabled": True}
        masked = _mask_credentials(creds)
        assert masked["port"] == 443
        assert masked["enabled"] is True


# =============================================================================
# OneDrive tenant_id / drive_id shape validation (SSRF defense)
# CodeQL alerts 1361, 1362 / bead enhancedchannelmanager-zbt74
# =============================================================================


class TestOneDriveTenantIdValidator:
    """_validate_tenant_id and adapter __init__ should reject SSRF-prone shapes."""

    def _make(self, tenant_id):
        return get_adapter("onedrive", {
            "client_id": "cid", "client_secret": "csec", "tenant_id": tenant_id,
        })

    def test_accepts_valid_guid_tenant_id(self):
        # Well-known Azure AD "common" GUID-form tenant
        adapter = self._make("72f988bf-86f1-41af-91ab-2d7cd011db47")
        assert adapter.tenant_id == "72f988bf-86f1-41af-91ab-2d7cd011db47"

    def test_accepts_valid_domain_tenant_id(self):
        adapter = self._make("contoso.onmicrosoft.com")
        assert adapter.tenant_id == "contoso.onmicrosoft.com"

    def test_accepts_single_label_tenant_id(self):
        # Azure permits single-label aliases like "common" / "organizations"
        adapter = self._make("common")
        assert adapter.tenant_id == "common"

    @pytest.mark.parametrize("bad", [
        "",
        "../evil",
        "evil.com/..",
        "foo.evil.com/",
        "..%2Fattacker",
        "tenant\x00id",
        "tenant id with space",
        "tenant/id",
        "tenant?x=1",
        "tenant#frag",
        "http://evil.com",
        "https://evil.com/",
        ".leading-dot.com",
        "trailing-dot.com.",
        "double..dot.com",
        "label-.invalid.com",
        "-leading-hyphen.com",
        "a" * 254,
    ])
    def test_rejects_invalid_tenant_id_shape(self, bad):
        with pytest.raises(ValueError):
            self._make(bad)

    def test_rejects_non_string_tenant_id(self):
        with pytest.raises(ValueError):
            get_adapter("onedrive", {
                "client_id": "c", "client_secret": "s", "tenant_id": None,
            })


class TestOneDriveDriveIdValidator:
    """_validate_drive_id and adapter __init__ should reject SSRF-prone shapes."""

    def _make(self, drive_id):
        return get_adapter("onedrive", {
            "client_id": "cid",
            "client_secret": "csec",
            "tenant_id": "contoso.onmicrosoft.com",
            "drive_id": drive_id,
        })

    def test_accepts_valid_base64url_drive_id(self):
        adapter = self._make("b!abcDEF123-_xyz")
        assert adapter.drive_id == "b!abcDEF123-_xyz"

    def test_empty_drive_id_allowed(self):
        adapter = self._make("")
        assert adapter.drive_id == ""

    def test_missing_drive_id_allowed(self):
        # Omitting the field entirely should also be fine (default drive).
        adapter = get_adapter("onedrive", {
            "client_id": "cid", "client_secret": "csec",
            "tenant_id": "contoso.onmicrosoft.com",
        })
        assert adapter.drive_id == ""

    @pytest.mark.parametrize("bad", [
        "../",
        "../../etc/passwd",
        "foo/bar",
        "foo\\bar",
        "https://evil.com/",
        "drive id with space",
        "drive\x00id",
        "drive\n",
        "driveé",  # unicode
        "a" * 129,       # overlong
        "drive?x=1",
        "drive#frag",
        "drive%2F",      # percent-encoded slash
    ])
    def test_rejects_invalid_drive_id_shape(self, bad):
        with pytest.raises(ValueError):
            self._make(bad)


class TestOneDriveCloudTargetApiValidation:
    """API boundary rejects SSRF-prone tenant_id/drive_id with HTTP 422."""

    @pytest.mark.asyncio
    async def test_create_rejects_bad_tenant_id(self, async_client):
        response = await async_client.post("/api/export/cloud-targets", json={
            "name": "BadTenant",
            "provider_type": "onedrive",
            "credentials": {
                "client_id": "c", "client_secret": "s",
                "tenant_id": "../evil",
            },
        })
        assert response.status_code == 422
        body = response.text
        assert "tenant_id" in body

    @pytest.mark.asyncio
    async def test_create_rejects_bad_drive_id(self, async_client):
        response = await async_client.post("/api/export/cloud-targets", json={
            "name": "BadDrive",
            "provider_type": "onedrive",
            "credentials": {
                "client_id": "c", "client_secret": "s",
                "tenant_id": "contoso.onmicrosoft.com",
                "drive_id": "../../etc/passwd",
            },
        })
        assert response.status_code == 422
        assert "drive_id" in response.text

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_create_accepts_valid_onedrive_credentials(self, mock_journal, async_client):
        with patch("routers.export.encrypt_credentials", return_value="enc"):
            response = await async_client.post("/api/export/cloud-targets", json={
                "name": "GoodOneDrive",
                "provider_type": "onedrive",
                "credentials": {
                    "client_id": "c", "client_secret": "s",
                    "tenant_id": "contoso.onmicrosoft.com",
                    "drive_id": "b!abcDEF123-_xyz",
                },
            })
        assert response.status_code == 201

    @pytest.mark.asyncio
    async def test_test_inline_rejects_bad_tenant_id(self, async_client):
        response = await async_client.post("/api/export/cloud-targets/test", json={
            "provider_type": "onedrive",
            "credentials": {
                "client_id": "c", "client_secret": "s",
                "tenant_id": "https://evil.com/",
            },
        })
        assert response.status_code == 422
