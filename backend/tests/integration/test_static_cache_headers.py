"""Integration tests for Cache-Control defaults on /assets and SPA index.html.

Bead: enhancedchannelmanager-hl603 — ECM should ship cache-safe defaults so
operators don't inherit whatever heuristic their reverse proxy applies.

Vite emits content-hashed filenames in frontend/dist/assets/* (see
frontend/vite.config.ts), so /assets/* is eternal-cache-safe. index.html
references those hashed bundles by name, so it must always re-validate or
users hit a stale index.html pointing at a bundle URL that no longer exists
on disk after a deploy (404 → kind:"chunk_load" client-error spike;
docs/runbooks/frontend_error_rate.md).

Companion runbook: docs/runbooks/infra-cache-invalidation.md
"""
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient

from main import (
    ASSETS_CACHE_CONTROL,
    INDEX_CACHE_CONTROL,
    ImmutableStaticFiles,
)


def _build_static_tree(root):
    """Mirror the runtime layout: <root>/assets/<bundle>.js + <root>/index.html."""
    assets_dir = root / "assets"
    assets_dir.mkdir()
    bundle = assets_dir / "index-deadbeef.js"
    bundle.write_text("console.log('hash-pinned bundle')\n")
    index = root / "index.html"
    index.write_text(
        "<!doctype html><html><head>"
        "<script src='/assets/index-deadbeef.js'></script>"
        "</head><body></body></html>\n"
    )
    return bundle, index


def _make_app(static_dir):
    """Construct an isolated FastAPI app that wires the same mount + SPA fallback
    pattern main.py uses, without depending on backend/static/ existing on disk
    in CI."""
    app = FastAPI()
    app.mount(
        "/assets",
        ImmutableStaticFiles(directory=os.path.join(static_dir, "assets")),
        name="assets",
    )

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        index_path = os.path.join(static_dir, "index.html")
        return FileResponse(
            index_path,
            headers={"Cache-Control": INDEX_CACHE_CONTROL},
        )

    return app


class TestAssetsCacheHeader:
    """/assets/* must advertise itself as immutable for one year."""

    def test_assets_response_has_immutable_cache_control(self, tmp_path):
        _build_static_tree(tmp_path)
        client = TestClient(_make_app(str(tmp_path)))

        resp = client.get("/assets/index-deadbeef.js")

        assert resp.status_code == 200
        assert resp.headers["cache-control"] == ASSETS_CACHE_CONTROL

    def test_assets_cache_control_is_one_year_immutable(self):
        """The constant itself encodes the policy — guard against drift."""
        assert "public" in ASSETS_CACHE_CONTROL
        assert "max-age=31536000" in ASSETS_CACHE_CONTROL  # 1 year in seconds
        assert "immutable" in ASSETS_CACHE_CONTROL

    def test_not_modified_response_preserves_cache_control(self, tmp_path):
        """Starlette's NotModifiedResponse copies cache-control onto 304s,
        so re-validations also carry the immutable directive forward."""
        _build_static_tree(tmp_path)
        client = TestClient(_make_app(str(tmp_path)))

        first = client.get("/assets/index-deadbeef.js")
        etag = first.headers.get("etag")
        assert etag, "StaticFiles should emit ETag for revalidation"

        second = client.get(
            "/assets/index-deadbeef.js",
            headers={"If-None-Match": etag},
        )
        assert second.status_code == 304
        assert second.headers["cache-control"] == ASSETS_CACHE_CONTROL


class TestIndexCacheHeader:
    """The SPA entry point must always be re-validated."""

    def test_spa_root_has_no_cache_must_revalidate(self, tmp_path):
        _build_static_tree(tmp_path)
        client = TestClient(_make_app(str(tmp_path)))

        resp = client.get("/")

        assert resp.status_code == 200
        assert resp.headers["cache-control"] == INDEX_CACHE_CONTROL

    def test_spa_arbitrary_path_has_no_cache_must_revalidate(self, tmp_path):
        """SPA routes (/channels, /settings, etc.) are also entry-point HTML."""
        _build_static_tree(tmp_path)
        client = TestClient(_make_app(str(tmp_path)))

        resp = client.get("/channels")

        assert resp.status_code == 200
        assert resp.headers["cache-control"] == INDEX_CACHE_CONTROL

    def test_index_cache_control_forbids_caching_without_revalidation(self):
        """The constant itself encodes the policy — guard against drift."""
        assert "no-cache" in INDEX_CACHE_CONTROL
        assert "must-revalidate" in INDEX_CACHE_CONTROL
