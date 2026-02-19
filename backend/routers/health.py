"""
Health & cache router — health check and cache management endpoints.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import os
from typing import Optional

from fastapi import APIRouter

from cache import get_cache

router = APIRouter(tags=["Health"])


@router.get("/api/health")
async def health_check():
    """Health check endpoint returning version and connection status."""
    version = os.environ.get("ECM_VERSION", "unknown")
    release_channel = os.environ.get("RELEASE_CHANNEL", "latest")
    git_commit = os.environ.get("GIT_COMMIT", "unknown")

    return {
        "status": "healthy",
        "service": "enhanced-channel-manager",
        "version": version,
        "release_channel": release_channel,
        "git_commit": git_commit,
    }


@router.post("/api/cache/invalidate", tags=["Cache"])
async def invalidate_cache(prefix: Optional[str] = None):
    """Invalidate cached data. If prefix is provided, only invalidate matching keys."""
    cache = get_cache()
    if prefix:
        count = cache.invalidate_prefix(prefix)
        return {"message": f"Invalidated {count} cache entries with prefix '{prefix}'"}
    else:
        count = cache.clear()
        return {"message": f"Cleared entire cache ({count} entries)"}


@router.get("/api/cache/stats", tags=["Cache"])
async def cache_stats():
    """Get cache statistics."""
    cache = get_cache()
    return cache.stats()
