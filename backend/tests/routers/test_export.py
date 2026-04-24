"""
Unit tests for Export router endpoints.

Tests: Profile CRUD, generate, preview, download, validation, journal logging.
Mocks: Dispatcharr client, ExportManager internals.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from export_models import PlaylistProfile


def _create_profile(session, **overrides):
    """Helper to create a PlaylistProfile with sensible defaults."""
    defaults = {
        "name": "Test Export",
        "description": "Test profile",
        "selection_mode": "all",
        "selected_groups": "[]",
        "selected_channels": "[]",
        "stream_url_mode": "direct",
        "include_logos": True,
        "include_epg_ids": True,
        "include_channel_numbers": True,
        "sort_order": "number",
        "filename_prefix": "playlist",
    }
    defaults.update(overrides)
    profile = PlaylistProfile(**defaults)
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


# =============================================================================
# Profile CRUD — List
# =============================================================================


class TestListProfiles:
    """Tests for GET /api/export/profiles."""

    @pytest.mark.asyncio
    async def test_returns_empty_list(self, async_client):
        response = await async_client.get("/api/export/profiles")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_returns_profiles(self, async_client, test_session):
        _create_profile(test_session, name="Profile A")
        _create_profile(test_session, name="Profile B")
        response = await async_client.get("/api/export/profiles")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        names = {p["name"] for p in data}
        assert names == {"Profile A", "Profile B"}

    @pytest.mark.asyncio
    async def test_includes_has_generated_field(self, async_client, test_session):
        _create_profile(test_session)
        response = await async_client.get("/api/export/profiles")
        data = response.json()
        assert data[0]["has_generated"] is False


# =============================================================================
# Profile CRUD — Create
# =============================================================================


class TestCreateProfile:
    """Tests for POST /api/export/profiles."""

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_creates_profile(self, mock_journal, async_client):
        response = await async_client.post("/api/export/profiles", json={
            "name": "My Export",
            "selection_mode": "all",
            "filename_prefix": "myexport",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "My Export"
        assert data["selection_mode"] == "all"
        assert data["filename_prefix"] == "myexport"
        assert data["id"] is not None

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_logs_journal_entry(self, mock_journal, async_client):
        await async_client.post("/api/export/profiles", json={"name": "JournalTest"})
        mock_journal.log_entry.assert_called_once()
        call_kwargs = mock_journal.log_entry.call_args
        assert call_kwargs[1]["category"] == "export"
        assert call_kwargs[1]["action_type"] == "create"

    @pytest.mark.asyncio
    async def test_duplicate_name_returns_409(self, async_client, test_session):
        _create_profile(test_session, name="Duplicate")
        response = await async_client.post("/api/export/profiles", json={"name": "Duplicate"})
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_invalid_selection_mode_returns_422(self, async_client):
        response = await async_client.post("/api/export/profiles", json={
            "name": "Bad", "selection_mode": "invalid",
        })
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_sort_order_returns_422(self, async_client):
        response = await async_client.post("/api/export/profiles", json={
            "name": "Bad", "sort_order": "invalid",
        })
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_filename_prefix_returns_422(self, async_client):
        response = await async_client.post("/api/export/profiles", json={
            "name": "Bad", "filename_prefix": "has spaces!",
        })
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_groups_mode_requires_selected_groups(self, async_client):
        response = await async_client.post("/api/export/profiles", json={
            "name": "Bad", "selection_mode": "groups", "selected_groups": [],
        })
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_channels_mode_requires_selected_channels(self, async_client):
        response = await async_client.post("/api/export/profiles", json={
            "name": "Bad", "selection_mode": "channels", "selected_channels": [],
        })
        assert response.status_code == 400

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_groups_mode_with_selection(self, mock_journal, async_client):
        response = await async_client.post("/api/export/profiles", json={
            "name": "Groups", "selection_mode": "groups", "selected_groups": [1, 2, 3],
        })
        assert response.status_code == 201
        data = response.json()
        assert data["selected_groups"] == [1, 2, 3]


# =============================================================================
# Profile CRUD — Update
# =============================================================================


class TestUpdateProfile:
    """Tests for PATCH /api/export/profiles/{id}."""

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_updates_name(self, mock_journal, async_client, test_session):
        profile = _create_profile(test_session)
        response = await async_client.patch(f"/api/export/profiles/{profile.id}", json={
            "name": "Updated Name",
        })
        assert response.status_code == 200
        assert response.json()["name"] == "Updated Name"

    @pytest.mark.asyncio
    async def test_not_found_returns_404(self, async_client):
        response = await async_client.patch("/api/export/profiles/9999", json={"name": "X"})
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_duplicate_name_returns_409(self, async_client, test_session):
        _create_profile(test_session, name="First")
        second = _create_profile(test_session, name="Second")
        response = await async_client.patch(f"/api/export/profiles/{second.id}", json={
            "name": "First",
        })
        assert response.status_code == 409

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_partial_update_preserves_other_fields(self, mock_journal, async_client, test_session):
        profile = _create_profile(test_session, description="Original desc")
        response = await async_client.patch(f"/api/export/profiles/{profile.id}", json={
            "include_logos": False,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["include_logos"] is False
        assert data["description"] == "Original desc"


# =============================================================================
# Profile CRUD — Delete
# =============================================================================


class TestDeleteProfile:
    """Tests for DELETE /api/export/profiles/{id}."""

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    @patch("routers.export._export_manager")
    async def test_deletes_profile(self, mock_mgr, mock_journal, async_client, test_session):
        profile = _create_profile(test_session)
        response = await async_client.delete(f"/api/export/profiles/{profile.id}")
        assert response.status_code == 204
        mock_mgr.cleanup.assert_called_once_with(profile.id)

    @pytest.mark.asyncio
    async def test_not_found_returns_404(self, async_client):
        response = await async_client.delete("/api/export/profiles/9999")
        assert response.status_code == 404


# =============================================================================
# Generate
# =============================================================================


class TestGenerate:
    """Tests for POST /api/export/profiles/{id}/generate."""

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    @patch("routers.export._export_manager")
    async def test_generates_successfully(self, mock_mgr, mock_journal, async_client, test_session):
        profile = _create_profile(test_session)
        mock_mgr.generate = AsyncMock(return_value={
            "channels_count": 50,
            "m3u_path": "/config/exports/1/playlist.m3u",
            "xmltv_path": "/config/exports/1/playlist.xml",
            "m3u_size": 5000,
            "xmltv_size": 20000,
        })
        response = await async_client.post(f"/api/export/profiles/{profile.id}/generate")
        assert response.status_code == 200
        data = response.json()
        assert data["channels_count"] == 50
        assert "duration_ms" in data

    @pytest.mark.asyncio
    async def test_not_found_returns_404(self, async_client):
        response = await async_client.post("/api/export/profiles/9999/generate")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    @patch("routers.export._export_manager")
    async def test_generation_failure_returns_500(self, mock_mgr, mock_journal, async_client, test_session):
        profile = _create_profile(test_session)
        mock_mgr.generate = AsyncMock(side_effect=Exception("Connection refused"))
        response = await async_client.post(f"/api/export/profiles/{profile.id}/generate")
        assert response.status_code == 500
        assert response.json()["detail"] == "Internal server error"

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    @patch("routers.export._export_manager")
    async def test_logs_journal_on_success(self, mock_mgr, mock_journal, async_client, test_session):
        profile = _create_profile(test_session)
        mock_mgr.generate = AsyncMock(return_value={"channels_count": 10})
        await async_client.post(f"/api/export/profiles/{profile.id}/generate")
        # Should have generate_started and generate_completed entries
        calls = mock_journal.log_entry.call_args_list
        action_types = [c[1]["action_type"] for c in calls]
        assert "generate_started" in action_types
        assert "generate_completed" in action_types

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    @patch("routers.export._export_manager")
    async def test_logs_journal_on_failure(self, mock_mgr, mock_journal, async_client, test_session):
        profile = _create_profile(test_session)
        mock_mgr.generate = AsyncMock(side_effect=Exception("Timeout"))
        await async_client.post(f"/api/export/profiles/{profile.id}/generate")
        calls = mock_journal.log_entry.call_args_list
        action_types = [c[1]["action_type"] for c in calls]
        assert "generate_started" in action_types
        assert "generate_failed" in action_types


# =============================================================================
# Preview
# =============================================================================


class TestPreview:
    """Tests for GET /api/export/profiles/{id}/preview."""

    @pytest.mark.asyncio
    @patch("routers.export._export_manager")
    @patch("routers.export.get_client")
    async def test_returns_preview(self, mock_get_client, mock_mgr, async_client, test_session):
        profile = _create_profile(test_session)
        channels = [
            {"id": i, "name": f"Channel {i}", "channel_number": i,
             "channel_group_name": "Test", "tvg_id": f"ch{i}", "logo_url": "",
             "streams": [100 + i]}
            for i in range(1, 16)
        ]
        mock_mgr._fetch_channels = AsyncMock(return_value=channels)
        mock_mgr._sort_channels = MagicMock(return_value=channels)

        response = await async_client.get(f"/api/export/profiles/{profile.id}/preview")
        assert response.status_code == 200
        data = response.json()
        assert data["total_channels"] == 15
        assert len(data["preview_channels"]) == 10  # Max 10 preview

    @pytest.mark.asyncio
    async def test_not_found_returns_404(self, async_client):
        response = await async_client.get("/api/export/profiles/9999/preview")
        assert response.status_code == 404


# =============================================================================
# Download
# =============================================================================


class TestDownloadM3U:
    """Tests for GET /api/export/profiles/{id}/download/m3u."""

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_downloads_m3u(self, mock_journal, async_client, test_session, tmp_path):
        profile = _create_profile(test_session)
        exports_root = tmp_path / "exports"
        export_dir = exports_root / str(profile.id)
        export_dir.mkdir(parents=True)
        m3u_file = export_dir / "playlist.m3u"
        m3u_file.write_text("#EXTM3U\n#EXTINF:-1,Test\nhttp://test\n")

        with patch("export_manager.EXPORTS_DIR", exports_root):
            response = await async_client.get(f"/api/export/profiles/{profile.id}/download/m3u")
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/x-mpegurl"
        assert "playlist.m3u" in response.headers["content-disposition"]
        assert "#EXTM3U" in response.text

    @pytest.mark.asyncio
    async def test_not_generated_returns_404(self, async_client, test_session, tmp_path):
        profile = _create_profile(test_session)
        exports_root = tmp_path / "exports"
        export_dir = exports_root / str(profile.id)
        export_dir.mkdir(parents=True)
        with patch("export_manager.EXPORTS_DIR", exports_root):
            response = await async_client.get(f"/api/export/profiles/{profile.id}/download/m3u")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_profile_not_found_returns_404(self, async_client):
        response = await async_client.get("/api/export/profiles/9999/download/m3u")
        assert response.status_code == 404


class TestDownloadXMLTV:
    """Tests for GET /api/export/profiles/{id}/download/xmltv."""

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_downloads_xmltv(self, mock_journal, async_client, test_session, tmp_path):
        profile = _create_profile(test_session)
        exports_root = tmp_path / "exports"
        export_dir = exports_root / str(profile.id)
        export_dir.mkdir(parents=True)
        xml_file = export_dir / "playlist.xml"
        xml_file.write_text('<?xml version="1.0"?>\n<tv></tv>\n')

        with patch("export_manager.EXPORTS_DIR", exports_root):
            response = await async_client.get(f"/api/export/profiles/{profile.id}/download/xmltv")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/xml"
        assert "playlist.xml" in response.headers["content-disposition"]

    @pytest.mark.asyncio
    async def test_not_generated_returns_404(self, async_client, test_session, tmp_path):
        profile = _create_profile(test_session)
        exports_root = tmp_path / "exports"
        export_dir = exports_root / str(profile.id)
        export_dir.mkdir(parents=True)
        with patch("export_manager.EXPORTS_DIR", exports_root):
            response = await async_client.get(f"/api/export/profiles/{profile.id}/download/xmltv")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    @patch("routers.export._export_manager")
    async def test_regenerate_flag(self, mock_mgr, mock_journal, async_client, test_session, tmp_path):
        profile = _create_profile(test_session)
        exports_root = tmp_path / "exports"
        export_dir = exports_root / str(profile.id)
        export_dir.mkdir(parents=True)
        xml_file = export_dir / "playlist.xml"
        xml_file.write_text("<tv></tv>")
        mock_mgr.generate = AsyncMock()

        with patch("export_manager.EXPORTS_DIR", exports_root):
            response = await async_client.get(
                f"/api/export/profiles/{profile.id}/download/xmltv?regenerate=true"
            )
        assert response.status_code == 200
        mock_mgr.generate.assert_called_once()


# =============================================================================
# Validation edge cases
# =============================================================================


class TestValidation:
    """Additional validation tests."""

    @pytest.mark.asyncio
    async def test_invalid_url_mode_returns_422(self, async_client):
        response = await async_client.post("/api/export/profiles", json={
            "name": "Bad", "stream_url_mode": "invalid",
        })
        assert response.status_code == 422

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_filename_prefix_allows_hyphens_underscores(self, mock_journal, async_client):
        response = await async_client.post("/api/export/profiles", json={
            "name": "Good", "filename_prefix": "my-export_v2",
        })
        assert response.status_code == 201

    @pytest.mark.asyncio
    @patch("routers.export.journal")
    async def test_update_switching_to_groups_requires_selection(self, mock_journal, async_client, test_session):
        profile = _create_profile(test_session)
        response = await async_client.patch(f"/api/export/profiles/{profile.id}", json={
            "selection_mode": "groups",
        })
        assert response.status_code == 400


# =============================================================================
# Print-Guide XSS Regression (bd-i3npz / CodeQL 1360)
# =============================================================================
#
# CodeQL alert 1360 flags request.title reaching Response(content=html) in
# backend/routers/export.py:1243 as a reflected-XSS sink. The sanitizer
# `_escape_html` IS in fact applied at :1235 before the title is inlined into
# the HTML document. These tests prove the sanitizer fires and exist so the
# CodeQL alert can be formally dismissed per ADR-005 Phase 1 policy
# (sanitizer-based FP dismissals must reference a test that proves the escape).
# =============================================================================


class TestPrintGuideXSS:
    """Regression tests ensuring print-guide HTML output escapes user input."""

    @pytest.mark.asyncio
    @patch("routers.export.get_client")
    async def test_print_guide_escapes_xss_in_title(self, mock_get_client, async_client):
        """Script tag in title must be HTML-entity encoded in the response body."""
        mock_client = MagicMock()
        mock_client.get_channel_groups = AsyncMock(return_value=[])
        mock_client.get_channels = AsyncMock(return_value={"results": []})
        mock_get_client.return_value = mock_client

        payload = {
            "title": "<script>alert(1)</script>",
            "groups": [],
        }
        response = await async_client.post("/api/export/print-guide", json=payload)

        assert response.status_code == 200
        body = response.text
        # Raw script tag must NOT appear anywhere in the rendered HTML
        assert "<script>alert(1)</script>" not in body
        # Entity-encoded form MUST appear (single-escape, HTML entity form)
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body

    @pytest.mark.asyncio
    @patch("routers.export.get_client")
    async def test_print_guide_escapes_double_quote_in_title(self, mock_get_client, async_client):
        """Double-quote in title must be entity-encoded to prevent attribute-context escape."""
        mock_client = MagicMock()
        mock_client.get_channel_groups = AsyncMock(return_value=[])
        mock_client.get_channels = AsyncMock(return_value={"results": []})
        mock_get_client.return_value = mock_client

        payload = {
            "title": 'abc" def',
            "groups": [],
        }
        response = await async_client.post("/api/export/print-guide", json=payload)

        assert response.status_code == 200
        body = response.text
        # The raw `"` from the user input must not survive as an unescaped character
        # adjacent to its preceding token (which would allow attribute breakout).
        assert 'abc" def' not in body
        # The escaped entity form must be present in the body.
        assert "abc&quot; def" in body

    @pytest.mark.asyncio
    @patch("routers.export.get_client")
    async def test_print_guide_escapes_ampersand_in_title(self, mock_get_client, async_client):
        """Ampersand in title must be encoded first to avoid double-escape drift."""
        mock_client = MagicMock()
        mock_client.get_channel_groups = AsyncMock(return_value=[])
        mock_client.get_channels = AsyncMock(return_value={"results": []})
        mock_get_client.return_value = mock_client

        payload = {
            "title": "A & B <tag>",
            "groups": [],
        }
        response = await async_client.post("/api/export/print-guide", json=payload)

        assert response.status_code == 200
        body = response.text
        # The ampersand is escaped first, then < and > are escaped — result is
        # single-pass encoded: "A &amp; B &lt;tag&gt;".
        assert "A &amp; B &lt;tag&gt;" in body
        # Raw `<tag>` substring must not appear.
        assert "A & B <tag>" not in body


# =============================================================================
# Export Path-Injection Regression (bd-h5rfv / CodeQL 1354-1359)
# =============================================================================
#
# CodeQL py/path-injection (CWE-22/23/36/73/99) flagged six sinks in the
# export pipeline:
#
#   1354 — backend/export_manager.py:87  (Path.exists in cleanup)
#   1355 — backend/export_manager.py:89  (shutil.rmtree in cleanup) DESTRUCTIVE
#   1356 — backend/routers/export.py:397 (m3u path construction)
#   1357 — backend/routers/export.py:408 (m3u_path.read_text)
#   1358 — backend/routers/export.py:433 (xmltv path construction)
#   1359 — backend/routers/export.py:444 (xmltv_path.read_text)
#
# Upstream sanitizers (FastAPI int path-param, Pydantic FILENAME_RE on
# filename_prefix) make the dataflow unreachable today, but bd-h5rfv adds
# defense-in-depth canonicalize-and-verify (`_safe_export_path`) that
# guarantees containment at the sink rather than relying on caller hygiene.
# These tests prove the containment check fires for the standard traversal
# and symlink-escape payload set, mirroring TestSavedBackupsPathInjection
# in test_backup.py (bd-0a1pr / CodeQL 1416-1419).
# =============================================================================


class TestExportPathInjection:
    """Path-injection regression tests for export download / cleanup paths.

    Backstops `_safe_export_path` containment in `export_manager` and the
    download endpoints in `routers.export`. Mirrors the coverage shape of
    `TestSavedBackupsPathInjection` (bd-0a1pr).
    """

    # Profile-id payloads that must never reach a filesystem sink.
    # FastAPI's int coercion handles most of these at the router boundary
    # (returning 422), but `_safe_export_path` is also called directly from
    # ExportManager.cleanup, where the int contract is by Python type-hint
    # only — no runtime enforcement. The unit tests below exercise both
    # paths.
    TRAVERSAL_PROFILE_IDS = [
        "..%2F1",
        "..%2F..%2Fetc%2Fpasswd",
        "%2Fetc%2Fpasswd",
        "1%00.evil",
        "..%5C1",
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload", TRAVERSAL_PROFILE_IDS)
    async def test_download_m3u_rejects_traversal_in_profile_id(self, async_client, payload):
        """download_m3u must reject non-int profile_id payloads with 4xx.

        FastAPI's int coercion is the first line of defense — the request
        never reaches the path-construction sink. Any non-2xx is acceptable;
        the critical guarantee is that we never reach Path.read_text on an
        attacker-controlled path component.
        """
        response = await async_client.get(f"/api/export/profiles/{payload}/download/m3u")
        assert response.status_code in (400, 404, 422), (
            f"payload {payload!r} was not rejected: status={response.status_code}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload", TRAVERSAL_PROFILE_IDS)
    async def test_download_xmltv_rejects_traversal_in_profile_id(self, async_client, payload):
        """download_xmltv must reject non-int profile_id payloads with 4xx."""
        response = await async_client.get(f"/api/export/profiles/{payload}/download/xmltv")
        assert response.status_code in (400, 404, 422), (
            f"payload {payload!r} was not rejected: status={response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_safe_export_path_rejects_absolute_traversal(self, tmp_path):
        """_safe_export_path raises ExportPathError if profile_id forces escape.

        Direct unit test of the helper: even if a caller bypasses Pydantic
        and passes a string with traversal, the resolved path must be
        verified under EXPORTS_DIR or rejected.
        """
        from export_manager import _safe_export_path, ExportPathError

        exports_root = tmp_path / "exports"
        exports_root.mkdir()
        with patch("export_manager.EXPORTS_DIR", exports_root):
            # Absolute-path injection: str(profile_id) being an absolute path
            # would otherwise overwrite EXPORTS_DIR via Path joining semantics.
            with pytest.raises(ExportPathError):
                _safe_export_path("/etc/passwd")

    @pytest.mark.asyncio
    async def test_safe_export_path_rejects_dotdot_traversal(self, tmp_path):
        """_safe_export_path raises on `..` traversal in the file-name part."""
        from export_manager import _safe_export_path, ExportPathError

        exports_root = tmp_path / "exports"
        exports_root.mkdir()
        with patch("export_manager.EXPORTS_DIR", exports_root):
            with pytest.raises(ExportPathError):
                _safe_export_path(1, "../../etc/passwd")

    @pytest.mark.asyncio
    async def test_safe_export_path_accepts_legitimate_path(self, tmp_path):
        """_safe_export_path returns a canonical Path for legitimate input."""
        from export_manager import _safe_export_path

        exports_root = tmp_path / "exports"
        exports_root.mkdir()
        with patch("export_manager.EXPORTS_DIR", exports_root):
            path = _safe_export_path(42, "playlist.m3u")
            assert path == (exports_root / "42" / "playlist.m3u").resolve()

    @pytest.mark.asyncio
    async def test_cleanup_refuses_traversal_payload(self, tmp_path):
        """ExportManager.cleanup must not rmtree on a traversal payload.

        Even if a caller bypasses the int type hint and passes a string with
        traversal, the rmtree sink (alert 1355, DESTRUCTIVE) must never run
        on a path outside EXPORTS_DIR. This is the strongest defense — if
        cleanup leaks rmtree to attacker-controlled paths, an attacker could
        delete arbitrary directories.
        """
        from export_manager import ExportManager

        exports_root = tmp_path / "exports"
        exports_root.mkdir()
        # Create a sentinel directory OUTSIDE exports_root — this must NOT
        # be deleted by cleanup, even if cleanup is somehow called with a
        # crafted profile_id that resolves to it.
        outside = tmp_path / "outside"
        outside.mkdir()
        sentinel = outside / "do-not-delete"
        sentinel.write_text("sentinel-content")

        mgr = ExportManager()
        with patch("export_manager.EXPORTS_DIR", exports_root):
            # str(profile_id) resolves to absolute /tmp/.../outside via
            # joinpath semantics — _safe_export_path must reject this and
            # cleanup must short-circuit before reaching rmtree.
            mgr.cleanup(str(outside))

        # Sentinel must still exist with original contents.
        assert sentinel.exists()
        assert sentinel.read_text() == "sentinel-content"

    @pytest.mark.asyncio
    async def test_cleanup_handles_dotdot_traversal(self, tmp_path):
        """ExportManager.cleanup short-circuits on `..` traversal in profile_id."""
        from export_manager import ExportManager

        exports_root = tmp_path / "exports"
        exports_root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        sentinel = outside / "do-not-delete"
        sentinel.write_text("sentinel-content")

        mgr = ExportManager()
        with patch("export_manager.EXPORTS_DIR", exports_root):
            # Profile id "../outside" resolves under tmp_path/outside,
            # which is NOT under exports_root — must short-circuit.
            mgr.cleanup("../outside")

        assert sentinel.exists()
        assert sentinel.read_text() == "sentinel-content"

    @pytest.mark.asyncio
    async def test_download_m3u_rejects_symlink_escape(
        self, async_client, test_session, tmp_path
    ):
        """A symlink in the export dir pointing outside must not leak contents.

        Creates a symlink whose name passes the FILENAME_RE prefix check but
        whose resolved target is outside EXPORTS_DIR. After canonicalization,
        `_safe_export_path.relative_to()` must raise and produce a 400.
        """
        import os

        exports_root = tmp_path / "exports"
        export_dir = exports_root / "1"
        export_dir.mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "secret.m3u"
        secret.write_text("top-secret-contents")

        # filename_prefix "playlist" is the default — the m3u file the
        # router will look for is "playlist.m3u". Make that a symlink
        # pointing outside.
        link_path = export_dir / "playlist.m3u"
        try:
            os.symlink(secret, link_path)
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks not supported in this environment")

        profile = _create_profile(test_session, name="SymTest", filename_prefix="playlist")
        with patch("export_manager.EXPORTS_DIR", exports_root):
            response = await async_client.get(f"/api/export/profiles/{profile.id}/download/m3u")

        # The canonicalized path resolves outside EXPORTS_DIR, so the
        # containment check must reject with 400. If the check regressed,
        # the response body would contain "top-secret-contents".
        assert response.status_code == 400
        assert "top-secret-contents" not in response.text

    @pytest.mark.asyncio
    async def test_download_xmltv_rejects_symlink_escape(
        self, async_client, test_session, tmp_path
    ):
        """A symlink-escape in the xmltv path must not leak contents."""
        import os

        exports_root = tmp_path / "exports"
        export_dir = exports_root / "2"
        export_dir.mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "secret.xml"
        secret.write_text("top-secret-xml")

        link_path = export_dir / "playlist.xml"
        try:
            os.symlink(secret, link_path)
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks not supported in this environment")

        profile = _create_profile(test_session, name="SymTestX", filename_prefix="playlist")
        # Use this profile's id, but force EXPORTS_DIR so the symlinked
        # file is under <exports_root>/<profile.id> not "/2".
        export_dir_for_profile = exports_root / str(profile.id)
        export_dir_for_profile.mkdir(parents=True, exist_ok=True)
        link_for_profile = export_dir_for_profile / "playlist.xml"
        try:
            os.symlink(secret, link_for_profile)
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks not supported in this environment")

        with patch("export_manager.EXPORTS_DIR", exports_root):
            response = await async_client.get(f"/api/export/profiles/{profile.id}/download/xmltv")

        assert response.status_code == 400
        assert "top-secret-xml" not in response.text
