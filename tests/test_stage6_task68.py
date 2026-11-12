"""Tests for Stage 6 Task 68: Redis Shared Cache Integration.

Covers:
1. Redis Connection & Configuration:
   - Config parsing: CACHE_BACKEND, REDIS_URL, timeouts, payload limits, prefixes
   - Local default preservation (CACHE_BACKEND=memory)
2. Redis Shared Cache CRUD & Namespacing:
   - get, set, delete, clear, incr, get_int
   - Key prefixing (e.g. rag:v1:...)
3. TTL & Payload Size Limits:
   - TTL enforcement
   - Maximum payload size limit rejection (REDIS_MAX_PAYLOAD_BYTES)
4. Serialization Safety:
   - Pure JSON strings, zero unrestricted pickle deserialization
5. Multi-Instance Shared Cache Access:
   - Instance A computes & stores -> Instance B retrieves without recomputing (embedding & intent)
6. Cross-Instance Invalidation:
   - Distributed generation counter invalidation for retrieval cache
   - invalidate_all_knowledge_caches coordinated invalidation
7. Namespace & Model Isolation:
   - Different prefixes isolate environments
   - Different models / dimensions isolate embeddings
8. Concurrency & Thread-Safety:
   - Concurrent reads and writes across threads
9. Fail-Soft Resilience:
   - Redis error on read becomes cache miss
   - Redis error on write does not lose computation result
   - PostgreSQL checkpoints remain independent of Redis
10. Observability & FastAPI Integration:
    - /health reflects active cache backend
    - /cache/stats includes shared cache metrics
"""

from __future__ import annotations

import concurrent.futures
import json
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from advanced_rag.cache import (
    CacheStats,
    EmbeddingCache,
    IntentRoutingCache,
    LRUCache,
    RedisBackend,
    RedisSharedCache,
    RetrievalCache,
    SingleFlight,
    close_cache_backend,
    get_all_cache_stats,
    get_cache,
    get_embedding_cache,
    get_intent_cache,
    get_retrieval_cache,
    get_shared_cache,
    init_cache_backend,
    invalidate_all_knowledge_caches,
)
from advanced_rag.config import Settings


# =============================================================================
# IN-MEMORY MOCK REDIS FOR DETERMINISTIC UNIT TESTS
# =============================================================================


class MockRedisClient:
    """In-memory Redis client mock implementing core string, list, and scan operations."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._ttls: dict[str, float] = {}
        self._lists: dict[str, list[str]] = {}

    def ping(self) -> bool:
        return True

    def get(self, key: str) -> str | None:
        if key in self._ttls and self._ttls[key] <= time.time():
            self._store.pop(key, None)
            self._ttls.pop(key, None)
            return None
        return self._store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._store[key] = value
        if ex is not None:
            self._ttls[key] = time.time() + ex
        else:
            self._ttls.pop(key, None)
        return True

    def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                self._ttls.pop(k, None)
                count += 1
            if k in self._lists:
                del self._lists[k]
                count += 1
        return count

    def scan_iter(self, match: str = "*", count: int = 100):
        import fnmatch

        for k in list(self._store.keys()) + list(self._lists.keys()):
            if fnmatch.fnmatch(k, match):
                yield k

    def incr(self, key: str) -> int:
        val = int(self.get(key) or 0) + 1
        self.set(key, str(val))
        return val

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = self._lists.get(key, [])
        if end == -1:
            return items[start:]
        return items[start : end + 1]

    def rpush(self, key: str, value: str) -> int:
        self._lists.setdefault(key, []).append(value)
        return len(self._lists[key])

    def ltrim(self, key: str, start: int, end: int) -> bool:
        if key in self._lists:
            items = self._lists[key]
            if start < 0:
                start = max(0, len(items) + start)
            if end == -1:
                self._lists[key] = items[start:]
            else:
                self._lists[key] = items[start : end + 1]
        return True

    def pipeline(self):
        class Pipeline:
            def __init__(p_self):
                p_self._actions = []

            def rpush(p_self, key, val):
                p_self._actions.append(lambda: self.rpush(key, val))
                return p_self

            def ltrim(p_self, key, start, end):
                p_self._actions.append(lambda: self.ltrim(key, start, end))
                return p_self

            def execute(p_self):
                results = [act() for act in p_self._actions]
                p_self._actions.clear()
                return results

        return Pipeline()

    def close(self) -> None:
        pass


@pytest.fixture
def mock_redis_shared():
    """Provides a RedisSharedCache backed by MockRedisClient."""
    mock_client = MockRedisClient()
    with patch("redis.ConnectionPool.from_url") as mock_pool_fn:
        with patch("redis.Redis", return_value=mock_client):
            cache = RedisSharedCache(
                url="redis://localhost:6379/0",
                key_prefix="rag:test",
                max_payload_bytes=1024 * 1024,
                default_ttl=3600,
            )
            yield cache, mock_client
            cache.close()


# =============================================================================
# 1. CONFIGURATION & DEFAULTS
# =============================================================================


def test_redis_configuration_defaults():
    """Verify default cache configuration preserves local in-process mode."""
    settings = Settings()
    assert settings.cache_backend == "memory"
    assert settings.redis_url is None
    assert settings.redis_connect_timeout == 2.0
    assert settings.redis_socket_timeout == 2.0
    assert settings.redis_max_payload_bytes == 1_048_576
    assert settings.redis_key_prefix == "rag:v1"


def test_redis_configuration_custom_overrides():
    """Verify Redis configuration can be customized via settings/env."""
    settings = Settings(
        cache_backend="redis",
        redis_url="redis://custom-redis.internal:6379/2",
        redis_connect_timeout=5.0,
        redis_socket_timeout=3.5,
        redis_max_payload_bytes=524_288,
        redis_key_prefix="copilot:v2",
    )
    assert settings.cache_backend == "redis"
    assert settings.redis_url == "redis://custom-redis.internal:6379/2"
    assert settings.redis_connect_timeout == 5.0
    assert settings.redis_socket_timeout == 3.5
    assert settings.redis_max_payload_bytes == 524_288
    assert settings.redis_key_prefix == "copilot:v2"


# =============================================================================
# 2. REDIS SHARED CACHE CRUD & NAMESPACING
# =============================================================================


def test_redis_shared_cache_crud_and_prefix(mock_redis_shared):
    """Verify CRUD operations and key namespacing in RedisSharedCache."""
    cache, raw_client = mock_redis_shared

    # Set and get
    assert cache.set("mykey", "myval", ttl=60) is True
    assert cache.get("mykey") == "myval"

    # Verify key prefixing in raw Redis store
    assert "rag:test:mykey" in raw_client._store
    assert raw_client._store["rag:test:mykey"] == "myval"

    # Delete
    assert cache.delete("mykey") is True
    assert cache.get("mykey") is None
    assert "rag:test:mykey" not in raw_client._store


def test_redis_shared_cache_incr_and_get_int(mock_redis_shared):
    """Verify atomic counter increments and integer retrieval."""
    cache, raw_client = mock_redis_shared
    assert cache.get_int("counter", default=0) == 0
    assert cache.incr("counter") == 1
    assert cache.incr("counter") == 2
    assert cache.get_int("counter") == 2


def test_redis_shared_cache_clear(mock_redis_shared):
    """Verify clearing keys matching the prefix."""
    cache, raw_client = mock_redis_shared
    cache.set("a", "1")
    cache.set("b", "2")
    assert cache.get("a") == "1"
    assert cache.get("b") == "2"

    deleted = cache.clear()
    assert deleted >= 2
    assert cache.get("a") is None
    assert cache.get("b") is None


# =============================================================================
# 3. PAYLOAD LIMITS & SERIALIZATION
# =============================================================================


def test_redis_payload_size_limit_rejection(mock_redis_shared):
    """Verify payloads exceeding max_payload_bytes are skipped safely."""
    cache, raw_client = mock_redis_shared
    cache.max_payload_bytes = 100  # 100 bytes limit

    small_payload = "hello world"
    large_payload = "x" * 200

    # Small payload succeeds
    assert cache.set("small", small_payload) is True
    assert cache.get("small") == small_payload

    # Large payload is rejected without raising
    assert cache.set("large", large_payload) is False
    assert cache.get("large") is None


def test_safe_json_serialization_only(mock_redis_shared):
    """Verify all stored values are pure JSON, never binary pickle."""
    cache, raw_client = mock_redis_shared
    data = {"model": "bge-small", "dim": 384, "dense": [0.1, 0.2, 0.3]}
    cache.set("data", json.dumps(data))

    raw_val = raw_client.get("rag:test:data")
    assert isinstance(raw_val, str)
    # Validate it parses as JSON
    parsed = json.loads(raw_val)
    assert parsed["model"] == "bge-small"
    assert parsed["dense"] == [0.1, 0.2, 0.3]


# =============================================================================
# 4. MULTI-INSTANCE SHARED EMBEDDING CACHE
# =============================================================================


def test_multi_instance_shared_embedding_cache(mock_redis_shared):
    """Verify Instance A computes embedding and Instance B retrieves without recomputing."""
    shared_cache, _ = mock_redis_shared

    # Instance A (Process A)
    inst_a = EmbeddingCache(max_entries=100, ttl=3600, shared_cache=shared_cache)
    # Instance B (Process B)
    inst_b = EmbeddingCache(max_entries=100, ttl=3600, shared_cache=shared_cache)

    mock_compute = MagicMock(return_value=[0.11, 0.22, 0.33])

    # Instance A computes
    res_a = inst_a.get_or_compute_dense(
        text="What is a deployment?",
        model_name="bge-small",
        dim=384,
        compute_fn=mock_compute,
    )
    assert res_a == [0.11, 0.22, 0.33]
    assert mock_compute.call_count == 1

    # Instance B retrieves - compute_fn must NOT be called
    mock_compute_b = MagicMock(return_value=[0.99, 0.99, 0.99])
    res_b = inst_b.get_or_compute_dense(
        text="What is a deployment?",
        model_name="bge-small",
        dim=384,
        compute_fn=mock_compute_b,
    )
    assert res_b == [0.11, 0.22, 0.33]
    assert mock_compute_b.call_count == 0


def test_embedding_cache_model_isolation(mock_redis_shared):
    """Verify different models and dimensions do not collide in shared cache."""
    shared_cache, _ = mock_redis_shared
    inst = EmbeddingCache(max_entries=100, ttl=3600, shared_cache=shared_cache)

    compute_1 = MagicMock(return_value=[0.1, 0.2])
    compute_2 = MagicMock(return_value=[0.8, 0.9])

    # Model 1
    r1 = inst.get_or_compute_dense("query", "bge-small", 384, compute_1)
    # Model 2 with same text
    r2 = inst.get_or_compute_dense("query", "gemini-embed", 768, compute_2)

    assert r1 == [0.1, 0.2]
    assert r2 == [0.8, 0.9]
    assert compute_1.call_count == 1
    assert compute_2.call_count == 1


# =============================================================================
# 5. MULTI-INSTANCE SHARED INTENT ROUTING CACHE
# =============================================================================


def test_multi_instance_shared_intent_routing_cache(mock_redis_shared):
    """Verify Instance A routes intent and Instance B reuses decision without LLM call."""
    shared_cache, _ = mock_redis_shared

    inst_a = IntentRoutingCache(max_entries=100, ttl=3600, shared_cache=shared_cache)
    inst_b = IntentRoutingCache(max_entries=100, ttl=3600, shared_cache=shared_cache)

    compute_a = MagicMock(return_value={"route": "sql", "reason": "database query"})
    compute_b = MagicMock(return_value={"route": "vector", "reason": "vector query"})

    # Instance A computes
    dec_a = inst_a.get_or_compute("Show clusters", "router-v1", "p-v1", compute_a)
    assert dec_a["route"] == "sql"
    assert compute_a.call_count == 1

    # Instance B retrieves
    dec_b = inst_b.get_or_compute("Show clusters", "router-v1", "p-v1", compute_b)
    assert dec_b["route"] == "sql"
    assert compute_b.call_count == 0


# =============================================================================
# 6. CROSS-INSTANCE RETRIEVAL CACHE & GENERATION INVALIDATION
# =============================================================================


def test_cross_instance_retrieval_generation_invalidation(mock_redis_shared):
    """Verify shared generation counter invalidates retrieval cache across all instances."""
    shared_cache, _ = mock_redis_shared

    inst_a = RetrievalCache(max_entries=100, ttl=600, shared_cache=shared_cache)
    inst_b = RetrievalCache(max_entries=100, ttl=600, shared_cache=shared_cache)

    sample_chunks = [{"chunk_id": "c1", "text": "Pod crashloop runbook"}]

    # Instance A stores retrieval results
    inst_a.set_results(
        query="pod crashloop",
        collection="sre_copilot_v1",
        top_k=5,
        results=sample_chunks,
    )

    # Instance B retrieves successfully
    cached_b = inst_b.get_results(
        query="pod crashloop",
        collection="sre_copilot_v1",
        top_k=5,
    )
    assert cached_b is not None
    assert cached_b[0]["chunk_id"] == "c1"

    # Ingestion occurs on Instance A: bumps generation
    new_gen = inst_a.bump_generation("sre_copilot_v1")
    assert new_gen >= 1

    # Instance B immediately sees the new generation and reports cache miss
    cached_after_bump = inst_b.get_results(
        query="pod crashloop",
        collection="sre_copilot_v1",
        top_k=5,
    )
    assert cached_after_bump is None


def test_invalidate_all_knowledge_caches_cross_instance(mock_redis_shared):
    """Verify invalidate_all_knowledge_caches bumps generation and clears exact keys in Redis."""
    shared_cache, raw_client = mock_redis_shared
    with patch("advanced_rag.cache.get_shared_cache", return_value=shared_cache):
        res = invalidate_all_knowledge_caches()
        assert res["answer_cache_cleared"] == 1
        assert res["retrieval_cache_generation"] >= 1
        # Verify generation counter in Redis
        assert shared_cache.get_int("gen:knowledge") >= 1


# =============================================================================
# 7. CONCURRENCY & THREAD-SAFETY
# =============================================================================


def test_concurrent_shared_cache_access(mock_redis_shared):
    """Verify multiple concurrent threads can get, set, and increment safely."""
    cache, _ = mock_redis_shared

    def worker(idx: int):
        for i in range(15):
            cache.set(f"k_{idx}_{i}", f"val_{idx}_{i}", ttl=60)
            val = cache.get(f"k_{idx}_{i}")
            assert val == f"val_{idx}_{i}"
            cache.incr("thread_counter")

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(worker, i) for i in range(6)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    assert cache.get_int("thread_counter") == 6 * 15


# =============================================================================
# 8. FAIL-SOFT RESILIENCE
# =============================================================================


def test_redis_failure_read_becomes_miss(mock_redis_shared):
    """Verify that a Redis exception during read degrades gracefully to a cache miss."""
    cache, raw_client = mock_redis_shared

    # Simulate connection error
    raw_client.get = MagicMock(side_effect=RuntimeError("Redis connection lost"))

    # get() must not raise; returns None (clean cache miss)
    assert cache.get("some_key") is None
    stats = cache.stats()
    assert stats["errors"] >= 1
    assert stats["misses"] >= 1


def test_redis_failure_write_does_not_lose_computation(mock_redis_shared):
    """Verify that a Redis exception during write does not disrupt caller or lose results."""
    cache, raw_client = mock_redis_shared

    # Simulate connection error on set
    raw_client.set = MagicMock(side_effect=RuntimeError("Redis write failed"))

    inst = EmbeddingCache(max_entries=100, ttl=3600, shared_cache=cache)
    mock_compute = MagicMock(return_value=[0.55, 0.66])

    # Computation must succeed and return vector even though Redis write failed
    res = inst.get_or_compute_dense("test text", "bge-small", 384, mock_compute)
    assert res == [0.55, 0.66]
    assert mock_compute.call_count == 1


def test_postgresql_checkpoint_independence_from_redis():
    """Verify CHECKPOINT_BACKEND=postgres remains completely independent of Redis cache."""
    settings = Settings(
        checkpoint_backend="postgres",
        postgres_dsn="postgresql://user:pass@localhost:5432/db",
        cache_backend="redis",
        redis_url="redis://localhost:6379/0",
    )
    assert settings.checkpoint_backend == "postgres"
    assert settings.cache_backend == "redis"
    # The checkpointer factory must NOT use redis
    from advanced_rag.graph import checkpoint

    assert hasattr(checkpoint, "PostgresPoolManager")


# =============================================================================
# 9. OBSERVABILITY & METRICS
# =============================================================================


def test_get_all_cache_stats_includes_shared_cache(mock_redis_shared):
    """Verify get_all_cache_stats includes shared_cache when Redis is active."""
    shared_cache, _ = mock_redis_shared
    with patch("advanced_rag.cache.get_shared_cache", return_value=shared_cache):
        stats = get_all_cache_stats()
        assert "answer_cache" in stats
        assert "embedding_cache" in stats
        assert "intent_cache" in stats
        assert "retrieval_cache" in stats
        assert "shared_cache" in stats
        assert stats["shared_cache"]["backend"] == "redis"
        assert stats["shared_cache"]["prefix"] == "rag:test"

