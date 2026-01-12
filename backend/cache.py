"""Simple in-memory cache for API responses."""
import time
import logging
from typing import Any, TypeVar, Generic
from dataclasses import dataclass

logger = logging.getLogger(__name__)

T = TypeVar('T')


@dataclass
class CacheEntry(Generic[T]):
    """A cached value with timestamp."""
    data: T
    cached_at: float


class Cache:
    """Simple in-memory cache with TTL support."""

    def __init__(self, default_ttl: int = 300):
        """
        Initialize cache.

        Args:
            default_ttl: Default time-to-live in seconds (default: 5 minutes)
        """
        self._cache: dict[str, CacheEntry] = {}
        self._default_ttl = default_ttl
        self._hits = 0
        self._misses = 0
        logger.debug(f"Cache initialized with TTL: {default_ttl}s")

    def get(self, key: str, ttl: int | None = None) -> Any | None:
        """
        Get a value from cache if not expired.

        Args:
            key: Cache key
            ttl: Time-to-live in seconds (uses default if not specified)

        Returns:
            Cached value or None if not found or expired
        """
        entry = self._cache.get(key)
        if entry is None:
            self._misses += 1
            logger.debug(f"Cache miss for key '{key}'")
            return None

        effective_ttl = ttl if ttl is not None else self._default_ttl
        age = time.time() - entry.cached_at

        if age > effective_ttl:
            self._misses += 1
            logger.debug(f"Cache expired for key '{key}' (age: {age:.1f}s, ttl: {effective_ttl}s)")
            del self._cache[key]
            return None

        self._hits += 1
        logger.debug(f"Cache hit for key '{key}' (age: {age:.1f}s)")
        return entry.data

    def set(self, key: str, value: Any) -> None:
        """
        Store a value in cache.

        Args:
            key: Cache key
            value: Value to cache
        """
        self._cache[key] = CacheEntry(data=value, cached_at=time.time())
        logger.debug(f"Cached value for key '{key}'")

    def invalidate(self, key: str) -> bool:
        """
        Remove a specific key from cache.

        Args:
            key: Cache key to remove

        Returns:
            True if key was found and removed, False otherwise
        """
        if key in self._cache:
            del self._cache[key]
            logger.debug(f"Invalidated cache for key '{key}'")
            return True
        return False

    def invalidate_prefix(self, prefix: str) -> int:
        """
        Remove all keys matching a prefix.

        Args:
            prefix: Key prefix to match

        Returns:
            Number of keys removed
        """
        keys_to_remove = [k for k in self._cache.keys() if k.startswith(prefix)]
        for key in keys_to_remove:
            del self._cache[key]
        if keys_to_remove:
            logger.debug(f"Invalidated {len(keys_to_remove)} cache entries with prefix '{prefix}'")
        return len(keys_to_remove)

    def clear(self) -> int:
        """
        Clear all cached values.

        Returns:
            Number of entries cleared
        """
        count = len(self._cache)
        self._cache.clear()
        logger.info(f"Cleared entire cache ({count} entries)")
        return count

    def stats(self) -> dict:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache stats including hit rate
        """
        now = time.time()
        entries = []
        for key, entry in self._cache.items():
            entries.append({
                "key": key,
                "age_seconds": round(now - entry.cached_at, 1),
            })

        total_requests = self._hits + self._misses
        hit_rate = (self._hits / total_requests * 100) if total_requests > 0 else 0

        stats = {
            "entry_count": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate_percent": round(hit_rate, 1),
            "entries": entries,
        }

        logger.info(f"Cache stats: {len(self._cache)} entries, hit rate: {hit_rate:.1f}% ({self._hits}/{total_requests})")
        return stats


# Global cache instance
# TTL of 5 minutes for streams (they don't change often)
_cache = Cache(default_ttl=300)


def get_cache() -> Cache:
    """Get the global cache instance."""
    return _cache
