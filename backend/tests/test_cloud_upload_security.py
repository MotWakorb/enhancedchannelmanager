"""Tests for bead 0i2vt.8 — cloud upload wiring (S3 / WebDAV / GDrive).

Covers the HARD ACs from grooming (SRE + Security):

* SSRF refusal BEFORE a socket opens (the 169.254.169.254 metadata AC) for
  each shipping adapter — proven by patching the resolver and asserting the
  upload is refused without the SDK/HTTP client ever being constructed.
* Blocking SDK calls are executor-wrapped (asyncio.to_thread).
* Uploads stream from the on-disk artifact — no whole-file .read() into RAM.
* Credential masking — no secret/signature substring survives into logs/errors.

All network/SDK is mocked — NO live cloud calls.
"""
from __future__ import annotations

import ipaddress
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cloud_storage import get_adapter
from cloud_storage import upload_security
from cloud_storage import webdav_adapter as webdav_mod
from cloud_storage.upload_security import mask_secrets
from security import ssrf


@pytest.fixture
def _fake_googleapiclient():
    """Inject a fake googleapiclient.http module (the SDK is not installed in
    the test env) so the gdrive adapter's inline import of MediaFileUpload
    resolves to a no-op stand-in."""
    import sys
    import types

    pkg = types.ModuleType("googleapiclient")
    http_mod = types.ModuleType("googleapiclient.http")
    http_mod.MediaFileUpload = MagicMock(name="MediaFileUpload")
    pkg.http = http_mod
    with patch.dict(sys.modules, {
        "googleapiclient": pkg,
        "googleapiclient.http": http_mod,
    }):
        yield http_mod


# ---------------------------------------------------------------------------
# Helpers — deterministic DNS so the SSRF corpus is hermetic.
# ---------------------------------------------------------------------------
def _patch_dns(*ips):
    """Patch the resolver inside security.ssrf to return ``ips``."""
    return patch.object(
        ssrf, "_resolve", lambda host, port: [ipaddress.ip_address(i) for i in ips]
    )


def _artifact(tmp_path: Path) -> Path:
    p = tmp_path / "ecm-artifact-abc123.zip"
    p.write_bytes(b"PK\x03\x04" + b"x" * 4096)
    return p


# ===========================================================================
# Credential masking
# ===========================================================================
class TestMaskSecrets:
    def test_masks_authorization_bearer(self):
        line = "PUT https://x Authorization: Bearer eyJ0eParticularTOKEN.value.sig"
        out = mask_secrets(line)
        assert "eyJ0eParticularTOKEN" not in out
        assert "REDACTED" in out

    def test_masks_aws_access_key_id(self):
        out = mask_secrets("access_key_id=AKIAIOSFODNN7EXAMPLE region=us-east-1")
        assert "AKIAIOSFODNN7EXAMPLE" not in out

    def test_masks_secret_access_key_kv(self):
        out = mask_secrets("secret_access_key: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
        assert "wJalrXUtnFEMI" not in out

    def test_masks_aws_secret_access_key_kv_single_literal_re(self):
        # Guards the Semgrep single-raw-string-literal collapse of _SECRET_KV_RE:
        # the pattern must still mask an aws_secret_access_key=<value> KV pair.
        out = mask_secrets("aws_secret_access_key=wJalrXUtnFEMIK7MDENGbPxRfiCYzzz token=keep")
        assert "wJalrXUtnFEMIK7MDENGbPxRfiCYzzz" not in out
        assert "REDACTED" in out
        # The key name is preserved; only the value is redacted.
        assert "aws_secret_access_key=" in out

    def test_masks_x_amz_signature(self):
        url = (
            "https://b.s3.amazonaws.com/k?X-Amz-Algorithm=AWS4-HMAC-SHA256"
            "&X-Amz-Signature=4709abcdef0123456789&X-Amz-Credential=AKIA/scope"
        )
        out = mask_secrets(url)
        assert "4709abcdef0123456789" not in out
        assert "AKIA/scope" not in out

    def test_masks_bearer_token_standalone(self):
        out = mask_secrets("headers={'Authorization': 'Bearer abc123def456ghi'}")
        assert "abc123def456ghi" not in out

    def test_empty_input_is_safe(self):
        assert mask_secrets("") == ""

    def test_patterns_are_precompiled_constants(self):
        # Semgrep no-bare-re-on-dynamic-pattern: the rules must be compiled
        # Pattern objects, not strings recompiled per call.
        import re as _re
        for rule in upload_security._MASK_RULES:
            assert isinstance(rule, _re.Pattern)


# ===========================================================================
# SSRF refusal BEFORE a socket opens — the 169.254.169.254 metadata AC
# ===========================================================================
class TestS3SsrfRefusedBeforeSocket:
    def _adapter(self, endpoint):
        return get_adapter("s3", {
            "bucket_name": "b", "access_key_id": "AKIAEXAMPLE", "secret_access_key": "S",
            "endpoint_url": endpoint,
        })

    @pytest.mark.asyncio
    async def test_s3_endpoint_pointed_at_imds_is_refused_before_socket(self, tmp_path):
        """A custom S3 endpoint resolving to 169.254.169.254 must be refused
        BEFORE the boto3 client (and therefore any socket) is created."""
        adapter = self._adapter("https://evil.example.com:9000")
        get_client = MagicMock()  # proves the SDK client is NEVER built
        with patch.object(adapter, "_get_client", get_client):
            with _patch_dns("169.254.169.254"):
                result = await adapter.upload(_artifact(tmp_path), "backups/a.zip")
        assert result.success is False
        assert get_client.call_count == 0, "boto3 client built despite SSRF denial"

    @pytest.mark.asyncio
    async def test_s3_literal_imds_host_refused(self, tmp_path):
        adapter = self._adapter("http://169.254.169.254/")
        get_client = MagicMock()
        with patch.object(adapter, "_get_client", get_client):
            result = await adapter.upload(_artifact(tmp_path), "backups/a.zip")
        assert result.success is False
        assert get_client.call_count == 0


class TestWebDAVSsrfRefusedBeforeSocket:
    @pytest.mark.asyncio
    async def test_webdav_base_url_imds_refused_before_client(self, tmp_path):
        adapter = get_adapter("webdav", {
            "base_url": "https://evil.example.com/dav", "username": "u", "password": "p",
        })
        # If a client were built, this would fail loudly — prove it is NOT.
        make_client = MagicMock()
        with patch.object(webdav_mod, "pinned_async_client", make_client):
            with _patch_dns("169.254.169.254"):
                result = await adapter.upload(_artifact(tmp_path), "a.zip")
        assert result.success is False
        assert make_client.call_count == 0, "httpx client built despite SSRF denial"


class TestGDriveSsrfRefusedBeforeSocket:
    def _adapter(self):
        return get_adapter("gdrive", {
            "service_account_json": {"client_email": "x@y.iam", "token_uri": "https://oauth2"},
            "folder_id": "FID",
        })

    @pytest.mark.asyncio
    async def test_gdrive_endpoint_resolving_to_imds_refused_before_service(self, tmp_path):
        adapter = self._adapter()
        get_service = MagicMock()
        with patch.object(adapter, "_get_service", get_service):
            with _patch_dns("169.254.169.254"):
                result = await adapter.upload(_artifact(tmp_path), "a.zip")
        assert result.success is False
        assert get_service.call_count == 0, "Drive service built despite SSRF denial"


# ===========================================================================
# Executor-wrapping — blocking SDK calls run via asyncio.to_thread
# ===========================================================================
class TestExecutorWrapping:
    @pytest.mark.asyncio
    async def test_s3_upload_file_runs_in_executor(self, tmp_path):
        adapter = get_adapter("s3", {
            "bucket_name": "b", "access_key_id": "AKIAEXAMPLE", "secret_access_key": "S",
        })
        mock_client = MagicMock()
        seen = {}

        def _record_upload(*a, **k):
            # Confirm we are NOT on the main event-loop thread — to_thread runs
            # us on a worker thread.
            import threading
            seen["thread"] = threading.current_thread().name

        mock_client.upload_file.side_effect = _record_upload
        with patch.object(adapter, "_get_client", return_value=mock_client):
            result = await adapter.upload(_artifact(tmp_path), "backups/a.zip")
        assert result.success is True
        mock_client.upload_file.assert_called_once()
        assert seen.get("thread") and "MainThread" not in seen["thread"]

    @pytest.mark.asyncio
    async def test_s3_upload_passes_path_not_bytes(self, tmp_path):
        """boto3.upload_file is called with the file PATH (it streams from disk),
        never with whole-file bytes — proves no .read() into RAM."""
        artifact = _artifact(tmp_path)
        adapter = get_adapter("s3", {
            "bucket_name": "b", "access_key_id": "A", "secret_access_key": "S",
        })
        mock_client = MagicMock()
        with patch.object(adapter, "_get_client", return_value=mock_client):
            await adapter.upload(artifact, "backups/a.zip")
        call = mock_client.upload_file.call_args
        assert call.args[0] == str(artifact)  # the on-disk path
        # No bytes object anywhere in the call args.
        assert not any(isinstance(a, (bytes, bytearray)) for a in call.args)

    @pytest.mark.asyncio
    async def test_gdrive_execute_runs_in_executor(self, tmp_path, _fake_googleapiclient):
        adapter = get_adapter("gdrive", {
            "service_account_json": {"client_email": "x@y"}, "folder_id": "FID",
        })
        threads = []

        def _list_execute():
            import threading
            threads.append(threading.current_thread().name)
            return {"files": []}

        def _create_execute():
            import threading
            threads.append(threading.current_thread().name)
            return {"id": "newid", "webViewLink": "https://drive/x"}

        mock_service = MagicMock()
        mock_service.files.return_value.list.return_value.execute.side_effect = _list_execute
        mock_service.files.return_value.create.return_value.execute.side_effect = _create_execute

        with patch.object(adapter, "_validate_endpoint"), \
             patch.object(adapter, "_get_service", return_value=mock_service):
            result = await adapter.upload(_artifact(tmp_path), "a.zip")
        assert result.success is True
        assert threads and all("MainThread" not in t for t in threads)


# ===========================================================================
# Streaming — WebDAV uploads via a file handle, not whole-file bytes
# ===========================================================================
class TestWebDAVStreaming:
    @pytest.mark.asyncio
    async def test_webdav_upload_streams_file_handle(self, tmp_path):
        adapter = get_adapter("webdav", {
            "base_url": "https://dav.example.com/files", "username": "u", "password": "secretpw",
        })
        artifact = _artifact(tmp_path)

        captured = {}
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        class _FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def put(self, url, content=None, **kw):
                # content must be a file-like object (streamed), NOT bytes.
                captured["content_type"] = type(content).__name__
                captured["is_bytes"] = isinstance(content, (bytes, bytearray))
                captured["url"] = url
                return mock_resp

        with _patch_dns("93.184.216.34"):
            with patch.object(webdav_mod, "pinned_async_client", return_value=_FakeClient()):
                result = await adapter.upload(artifact, "a.zip")

        assert result.success is True
        assert captured["is_bytes"] is False, "WebDAV buffered the whole file into RAM"
        # The connect URL is pinned to the validated IP, not the hostname.
        assert "93.184.216.34" in captured["url"]

    @pytest.mark.asyncio
    async def test_webdav_upload_error_is_masked(self, tmp_path):
        adapter = get_adapter("webdav", {
            "base_url": "https://dav.example.com/files",
            "username": "u", "password": "p",
        })

        class _BoomClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def put(self, *a, **k):
                raise RuntimeError(
                    "401 Authorization: Bearer leakedtoken123456 rejected"
                )

        with _patch_dns("93.184.216.34"):
            with patch.object(webdav_mod, "pinned_async_client", return_value=_BoomClient()):
                result = await adapter.upload(_artifact(tmp_path), "a.zip")
        assert result.success is False
        assert "leakedtoken123456" not in result.error


# ===========================================================================
# Factory registration of the net-new WebDAV adapter
# ===========================================================================
class TestWebDAVFactory:
    def test_factory_returns_webdav_adapter(self):
        from cloud_storage.webdav_adapter import WebDAVAdapter
        adapter = get_adapter("webdav", {"base_url": "https://dav.example.com/x"})
        assert isinstance(adapter, WebDAVAdapter)

    def test_webdav_requires_base_url(self):
        with pytest.raises(ValueError):
            get_adapter("webdav", {})

    def test_webdav_insecure_flag_parsed(self):
        adapter = get_adapter("webdav", {"base_url": "https://x/y", "insecure": True})
        assert adapter.insecure is True
