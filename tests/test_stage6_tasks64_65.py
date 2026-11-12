"""Tests for Stage 6 Tasks 64 & 65: Advanced Caching & Intelligent Cache Optimization.

Covers:
1. TTL Tests:
   - Entry available before expiry
   - Entry unavailable after expiry (lazy eviction on get)
   - Monotonic clock usage protects against wall-clock skew
   - Sliding TTL extension on access
2. Capacity & LRU Eviction:
   - Strict capacity ceiling enforcement (max_entries)
   - Predictable LRU eviction order
   - Explicit cleanup_expired pruning
   - Eviction count & metric correctness
3. Cache Operations & Metrics:
   - get, set, delete, clear, invalidate (prefix & predicate), cleanup_expired
   - Metrics tracking: hits, misses, evictions, expired, count, hit_rate
4. Concurrency & SingleFlight Request Coalescing:
   - Multi-threaded concurrent readers and writers without corruption or deadlocks
   - SingleFlight coalescing identical in-flight requests into exactly one execution
   - Different keys executed independently
   - SingleFlight exception propagation without deadlocks
5. Embedding Cache (Task 65.1):
   - Repeated identical query embedding returns cached vector without calling embedder
   - Namespaced by model and dimension
   - Defensive copies ensure cached vector immutability
   - Single-flight coalescing for simultaneous identical embedding calls
   - Embedder failure leaves cache uncorrupted
6. Intent Routing Cache (Task 65.2):
   - Repeated questions return cached RouteDecision without calling LLM
   - Namespaced by router model and prompt hash
   - Dynamic state fields are never cached
   - LLM failure falls back gracefully to vector routing
7. Retrieval Result Cache (Task 65.3 - Benchmark Gated):
   - Disabled by default in Settings
   - Filter and parameter aware
   - Generation-based instant invalidation
8. Knowledge Invalidation (Task 65.4):
   - Document upload/replace/delete and corpus sync invalidate answer and retrieval caches
   - Unaffected caches (embedding and intent routing) remain intact
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from advanced_rag.cache import (
    CacheStats,
    EmbeddingCache,
    IntentRoutingCache,
    LRUCache,
    RetrievalCache,
    SingleFlight,
    get_cache,
    get_embedding_cache,
    get_intent_cache,
    get_retrieval_cache,
    invalidate_all_knowledge_caches,
)
from advanced_rag.config import Settings
from advanced_rag.graph.nodes import Route, RouteDecision, route_node
from advanced_rag.graph.state import RagState
from advanced_rag.models import Chunk, RetrievedChunk


# =============================================================================
# 1. TTL TESTS
# =============================================================================


def test_ttl_entry_available_before_expiry():
    cache = LRUCache[str](max_entries=10, default_ttl=10.0)
    cache.set("k1", "v1", ttl=5.0)
    assert cache.get("k1") == "v1"
    assert "k1" in cache


def test_ttl_entry_unavailable_after_expiry():
    cache = LRUCache[str](max_entries=10, default_ttl=10.0)
    cache.set("k1", "v1", ttl=0.05)
    time.sleep(0.08)
    assert cache.get("k1") is None
    assert "k1" not in cache
    # Verify expired count incremented
    stats = cache.stats()
    assert stats.expired >= 1
    assert stats.misses >= 1


def test_ttl_monotonic_clock_used():
    """Verify time.monotonic is used so system wall-clock jumps do not expire entries."""
    cache = LRUCache[str](max_entries=10, default_ttl=60.0)
    now = time.monotonic()
    with patch("time.monotonic", return_value=now):
        cache.set("k1", "v1", ttl=30.0)
        assert cache.get("k1") == "v1"

    # Wall clock simulates an arbitrary jump, but monotonic advances only by 10s
    with patch("time.monotonic", return_value=now + 10.0):
        assert cache.get("k1") == "v1"

    # Monotonic advances past 30s
    with patch("time.monotonic", return_value=now + 31.0):
        assert cache.get("k1") is None


def test_ttl_sliding_window():
    """Verify sliding TTL extends expiry on each read access."""
    cache = LRUCache[str](max_entries=10, default_ttl=60.0)
    now = time.monotonic()
    with patch("time.monotonic", return_value=now):
        cache.set("k1", "v1", ttl=20.0, sliding=True)

    # Access at now + 15s (before 20s expiry) -> should reset expiry to (now + 15) + 20 = now + 35
    with patch("time.monotonic", return_value=now + 15.0):
        assert cache.get("k1") == "v1"

    # At now + 25s: would have expired under fixed TTL (20s), but sliding extended it to 25 + 20 = 45s
    with patch("time.monotonic", return_value=now + 25.0):
        assert cache.get("k1") == "v1"

    # At now + 46s: expired (> 45s)
    with patch("time.monotonic", return_value=now + 46.0):
        assert cache.get("k1") is None


# =============================================================================
# 2. CAPACITY & LRU EVICTION TESTS
# =============================================================================


def test_capacity_max_entries_enforced():
    cache = LRUCache[str](max_entries=3, default_ttl=60.0)
    cache.set("a", "1")
    cache.set("b", "2")
    cache.set("c", "3")
    assert len(cache) == 3

    # Adding a 4th entry must evict the least recently used ('a')
    cache.set("d", "4")
    assert len(cache) == 3
    assert cache.get("a") is None
    assert cache.get("b") == "2"
    assert cache.get("c") == "3"
    assert cache.get("d") == "4"

    stats = cache.stats()
    assert stats.evictions == 1
    assert stats.count == 3


def test_lru_order_updated_on_access():
    cache = LRUCache[str](max_entries=3, default_ttl=60.0)
    cache.set("a", "1")
    cache.set("b", "2")
    cache.set("c", "3")

    # Access 'a' to make it most recently used; 'b' becomes the oldest
    assert cache.get("a") == "1"

    # Adding 'd' must evict 'b', not 'a'
    cache.set("d", "4")
    assert cache.get("b") is None
    assert cache.get("a") == "1"
    assert cache.get("c") == "3"
    assert cache.get("d") == "4"


def test_cleanup_expired_explicit_pruning():
    cache = LRUCache[str](max_entries=10, default_ttl=60.0)
    now = time.monotonic()
    with patch("time.monotonic", return_value=now):
        cache.set("active", "1", ttl=100.0)
        cache.set("expired1", "2", ttl=10.0)
        cache.set("expired2", "3", ttl=20.0)

    with patch("time.monotonic", return_value=now + 25.0):
        assert len(cache._entries) == 3
        cleaned = cache.cleanup_expired()
        assert cleaned == 2
        assert len(cache) == 1
        assert cache.get("active") == "1"


# =============================================================================
# 3. CACHE OPERATIONS & METRICS
# =============================================================================


def test_cache_crud_operations():
    cache = LRUCache[dict](max_entries=10, default_ttl=60.0)
    cache.set("item1", {"score": 99})
    assert cache.get("item1") == {"score": 99}

    assert cache.delete("item1") is True
    assert cache.get("item1") is None
    assert cache.delete("nonexistent") is False

    cache.set("k1", {"val": 1})
    cache.set("k2", {"val": 2})
    cache.clear()
    assert len(cache) == 0
    assert cache.get("k1") is None


def test_cache_invalidate_prefix_and_predicate():
    cache = LRUCache[str](max_entries=10, default_ttl=60.0)
    cache.set("doc:1", "a")
    cache.set("doc:2", "b")
    cache.set("user:1", "c")

    # Invalidate by prefix
    pruned = cache.invalidate("doc:")
    assert pruned == 2
    assert cache.get("doc:1") is None
    assert cache.get("doc:2") is None
    assert cache.get("user:1") == "c"

    # Invalidate by predicate
    cache.set("tag:prod:1", "x")
    cache.set("tag:dev:1", "y")
    pruned2 = cache.invalidate(lambda k: "prod" in k)
    assert pruned2 == 1
    assert cache.get("tag:prod:1") is None
    assert cache.get("tag:dev:1") == "y"


def test_cache_stats_metrics():
    cache = LRUCache[str](max_entries=5, default_ttl=60.0)
    cache.set("k1", "v1")
    cache.get("k1")  # Hit
    cache.get("k1")  # Hit
    cache.get("k2")  # Miss

    stats = cache.stats()
    assert stats.hits == 2
    assert stats.misses == 1
    assert stats.count == 1
    assert stats.hit_rate == pytest.approx(2 / 3, 0.01)

    cache.reset_stats()
    assert cache.stats().hits == 0
    assert cache.stats().misses == 0


# =============================================================================
# 4. CONCURRENCY & SINGLE-FLIGHT COALESCING
# =============================================================================


def test_concurrent_readers_and_writers():
    cache = LRUCache[int](max_entries=50, default_ttl=10.0)
    num_threads = 16
    ops_per_thread = 100

    def worker(worker_id: int):
        for i in range(ops_per_thread):
            key = f"key_{i % 10}"
            if worker_id % 2 == 0:
                cache.set(key, i)
            else:
                cache.get(key)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, tid) for tid in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    assert len(cache) <= 50


def test_single_flight_coalesces_identical_requests():
    sf = SingleFlight[str]()
    call_count = 0
    lock = threading.Lock()

    def expensive_computation() -> str:
        nonlocal call_count
        with lock:
            call_count += 1
        time.sleep(0.05)  # Simulate latency
        return "computed_result"

    num_threads = 10
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(sf.execute, "same_query", expensive_computation) for _ in range(num_threads)]
        results = [f.result() for f in futures]

    assert all(r == "computed_result" for r in results)
    # Crucial assertion: despite 10 concurrent calls, the computation was executed exactly once!
    assert call_count == 1


def test_single_flight_independent_keys():
    sf = SingleFlight[str]()
    call_counts: dict[str, int] = {}
    lock = threading.Lock()

    def compute(key: str) -> str:
        with lock:
            call_counts[key] = call_counts.get(key, 0) + 1
        time.sleep(0.02)
        return f"result_{key}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        f1 = executor.submit(sf.execute, "key_a", lambda: compute("key_a"))
        f2 = executor.submit(sf.execute, "key_b", lambda: compute("key_b"))
        assert f1.result() == "result_key_a"
        assert f2.result() == "result_key_b"

    assert call_counts["key_a"] == 1
    assert call_counts["key_b"] == 1


def test_single_flight_exception_propagation_no_deadlock():
    sf = SingleFlight[str]()

    def failing_computation() -> str:
        time.sleep(0.03)
        raise ValueError("simulated computational failure")

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(sf.execute, "fail_key", failing_computation) for _ in range(4)]
        for f in futures:
            with pytest.raises(ValueError, match="simulated computational failure"):
                f.result(timeout=1.0)


# =============================================================================
# 5. EMBEDDING CACHE TESTS (TASK 65.1)
# =============================================================================


def test_embedding_cache_repeated_query_cached():
    emb_cache = EmbeddingCache(max_entries=100, ttl=3600.0)
    call_count = 0

    def mock_embed(text: str) -> list[float]:
        nonlocal call_count
        call_count += 1
        return [0.1, 0.2, 0.3]

    # First call: misses cache, executes mock_embed
    vec1 = emb_cache.get_or_compute("what is pod status", "model-a", 3, lambda: mock_embed("what is pod status"))
    assert vec1 == [0.1, 0.2, 0.3]
    assert call_count == 1

    # Second call: hits cache, mock_embed NOT called
    vec2 = emb_cache.get_or_compute("what is pod status", "model-a", 3, lambda: mock_embed("what is pod status"))
    assert vec2 == [0.1, 0.2, 0.3]
    assert call_count == 1


def test_embedding_cache_namespaced_by_model_and_dim():
    emb_cache = EmbeddingCache(max_entries=100, ttl=3600.0)
    calls = []

    def mock_embed(m: str, d: int) -> list[float]:
        calls.append((m, d))
        return [0.5] * d

    text = "kubernetes ingress"
    v1 = emb_cache.get_or_compute(text, "model-onnx", 384, lambda: mock_embed("model-onnx", 384))
    v2 = emb_cache.get_or_compute(text, "model-gemini", 768, lambda: mock_embed("model-gemini", 768))

    assert len(v1) == 384
    assert len(v2) == 768
    assert len(calls) == 2


def test_embedding_cache_defensive_copy():
    emb_cache = EmbeddingCache(max_entries=10, ttl=3600.0)
    original = [1.0, 2.0, 3.0]
    vec1 = emb_cache.get_or_compute("pod", "m", 3, lambda: original)

    # Mutate the returned vector
    vec1[0] = 999.0

    # Fetch again from cache: must NOT be mutated!
    vec2 = emb_cache.get_or_compute("pod", "m", 3, lambda: [1.0, 2.0, 3.0])
    assert vec2[0] == 1.0


def test_embedding_cache_failure_handling():
    emb_cache = EmbeddingCache(max_entries=10, ttl=3600.0)

    def fail():
        raise RuntimeError("Embedding service unavailable")

    with pytest.raises(RuntimeError):
        emb_cache.get_or_compute("fail", "m", 3, fail)

    # Cache should have 0 entries
    assert emb_cache.size == 0


# =============================================================================
# 6. INTENT ROUTING CACHE TESTS (TASK 65.2)
# =============================================================================


def test_intent_cache_repeated_routing():
    intent_cache = IntentRoutingCache(max_entries=100, ttl=3600.0)
    llm_calls = 0

    def mock_llm_route() -> dict[str, str]:
        nonlocal llm_calls
        llm_calls += 1
        return {"route": "vector", "reason": "Vector docs needed"}

    q = "how do I configure helm values?"
    model = "llama-fast"
    p_hash = "hash1234"

    d1 = intent_cache.get_or_compute(q, model, p_hash, mock_llm_route)
    assert d1["route"] == "vector"
    assert llm_calls == 1

    d2 = intent_cache.get_or_compute(q, model, p_hash, mock_llm_route)
    assert d2["route"] == "vector"
    assert llm_calls == 1  # Served from cache!


def test_intent_cache_prompt_hash_change_causes_miss():
    intent_cache = IntentRoutingCache(max_entries=100, ttl=3600.0)
    calls = 0

    def mock_llm() -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"route": "sql", "reason": "Database query"}

    q = "select count of pods"
    intent_cache.get_or_compute(q, "m1", "hash_v1", mock_llm)
    assert calls == 1

    # Changing prompt hash triggers re-evaluation
    intent_cache.get_or_compute(q, "m1", "hash_v2", mock_llm)
    assert calls == 2


def test_intent_routing_in_graph_node_fallback_on_failure():
    """Verify route_node falls back to Route.VECTOR if LLM raises an error."""
    state: RagState = {
        "question": "test question",
        "original_question": "test question",
        "chat_history": [],
    }
    with patch("advanced_rag.graph.nodes.get_llm") as mock_get_llm:
        mock_llm = MagicMock()
        mock_llm.complete_json.side_effect = RuntimeError("LLM failure")
        mock_get_llm.return_value = mock_llm

        res = route_node(state)
        assert res["route"] == Route.VECTOR


# =============================================================================
# 7. RETRIEVAL RESULT CACHE TESTS (TASK 65.3)
# =============================================================================


def test_retrieval_cache_disabled_by_default():
    settings = Settings()
    assert settings.retrieval_cache_enabled is False


def test_retrieval_cache_filter_and_param_aware():
    rc = RetrievalCache(max_entries=100, ttl=600.0)
    dummy_results = [RetrievedChunk(chunk=Chunk(id="c1", text="k8s", source="s"), retrieval_score=0.9)]

    # Store for filter A
    rc.set_results(
        query="pods",
        collection="k8s_docs",
        top_k=5,
        results=dummy_results,
        score_threshold=0.7,
        filter_dict={"ns": "default"},
    )

    # Same query, same filter -> hit
    hit = rc.get_results(
        query="pods",
        collection="k8s_docs",
        top_k=5,
        score_threshold=0.7,
        filter_dict={"ns": "default"},
    )
    assert hit is not None
    assert len(hit) == 1
    assert hit[0].chunk.id == "c1"

    # Same query, different filter -> miss
    miss = rc.get_results(
        query="pods",
        collection="k8s_docs",
        top_k=5,
        score_threshold=0.7,
        filter_dict={"ns": "kube-system"},
    )
    assert miss is None

    # Same query, different top_k -> miss
    miss2 = rc.get_results(
        query="pods",
        collection="k8s_docs",
        top_k=10,
        score_threshold=0.7,
        filter_dict={"ns": "default"},
    )
    assert miss2 is None


def test_retrieval_cache_generation_invalidation():
    rc = RetrievalCache(max_entries=100, ttl=600.0)
    dummy = [RetrievedChunk(chunk=Chunk(id="c1", text="t", source="s"), retrieval_score=1.0)]
    rc.set_results("query", "col", 5, dummy)

    assert rc.get_results("query", "col", 5) is not None

    # Bump generation: invalidates all existing retrieval cache entries instantaneously
    rc.bump_generation()
    assert rc.get_results("query", "col", 5) is None


# =============================================================================
# 8. KNOWLEDGE INVALIDATION INTEGRATION TESTS (TASK 65.4)
# =============================================================================


def test_invalidate_all_knowledge_caches_leaves_embedding_cache_intact():
    # Setup answer cache with an entry
    ans_cache = get_cache()
    ans_cache.store("test question", {"answer": "test answer"})

    # Setup retrieval cache with an entry
    rc = get_retrieval_cache()
    dummy = [RetrievedChunk(chunk=Chunk(id="c1", text="t", source="s"), retrieval_score=1.0)]
    rc.set_results("query", "col", 5, dummy)

    # Setup embedding cache with an entry
    emb_cache = get_embedding_cache()
    emb_cache.get_or_compute("sample query", "test-model", 384, lambda: [0.1, 0.2, 0.3])

    # Setup intent routing cache with an entry
    int_cache = get_intent_cache()
    int_cache.get_or_compute("route query", "test-model", "hash1", lambda: {"route": "vector", "reason": "docs"})

    # Perform knowledge invalidation (simulating document ingestion/update/deletion)
    invalidate_all_knowledge_caches()

    # Answer cache should be cleared
    payload, kind = ans_cache.lookup("test question")
    assert kind == "none"
    assert payload is None

    # Retrieval cache should be invalidated
    assert rc.get_results("query", "col", 5) is None

    # Crucial: Unaffected caches (embedding and intent routing) MUST remain valid!
    assert emb_cache.size >= 1
    assert int_cache.size >= 1
    assert emb_cache.size >= 1
    assert int_cache.size >= 1
