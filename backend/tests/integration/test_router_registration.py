"""
Integration tests for router registration.

Tests: Verify all routers registered, route prefixes correct,
       no duplicate paths, tags_metadata alignment.
Uses app.routes introspection — no HTTP calls needed.
"""
import pytest
from fastapi.routing import APIRoute

from main import app, tags_metadata


def _get_api_routes():
    """Return all APIRoute objects from the app."""
    return [r for r in app.routes if isinstance(r, APIRoute)]


def _get_api_paths():
    """Return all API path strings."""
    return {r.path for r in _get_api_routes()}


class TestRouteCount:
    """Verify the app has a reasonable number of routes."""

    def test_has_substantial_routes(self):
        """App should have 200+ API routes."""
        routes = _get_api_routes()
        assert len(routes) > 200, f"Expected 200+ routes, got {len(routes)}"


class TestRoutePrefixes:
    """Verify expected route prefix groups are registered."""

    EXPECTED_PREFIXES = [
        "/api/health",
        "/api/settings",
        "/api/channels",
        "/api/channel-groups",
        "/api/channel-profiles",
        "/api/streams",
        "/api/stream-profiles",
        "/api/m3u",
        "/api/epg",
        "/api/providers",
        "/api/tasks",
        "/api/cron",
        "/api/notifications",
        "/api/alert-methods",
        "/api/journal",
        "/api/stats",
        "/api/stream-stats",
        "/api/normalization",
        "/api/tags",
        "/api/cache",
        "/api/auto-creation",
        "/api/ffmpeg",
        "/api/auth",
    ]

    @pytest.mark.parametrize("prefix", EXPECTED_PREFIXES)
    def test_prefix_has_routes(self, prefix):
        """Each expected prefix should have at least one route."""
        paths = _get_api_paths()
        matching = [p for p in paths if p.startswith(prefix)]
        assert len(matching) > 0, f"No routes found with prefix {prefix}"

    def test_all_routes_under_api(self):
        """All API routes should start with /api/."""
        for route in _get_api_routes():
            assert route.path.startswith("/api/"), (
                f"Route {route.path} not under /api/ prefix"
            )


class TestNoDuplicateRoutes:
    """Verify no duplicate method+path combinations exist."""

    def test_no_duplicates(self):
        """Each method+path should appear exactly once."""
        seen = set()
        duplicates = []
        for route in _get_api_routes():
            for method in route.methods:
                key = (method, route.path)
                if key in seen:
                    duplicates.append(key)
                seen.add(key)
        assert duplicates == [], f"Duplicate routes: {duplicates}"


class TestTagsMetadata:
    """Verify tags_metadata alignment with actual route tags."""

    def test_metadata_has_entries(self):
        """tags_metadata should have entries defined."""
        assert len(tags_metadata) >= 25, (
            f"Expected 25+ tag entries, got {len(tags_metadata)}"
        )

    def test_metadata_entries_have_name_and_description(self):
        """Each tag entry should have name and description."""
        for entry in tags_metadata:
            assert "name" in entry, f"Tag entry missing name: {entry}"
            assert "description" in entry, f"Tag entry missing description: {entry}"

    def test_used_tags_mostly_covered(self):
        """Tags used on routes should mostly be in tags_metadata."""
        metadata_names = {t["name"] for t in tags_metadata}
        used_tags = set()
        for route in _get_api_routes():
            if route.tags:
                used_tags.update(route.tags)
        missing = used_tags - metadata_names
        # Allow a small number of tags to be missing (dynamically-added)
        assert len(missing) <= 3, (
            f"Too many tags missing from metadata: {missing}"
        )

    def test_core_tags_present(self):
        """Core tag names should be in tags_metadata."""
        metadata_names = {t["name"] for t in tags_metadata}
        core_tags = {
            "Health", "Settings", "Channels", "Channel Groups",
            "Streams", "M3U", "EPG", "Tasks", "Notifications",
            "Journal", "Tags", "Authentication",
        }
        missing = core_tags - metadata_names
        assert missing == set(), f"Core tags missing: {missing}"


class TestIncludedRouters:
    """Verify explicitly included routers are registered."""

    def test_auth_routes_present(self):
        """Auth router routes should be present."""
        paths = _get_api_paths()
        auth_paths = [p for p in paths if "/auth/" in p]
        assert len(auth_paths) >= 3, (
            f"Expected 3+ auth routes, got {len(auth_paths)}"
        )

    def test_admin_routes_present(self):
        """Admin router routes should be present."""
        paths = _get_api_paths()
        admin_paths = [p for p in paths if "/admin/" in p]
        assert len(admin_paths) >= 1, "Expected admin routes"

    def test_tls_routes_present(self):
        """TLS router routes should be present."""
        paths = _get_api_paths()
        tls_paths = [p for p in paths if "/tls" in p]
        assert len(tls_paths) >= 1, "Expected TLS routes"
