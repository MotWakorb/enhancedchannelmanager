"""
Unit tests for the Cache module.
"""
import time
import pytest

from cache import Cache, CacheEntry, get_cache


class TestCacheEntry:
    """Tests for CacheEntry dataclass."""

    def test_cache_entry_stores_data(self):
        """CacheEntry stores data and timestamp correctly."""
        data = {"key": "value"}
        now = time.time()
        entry = CacheEntry(data=data, cached_at=now)

        assert entry.data == data
        assert entry.cached_at == now

    def test_cache_entry_generic_typing(self):
        """CacheEntry supports different data types."""
        string_entry = CacheEntry[str](data="test", cached_at=time.time())
        list_entry = CacheEntry[list](data=[1, 2, 3], cached_at=time.time())
        dict_entry = CacheEntry[dict](data={"a": 1}, cached_at=time.time())

        assert string_entry.data == "test"
        assert list_entry.data == [1, 2, 3]
        assert dict_entry.data == {"a": 1}


class TestCache:
    """Tests for Cache class."""

    @pytest.fixture
    def cache(self):
        """Create a fresh cache instance for each test."""
        return Cache(default_ttl=60)

    def test_cache_init_with_default_ttl(self, cache):
        """Cache initializes with specified default TTL."""
        assert cache._default_ttl == 60
        assert cache._hits == 0
        assert cache._misses == 0
        assert len(cache._cache) == 0

    def test_cache_init_with_different_ttl(self):
        """Cache can be initialized with different TTL values."""
        cache = Cache(default_ttl=300)
        assert cache._default_ttl == 300

    def test_set_stores_value(self, cache):
        """set() stores value in cache."""
        cache.set("key1", "value1")
        assert "key1" in cache._cache
        assert cache._cache["key1"].data == "value1"

    def test_set_stores_timestamp(self, cache):
        """set() stores current timestamp."""
        before = time.time()
        cache.set("key1", "value1")
        after = time.time()

        assert before <= cache._cache["key1"].cached_at <= after

    def test_get_returns_cached_value(self, cache):
        """get() returns cached value when not expired."""
        cache.set("key1", "value1")
        result = cache.get("key1")
        assert result == "value1"

    def test_get_returns_none_for_missing_key(self, cache):
        """get() returns None for non-existent key."""
        result = cache.get("nonexistent")
        assert result is None

    def test_get_returns_none_for_expired_value(self, cache):
        """get() returns None when value has expired."""
        # Set with a tiny TTL that will expire immediately
        cache.set("key1", "value1")
        cache._cache["key1"].cached_at = time.time() - 100  # Force expire

        result = cache.get("key1", ttl=60)
        assert result is None
        assert "key1" not in cache._cache  # Should be removed

    def test_get_respects_custom_ttl(self, cache):
        """get() respects custom TTL parameter."""
        cache.set("key1", "value1")
        cache._cache["key1"].cached_at = time.time() - 50  # 50 seconds old

        # Should still be valid with 60s TTL
        assert cache.get("key1", ttl=60) == "value1"

        # Should be expired with 30s TTL
        assert cache.get("key1", ttl=30) is None

    def test_get_tracks_hits(self, cache):
        """get() increments hit counter on cache hit."""
        cache.set("key1", "value1")
        cache.get("key1")
        cache.get("key1")

        assert cache._hits == 2

    def test_get_tracks_misses(self, cache):
        """get() increments miss counter on cache miss."""
        cache.get("nonexistent1")
        cache.get("nonexistent2")

        assert cache._misses == 2

    def test_invalidate_removes_key(self, cache):
        """invalidate() removes specific key from cache."""
        cache.set("key1", "value1")
        cache.set("key2", "value2")

        result = cache.invalidate("key1")

        assert result is True
        assert "key1" not in cache._cache
        assert "key2" in cache._cache

    def test_invalidate_returns_false_for_missing_key(self, cache):
        """invalidate() returns False for non-existent key."""
        result = cache.invalidate("nonexistent")
        assert result is False

    def test_invalidate_prefix_removes_matching_keys(self, cache):
        """invalidate_prefix() removes all keys with matching prefix."""
        cache.set("channels:1", "data1")
        cache.set("channels:2", "data2")
        cache.set("channels:3", "data3")
        cache.set("streams:1", "data4")

        count = cache.invalidate_prefix("channels:")

        assert count == 3
        assert "channels:1" not in cache._cache
        assert "channels:2" not in cache._cache
        assert "channels:3" not in cache._cache
        assert "streams:1" in cache._cache

    def test_invalidate_prefix_returns_zero_for_no_matches(self, cache):
        """invalidate_prefix() returns 0 when no keys match."""
        cache.set("key1", "value1")
        count = cache.invalidate_prefix("nonexistent:")
        assert count == 0

    def test_clear_removes_all_entries(self, cache):
        """clear() removes all cached values."""
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")

        count = cache.clear()

        assert count == 3
        assert len(cache._cache) == 0

    def test_clear_returns_zero_for_empty_cache(self, cache):
        """clear() returns 0 for empty cache."""
        count = cache.clear()
        assert count == 0

    def test_stats_returns_entry_count(self, cache):
        """stats() returns correct entry count."""
        cache.set("key1", "value1")
        cache.set("key2", "value2")

        stats = cache.stats()

        assert stats["entry_count"] == 2

    def test_stats_returns_hit_miss_counts(self, cache):
        """stats() returns correct hit and miss counts."""
        cache.set("key1", "value1")
        cache.get("key1")  # hit
        cache.get("key1")  # hit
        cache.get("nonexistent")  # miss

        stats = cache.stats()

        assert stats["hits"] == 2
        assert stats["misses"] == 1

    def test_stats_calculates_hit_rate(self, cache):
        """stats() calculates correct hit rate percentage."""
        cache.set("key1", "value1")
        cache.get("key1")  # hit
        cache.get("key1")  # hit
        cache.get("key1")  # hit
        cache.get("nonexistent")  # miss

        stats = cache.stats()

        # 3 hits out of 4 requests = 75%
        assert stats["hit_rate_percent"] == 75.0

    def test_stats_returns_zero_hit_rate_with_no_requests(self, cache):
        """stats() returns 0% hit rate with no requests."""
        stats = cache.stats()
        assert stats["hit_rate_percent"] == 0

    def test_stats_includes_entry_ages(self, cache):
        """stats() includes age information for each entry."""
        cache.set("key1", "value1")
        time.sleep(0.01)  # Small delay

        stats = cache.stats()

        assert len(stats["entries"]) == 1
        assert stats["entries"][0]["key"] == "key1"
        assert stats["entries"][0]["age_seconds"] >= 0


class TestGetCache:
    """Tests for get_cache() global accessor."""

    def test_get_cache_returns_cache_instance(self):
        """get_cache() returns a Cache instance."""
        cache = get_cache()
        assert isinstance(cache, Cache)

    def test_get_cache_returns_same_instance(self):
        """get_cache() returns the same instance each time."""
        cache1 = get_cache()
        cache2 = get_cache()
        assert cache1 is cache2

    def test_global_cache_has_default_ttl(self):
        """Global cache has 300 second default TTL."""
        cache = get_cache()
        assert cache._default_ttl == 300
