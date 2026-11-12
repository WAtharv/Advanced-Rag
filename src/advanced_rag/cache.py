"""High-performance multi-tier caching subsystem.

Architecture:
1. In-Process Core (Task 64):
   - Concurrency-safe LRU eviction via collections.OrderedDict and threading.RLock.
   - Monotonic time expiration (time.monotonic()) immune to NTP/wall-clock adjustments.
   - SingleFlight request coalescing to deduplicate concurrent identical in-flight operations.
   - Granular operations: get, set, delete, clear, invalidate, cleanup_expired, stats.

2. Intelligent Domain Caches (Task 65):
   - AnswerCache: Two-tier exact and semantic query-to-answer cache.
   - EmbeddingCache: Caches expensive dense and sparse query embeddings.
   - IntentRoutingCache: Caches deterministic LLM routing outcomes (RouteDecision).
   - RetrievalCache: Benchmark-gated result cache with generation-based invalidation.
   - Knowledge Invalidation: Coordinated invalidation across answer and retrieval tiers
     upon document ingestion, update, deletion, or corpus sync.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import logging
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

from advanced_rag.config import Settings, get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T")
V = TypeVar("V")

_EXACT_PREFIX = "rag:exact:"
_SEMANTIC_KEY = "rag:semantic"


# =============================================================================
# 1. METRICS & CACHE ENTRY MODELS
# =============================================================================


@dataclass(frozen=True)
class CacheStats:
    """Thread-safe snapshot of cache performance metrics."""

    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expired: int = 0
    count: int = 0
    max_entries: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return (self.hits / total) if total > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "expired": self.expired,
            "count": self.count,
            "max_entries": self.max_entries,
            "hit_rate": round(self.hit_rate, 4),
        }


@dataclass
class CacheEntry(Generic[V]):
    """Internal cache entry wrapper with monotonic expiration."""

    key: str
    value: V
    expires_at: float  # monotonic timestamp (float('inf') for no expiration)
    created_at: float  # monotonic timestamp
    sliding: bool = False
    ttl: float = 0.0


# =============================================================================
# 2. SINGLE-FLIGHT REQUEST COALESCING
# =============================================================================


class SingleFlight(Generic[T]):
    """Coalesces concurrent identical in-flight calls to execute once and share the result.

    Guarantees:
    - Only one computation runs concurrently for a given key.
    - Subsequent callers wait on an event and receive the exact same result or exception.
    - Exceptions never leave waiting threads permanently blocked.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: dict[str, tuple[threading.Event, list[Any]]] = {}

    def execute(self, key: str, fn: Callable[[], T]) -> T:
        with self._lock:
            if key in self._calls:
                event, holder = self._calls[key]
                is_leader = False
            else:
                event = threading.Event()
                holder = [None, None]  # [result, exception]
                self._calls[key] = (event, holder)
                is_leader = True

        if not is_leader:
            # Wait for leader to complete
            event.wait()
            res, exc = holder
            if exc is not None:
                raise exc
            return res  # type: ignore[return-value]

        try:
            res = fn()
            holder[0] = res
            return res
        except Exception as exc:
            holder[1] = exc
            raise
        finally:
            with self._lock:
                self._calls.pop(key, None)
            event.set()


# =============================================================================
# 3. GENERIC IN-PROCESS LRU CACHE WITH MONOTONIC TTL
# =============================================================================


class LRUCache(Generic[V]):
    """Thread-safe in-process LRU cache with monotonic TTL expiration.

    Features:
    - O(1) lookups and updates via OrderedDict.
    - Monotonic clock (time.monotonic()) protects against system clock jumps/skew.
    - Configurable capacity ceiling with predictable LRU eviction.
    - Optional sliding-window expiration.
    - Thread-safety via reentrant mutex (threading.RLock).
    """

    def __init__(self, max_entries: int = 1000, default_ttl: float = 3600.0) -> None:
        self.max_entries = max(1, int(max_entries))
        self.default_ttl = max(0.0, float(default_ttl))
        self._lock = threading.RLock()
        self._entries: OrderedDict[str, CacheEntry[V]] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._expired = 0

    def get(self, key: str) -> V | None:
        """Retrieve an entry by key. Returns None if missing or expired."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None

            now = time.monotonic()
            if entry.expires_at <= now:
                # Expired: evict lazily
                del self._entries[key]
                self._expired += 1
                self._misses += 1
                return None

            # Mark as most recently used
            self._entries.move_to_end(key)
            if entry.sliding and entry.ttl > 0:
                entry.expires_at = now + entry.ttl

            self._hits += 1
            return entry.value

    def set(
        self,
        key: str,
        value: V,
        ttl: float | None = None,
        sliding: bool = False,
    ) -> None:
        """Store or update an entry with an optional TTL (in seconds)."""
        now = time.monotonic()
        if ttl is not None:
            effective_ttl = float(ttl)
            if effective_ttl == float("inf"):
                expires_at = float("inf")
            elif effective_ttl <= 0:
                expires_at = now - 1.0
            else:
                expires_at = now + effective_ttl
        else:
            effective_ttl = self.default_ttl
            if effective_ttl > 0:
                expires_at = now + effective_ttl
            else:
                expires_at = float("inf")

        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
                self._entries[key] = CacheEntry(
                    key=key,
                    value=value,
                    expires_at=expires_at,
                    created_at=now,
                    sliding=sliding,
                    ttl=effective_ttl,
                )
                return

            # Check capacity; prune expired entries first before evicting live entries
            if len(self._entries) >= self.max_entries:
                self._cleanup_expired_locked(now)

            if len(self._entries) >= self.max_entries:
                # Evict oldest (least recently used)
                self._entries.popitem(last=False)
                self._evictions += 1

            self._entries[key] = CacheEntry(
                key=key,
                value=value,
                expires_at=expires_at,
                created_at=now,
                sliding=sliding,
                ttl=effective_ttl,
            )

    def delete(self, key: str) -> bool:
        """Remove a specific key. Returns True if key existed."""
        with self._lock:
            if key in self._entries:
                del self._entries[key]
                return True
            return False

    def clear(self) -> None:
        """Flush all cache entries."""
        with self._lock:
            self._entries.clear()

    def invalidate(self, prefix_or_predicate: str | Callable[[str], bool]) -> int:
        """Invalidate entries matching a prefix string or predicate function."""
        with self._lock:
            if isinstance(prefix_or_predicate, str):
                prefix = prefix_or_predicate
                to_delete = [k for k in self._entries if k.startswith(prefix)]
            else:
                predicate = prefix_or_predicate
                to_delete = [k for k in self._entries if predicate(k)]

            for k in to_delete:
                del self._entries[k]
            return len(to_delete)

    def cleanup_expired(self) -> int:
        """Active pruning of all expired entries."""
        with self._lock:
            return self._cleanup_expired_locked(time.monotonic())

    def _cleanup_expired_locked(self, now: float) -> int:
        expired_keys = [k for k, v in self._entries.items() if v.expires_at <= now]
        for k in expired_keys:
            del self._entries[k]
            self._expired += 1
        return len(expired_keys)

    def stats(self) -> CacheStats:
        """Return a snapshot of metrics."""
        with self._lock:
            return CacheStats(
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
                expired=self._expired,
                count=len(self._entries),
                max_entries=self.max_entries,
            )

    def reset_stats(self) -> None:
        with self._lock:
            self._hits = 0
            self._misses = 0
            self._evictions = 0
            self._expired = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __contains__(self, key: str) -> bool:
        with self._lock:
            if key not in self._entries:
                return False
            if self._entries[key].expires_at <= time.monotonic():
                return False
            return True


# =============================================================================
# 4. REDIS SHARED CACHE & STORAGE BACKENDS (TASK 68)
# =============================================================================


class RedisSharedCache:
    """Thread-safe Redis shared cache backend for multi-instance deployments.

    Guarantees:
    - Connection pooling and bounded connect/socket timeouts.
    - TLS compatibility (rediss:// or SSL parameters).
    - Hard ceiling on maximum payload size (REDIS_MAX_PAYLOAD_BYTES).
    - Namespaced cache keys with application and schema version (e.g. rag:v1:...).
    - Strictly structured JSON serialization (no pickle).
    - Fail-soft resilience: timeouts or errors return None (cache miss) or False (skipped write)
      without interrupting live request execution or dropping computations.
    """

    def __init__(
        self,
        url: str,
        connect_timeout: float = 2.0,
        socket_timeout: float = 2.0,
        max_payload_bytes: int = 1_048_576,
        key_prefix: str = "rag:v1",
        default_ttl: int = 3600,
    ) -> None:
        import redis

        self.url = url
        self.connect_timeout = float(connect_timeout)
        self.socket_timeout = float(socket_timeout)
        self.max_payload_bytes = int(max_payload_bytes)
        self.key_prefix = key_prefix.strip(":")
        self.default_ttl = int(default_ttl)

        self._pool = redis.ConnectionPool.from_url(
            url,
            socket_connect_timeout=self.connect_timeout,
            socket_timeout=self.socket_timeout,
            decode_responses=True,
        )
        self._client = redis.Redis(connection_pool=self._pool)
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._writes = 0
        self._errors = 0

    def _qualify(self, key: str) -> str:
        k = key.strip(":")
        if k.startswith(self.key_prefix):
            return k
        return f"{self.key_prefix}:{k}"

    def get(self, key: str) -> str | None:
        try:
            full_key = self._qualify(key)
            val = self._client.get(full_key)
            if val is not None:
                with self._lock:
                    self._hits += 1
                return val
            with self._lock:
                self._misses += 1
            return None
        except Exception as exc:
            with self._lock:
                self._errors += 1
                self._misses += 1
            logger.debug("Redis shared cache get failed (%s: %s) for key %s", type(exc).__name__, exc, key)
            return None

    def set(self, key: str, value: str, ttl: int | None = None) -> bool:
        try:
            val_bytes = value.encode("utf-8")
            if len(val_bytes) > self.max_payload_bytes:
                logger.warning(
                    "Redis payload size (%d bytes) exceeds ceiling (%d bytes); skipping cache write for %s",
                    len(val_bytes),
                    self.max_payload_bytes,
                    key,
                )
                return False
            full_key = self._qualify(key)
            effective_ttl = ttl if ttl is not None else self.default_ttl
            if effective_ttl > 0:
                self._client.set(full_key, value, ex=effective_ttl)
            else:
                self._client.set(full_key, value)
            with self._lock:
                self._writes += 1
            return True
        except Exception as exc:
            with self._lock:
                self._errors += 1
            logger.debug("Redis shared cache set failed (%s: %s) for key %s", type(exc).__name__, exc, key)
            return False

    def delete(self, key: str) -> bool:
        try:
            full_key = self._qualify(key)
            res = self._client.delete(full_key)
            return bool(res)
        except Exception as exc:
            with self._lock:
                self._errors += 1
            logger.debug("Redis delete failed (%s: %s) for key %s", type(exc).__name__, exc, key)
            return False

    def clear(self, pattern: str | None = None) -> int:
        try:
            pat = f"{self.key_prefix}:{pattern}" if pattern else f"{self.key_prefix}:*"
            keys = list(self._client.scan_iter(match=pat, count=200))
            if keys:
                return self._client.delete(*keys)
            return 0
        except Exception as exc:
            with self._lock:
                self._errors += 1
            logger.debug("Redis clear failed (%s: %s)", type(exc).__name__, exc)
            return 0

    def incr(self, key: str) -> int:
        try:
            full_key = self._qualify(key)
            return self._client.incr(full_key)
        except Exception as exc:
            with self._lock:
                self._errors += 1
            logger.debug("Redis incr failed (%s: %s)", type(exc).__name__, exc)
            return 0

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            full_key = self._qualify(key)
            val = self._client.get(full_key)
            return int(val) if val is not None else default
        except Exception:
            return default

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:
            return False

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "backend": "redis",
                "connected": self.ping(),
                "hits": self._hits,
                "misses": self._misses,
                "writes": self._writes,
                "errors": self._errors,
                "prefix": self.key_prefix,
                "max_payload_bytes": self.max_payload_bytes,
            }

    def close(self) -> None:
        try:
            self._client.close()
            self._pool.disconnect()
        except Exception:
            pass


class Backend(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str, ttl: int) -> None: ...
    def entries(self, key: str) -> list[str]: ...
    def append(self, key: str, value: str, max_len: int) -> None: ...
    def clear(self) -> None: ...


class MemoryBackend:
    """Upgraded in-process backend powered by thread-safe LRUCache.

    Retains 100% protocol backward compatibility with existing tests and AnswerCache.
    """

    def __init__(self, max_entries: int = 1000, default_ttl: int = 3600) -> None:
        self._values = LRUCache[str](max_entries=max_entries, default_ttl=float(default_ttl))
        self._lists_lock = threading.RLock()
        self._lists: dict[str, list[tuple[float, str]]] = {}  # key -> list[(expires_at, raw_json)]

    def get(self, key: str) -> str | None:
        return self._values.get(key)

    def set(self, key: str, value: str, ttl: int) -> None:
        self._values.set(key, value, ttl=float(ttl))

    def entries(self, key: str) -> list[str]:
        now = time.monotonic()
        with self._lists_lock:
            bucket = self._lists.get(key, [])
            valid: list[str] = []
            expired_indices: list[int] = []
            for idx, (exp, raw) in enumerate(bucket):
                if exp > now:
                    valid.append(raw)
                else:
                    expired_indices.append(idx)
            # Prune expired items from the list in reverse
            if expired_indices:
                for idx in reversed(expired_indices):
                    del bucket[idx]
            return valid

    def append(self, key: str, value: str, max_len: int) -> None:
        now = time.monotonic()
        # Extract expires_at from JSON if possible, else default to 1 hour
        exp = now + 3600.0
        try:
            parsed = json.loads(value)
            if "expires_at" in parsed and isinstance(parsed["expires_at"], (int, float)):
                # If expires_at is wall-clock, convert to monotonic delta
                delta = max(0.0, parsed["expires_at"] - time.time())
                exp = now + delta
        except Exception:
            pass

        with self._lists_lock:
            bucket = self._lists.setdefault(key, [])
            bucket.append((exp, value))
            if len(bucket) > max_len:
                del bucket[: len(bucket) - max_len]

    def clear(self) -> None:
        self._values.clear()
        with self._lists_lock:
            self._lists.clear()

    def stats(self) -> CacheStats:
        return self._values.stats()


class RedisBackend:
    """Redis backend for multi-replica shared answer caching with connection pooling and timeouts."""

    def __init__(self, url: str, settings: Settings | None = None) -> None:
        st = settings or get_settings()
        self._shared = RedisSharedCache(
            url=url,
            connect_timeout=getattr(st, "redis_connect_timeout", 2.0),
            socket_timeout=getattr(st, "redis_socket_timeout", 2.0),
            max_payload_bytes=getattr(st, "redis_max_payload_bytes", 1_048_576),
            key_prefix=getattr(st, "redis_key_prefix", "rag:v1"),
            default_ttl=getattr(st, "cache_default_ttl", 3600),
        )
        # Verify connection on init; raises if unreachable
        if not self._shared.ping():
            raise RuntimeError(f"Unable to ping Redis at {url}")

    def get(self, key: str) -> str | None:
        return self._shared.get(key)

    def set(self, key: str, value: str, ttl: int) -> None:
        self._shared.set(key, value, ttl=ttl)

    def entries(self, key: str) -> list[str]:
        try:
            full_key = self._shared._qualify(key)
            return self._shared._client.lrange(full_key, 0, -1)
        except Exception:
            return []

    def append(self, key: str, value: str, max_len: int) -> None:
        try:
            if len(value.encode("utf-8")) > self._shared.max_payload_bytes:
                return
            full_key = self._shared._qualify(key)
            pipe = self._shared._client.pipeline()
            pipe.rpush(full_key, value)
            pipe.ltrim(full_key, -max_len, -1)
            pipe.execute()
        except Exception:
            pass

    def clear(self) -> None:
        self._shared.clear()

    def stats(self) -> dict[str, Any]:
        return self._shared.stats()

    def close(self) -> None:
        self._shared.close()


# =============================================================================
# 5. ANSWER CACHE (EXACT & SEMANTIC)
# =============================================================================


class AnswerCache:
    """Two-tier question -> answer cache with exact and semantic tiers."""

    def __init__(
        self,
        settings: Settings | None = None,
        backend: Backend | None = None,
        embed_query: Callable[[str], list[float]] | None = None,
        max_semantic_entries: int = 500,
    ) -> None:
        self.settings = settings or get_settings()
        self.max_semantic_entries = max_semantic_entries
        self._embed_query = embed_query
        self._semantic_disabled = False
        self._backend = backend or self._build_backend()

    def _build_backend(self) -> Backend:
        url = self.settings.redis_url
        if not url:
            logger.info("REDIS_URL unset - using in-process answer cache")
            return MemoryBackend(
                max_entries=getattr(self.settings, "cache_max_entries", 1000),
                default_ttl=getattr(self.settings, "cache_default_ttl", 3600),
            )
        try:
            backend = RedisBackend(url, settings=self.settings)
            logger.info("Answer cache using Redis at %s", url)
            return backend
        except Exception:
            logger.warning("Redis unreachable at %s - falling back to memory cache", url)
            return MemoryBackend(
                max_entries=getattr(self.settings, "cache_max_entries", 1000),
                default_ttl=getattr(self.settings, "cache_default_ttl", 3600),
            )

    def embed(self, text: str) -> list[float]:
        if self._embed_query is None:
            from advanced_rag.retrieval.embeddings import get_embedder

            self._embed_query = get_embedder(self.settings).embed_query
        return self._embed_query(text)

    def _embed_for_semantic(self, text: str) -> list[float] | None:
        if self._semantic_disabled:
            return None
        try:
            return self.embed(text)
        except Exception as exc:
            self._semantic_disabled = True
            logger.warning(
                "Semantic cache tier disabled - no embedder available (%s: %s). "
                "The exact-match tier is unaffected.",
                type(exc).__name__,
                exc,
            )
            return None

    def lookup(self, question: str) -> tuple[dict[str, Any] | None, str]:
        """Return (payload, kind) where kind is 'exact', 'semantic' or 'none'."""
        if not getattr(self.settings, "enable_cache", True):
            return None, "none"

        raw = self._backend.get(_EXACT_PREFIX + _fingerprint(question))
        if raw:
            logger.info("Answer cache hit (exact)")
            return json.loads(raw), "exact"

        hit = self._semantic_lookup(question)
        if hit is not None:
            return hit, "semantic"
        return None, "none"

    def store(self, question: str, payload: dict[str, Any]) -> None:
        if not getattr(self.settings, "enable_cache", True):
            return
        serialized = json.dumps(payload, ensure_ascii=False)
        ttl = getattr(self.settings, "cache_default_ttl", self.settings.cache_ttl_seconds)
        self._backend.set(_EXACT_PREFIX + _fingerprint(question), serialized, ttl)

        vector = self._embed_for_semantic(question)
        if vector is None:
            return
        self._backend.append(
            _SEMANTIC_KEY,
            json.dumps(
                {
                    "question": question,
                    "vector": vector,
                    "expires_at": time.time() + ttl,
                    "payload": payload,
                },
                ensure_ascii=False,
            ),
            self.max_semantic_entries,
        )

    def clear(self) -> None:
        self._backend.clear()

    def stats(self) -> dict[str, Any]:
        if hasattr(self._backend, "stats"):
            res = self._backend.stats()
            return res.to_dict() if hasattr(res, "to_dict") else res
        return {"backend": type(self._backend).__name__}

    def _semantic_lookup(self, question: str) -> dict[str, Any] | None:
        entries = self._backend.entries(_SEMANTIC_KEY)
        if not entries:
            return None
        vector = self._embed_for_semantic(question)
        if vector is None:
            return None

        now = time.time()
        best: tuple[float, dict[str, Any]] | None = None
        for raw in entries:
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if entry.get("expires_at", 0) < now:
                continue
            score = _cosine(vector, entry["vector"])
            if best is None or score > best[0]:
                best = (score, entry)

        if best is None or best[0] < self.settings.semantic_cache_threshold:
            return None
        logger.info(
            "Answer cache hit (semantic, similarity=%.3f to %r)", best[0], best[1]["question"]
        )
        return best[1]["payload"]


# =============================================================================
# 6. INTELLIGENT EMBEDDING CACHE (TASK 65.1 & 68)
# =============================================================================


class EmbeddingCache:
    """Thread-safe two-tier cache for generated dense and sparse text embeddings.

    Guarantees:
    - Namespaced by model identifier and dimension so different models never collide.
    - Normalized text hashing prevents whitespace/case variance.
    - SingleFlight deduplicates identical concurrent embedding calculations.
    - Two-tier hierarchy: L1 in-process LRU cache + optional L2 Redis shared cache.
    - Defensively copies returned vectors to prevent caller mutation of cache state.
    - Failed calculations are never cached.
    """

    def __init__(
        self,
        max_entries: int = 2000,
        ttl: float = 86400.0,
        shared_cache: RedisSharedCache | None = None,
    ) -> None:
        self._cache = LRUCache[tuple[float, ...]](max_entries=max_entries, default_ttl=ttl)
        self._single_flight = SingleFlight[list[float]]()
        self.shared_cache = shared_cache
        self._ttl = ttl

    def get_or_compute_dense(
        self,
        text: str,
        model_name: str,
        dim: int,
        compute_fn: Callable[[], Sequence[float]],
    ) -> list[float]:
        norm_key = f"embed:{model_name}:{dim}:{_fingerprint(text)}"
        cached = self._cache.get(norm_key)
        if cached is not None:
            return list(cached)

        # L2 Shared Cache check
        if self.shared_cache is not None:
            raw = self.shared_cache.get(norm_key)
            if raw is not None:
                try:
                    vec = json.loads(raw)
                    if isinstance(vec, list):
                        self._cache.set(norm_key, tuple(vec))
                        return list(vec)
                except Exception:
                    pass

        def _compute() -> list[float]:
            inner_hit = self._cache.get(norm_key)
            if inner_hit is not None:
                return list(inner_hit)
            if self.shared_cache is not None:
                raw = self.shared_cache.get(norm_key)
                if raw is not None:
                    try:
                        vec = json.loads(raw)
                        if isinstance(vec, list):
                            self._cache.set(norm_key, tuple(vec))
                            return list(vec)
                    except Exception:
                        pass
            vec = list(compute_fn())
            self._cache.set(norm_key, tuple(vec))
            if self.shared_cache is not None:
                try:
                    self.shared_cache.set(norm_key, json.dumps(vec), ttl=int(self._ttl))
                except Exception:
                    pass
            return vec

        result = self._single_flight.execute(norm_key, _compute)
        return list(result)

    def get_or_compute(
        self,
        text: str,
        model_name: str,
        dim: int,
        compute_fn: Callable[[], Sequence[float]],
    ) -> list[float]:
        return self.get_or_compute_dense(text, model_name, dim, compute_fn)

    @property
    def size(self) -> int:
        return len(self._cache)

    def stats(self) -> CacheStats:
        return self._cache.stats()

    def clear(self) -> None:
        self._cache.clear()
        if self.shared_cache is not None:
            self.shared_cache.clear("embed:*")

    def invalidate_model(self, model_name: str) -> int:
        count = self._cache.invalidate(f"embed:{model_name}:")
        if self.shared_cache is not None:
            self.shared_cache.clear(f"embed:{model_name}:*")
        return count


# =============================================================================
# 7. INTELLIGENT INTENT-ROUTING CACHE (TASK 65.2 & 68)
# =============================================================================


class IntentRoutingCache:
    """Thread-safe two-tier cache for deterministic LLM routing outcomes (RouteDecision).

    Guarantees:
    - Caches only successful, validated routing outcomes (VECTOR, SQL, BOTH, REJECT).
    - Keys are namespaced by routing model and prompt version.
    - SingleFlight prevents duplicate LLM invocations for identical concurrent queries.
    - Two-tier hierarchy: L1 in-process LRU cache + optional L2 Redis shared cache.
    - Does NOT cache context-dependent state or dynamic permissions.
    - Failed/errored decisions are never cached.
    """

    def __init__(
        self,
        max_entries: int = 1000,
        ttl: float = 3600.0,
        shared_cache: RedisSharedCache | None = None,
    ) -> None:
        self._cache = LRUCache[dict[str, Any]](max_entries=max_entries, default_ttl=ttl)
        self._single_flight = SingleFlight[dict[str, Any]]()
        self.shared_cache = shared_cache
        self._ttl = ttl

    def get_or_compute(
        self,
        question: str,
        model_name: str,
        prompt_version: str,
        compute_fn: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        norm_key = f"intent:{model_name}:{prompt_version}:{_fingerprint(question)}"
        cached = self._cache.get(norm_key)
        if cached is not None:
            return copy.deepcopy(cached)

        # L2 Shared Cache check
        if self.shared_cache is not None:
            raw = self.shared_cache.get(norm_key)
            if raw is not None:
                try:
                    dec = json.loads(raw)
                    if isinstance(dec, dict) and "route" in dec:
                        self._cache.set(norm_key, copy.deepcopy(dec))
                        return copy.deepcopy(dec)
                except Exception:
                    pass

        def _compute() -> dict[str, Any]:
            inner_hit = self._cache.get(norm_key)
            if inner_hit is not None:
                return copy.deepcopy(inner_hit)
            if self.shared_cache is not None:
                raw = self.shared_cache.get(norm_key)
                if raw is not None:
                    try:
                        dec = json.loads(raw)
                        if isinstance(dec, dict) and "route" in dec:
                            self._cache.set(norm_key, copy.deepcopy(dec))
                            return copy.deepcopy(dec)
                    except Exception:
                        pass
            decision_dict = compute_fn()
            # Only cache if valid route decision
            if decision_dict and "route" in decision_dict:
                self._cache.set(norm_key, copy.deepcopy(decision_dict))
                if self.shared_cache is not None:
                    try:
                        self.shared_cache.set(norm_key, json.dumps(decision_dict), ttl=int(self._ttl))
                    except Exception:
                        pass
            return decision_dict

        result = self._single_flight.execute(norm_key, _compute)
        return copy.deepcopy(result)

    def stats(self) -> CacheStats:
        return self._cache.stats()

    @property
    def size(self) -> int:
        return len(self._cache)

    def clear(self) -> None:
        self._cache.clear()
        if self.shared_cache is not None:
            self.shared_cache.clear("intent:*")

    def invalidate_prompt(self, prompt_version: str) -> int:
        count = self._cache.invalidate("intent:")
        if self.shared_cache is not None:
            self.shared_cache.clear("intent:*")
        return count


# =============================================================================
# 8. BENCHMARK-GATED RETRIEVAL CACHE (TASK 65.3 & 68)
# =============================================================================


class RetrievalCache:
    """Benchmark-gated retrieval result cache with distributed generation support.

    Guarantees:
    - Incorporates collection, mode, fusion, top_k, filters, and query in the key.
    - Defensively copies RetrievedChunk objects to prevent caller contamination.
    - Generation counter provides instant O(1) invalidation on document ingestion or deletion.
    - When Redis is active, generation counter is shared across all instances atomically.
    - Disabled by default unless benchmark proves concrete performance benefit without quality loss.
    """

    def __init__(
        self,
        max_entries: int = 500,
        ttl: float = 600.0,
        shared_cache: RedisSharedCache | None = None,
    ) -> None:
        self._cache = LRUCache[list[dict[str, Any]]](max_entries=max_entries, default_ttl=ttl)
        self._generation = 0
        self._lock = threading.Lock()
        self.shared_cache = shared_cache
        self._default_ttl = ttl

    def get_generation(self, collection: str | None = None) -> int:
        if self.shared_cache is not None:
            col = collection or "global"
            col_gen = self.shared_cache.get_int(f"gen:retrieval:{col}", default=self._generation)
            know_gen = self.shared_cache.get_int("gen:knowledge", default=0)
            return max(self._generation, col_gen, know_gen)
        return self._generation

    def build_key(
        self,
        query: str,
        collection: str,
        mode: str,
        top_k: int,
        fusion: str | None = None,
        filter_dict: dict[str, Any] | None = None,
    ) -> str:
        gen = self.get_generation(collection)
        filter_str = json.dumps(filter_dict or {}, sort_keys=True)
        raw_key = f"{gen}:{collection}:{mode}:{fusion}:{top_k}:{filter_str}:{_fingerprint(query)}"
        return hashlib.sha256(raw_key.encode()).hexdigest()[:32]

    def get(self, key: str) -> list[Any] | None:
        cached = self._cache.get(key)
        if cached is not None:
            return copy.deepcopy(cached)
        if self.shared_cache is not None:
            raw = self.shared_cache.get(f"retrieval:{key}")
            if raw is not None:
                try:
                    data = json.loads(raw)
                    if isinstance(data, list):
                        self._cache.set(key, copy.deepcopy(data))
                        return copy.deepcopy(data)
                except Exception:
                    pass
        return None

    def set(self, key: str, results: list[Any], ttl: float | None = None) -> None:
        self._cache.set(key, copy.deepcopy(results), ttl=ttl)
        if self.shared_cache is not None:
            try:
                serializable = []
                for item in results:
                    if hasattr(item, "to_dict"):
                        serializable.append(item.to_dict())
                    elif isinstance(item, dict):
                        serializable.append(item)
                    elif dataclasses.is_dataclass(item):
                        serializable.append(dataclasses.asdict(item))
                    else:
                        serializable.append(str(item))
                self.shared_cache.set(
                    f"retrieval:{key}",
                    json.dumps(serializable),
                    ttl=int(ttl or self._default_ttl),
                )
            except Exception as exc:
                logger.debug("Failed to set retrieval cache in Redis: %s", exc)

    def get_results(
        self,
        query: str,
        collection: str,
        top_k: int,
        score_threshold: float = 0.0,
        filter_dict: dict[str, Any] | None = None,
        mode: str = "hybrid",
        fusion: str | None = None,
    ) -> list[Any] | None:
        key = self.build_key(
            query=query,
            collection=collection,
            mode=mode,
            top_k=top_k,
            fusion=fusion,
            filter_dict=filter_dict,
        )
        return self.get(key)

    def set_results(
        self,
        query: str,
        collection: str,
        top_k: int,
        results: list[Any],
        score_threshold: float = 0.0,
        filter_dict: dict[str, Any] | None = None,
        mode: str = "hybrid",
        fusion: str | None = None,
        ttl: float | None = None,
    ) -> None:
        key = self.build_key(
            query=query,
            collection=collection,
            mode=mode,
            top_k=top_k,
            fusion=fusion,
            filter_dict=filter_dict,
        )
        self.set(key, results, ttl=ttl)

    def bump_generation(self, collection: str | None = None) -> int:
        """Atomically advances generation, invalidating all current retrieval results in O(1)."""
        with self._lock:
            self._generation += 1
            self._cache.clear()
            if self.shared_cache is not None:
                col = collection or "global"
                shared_gen = self.shared_cache.incr(f"gen:retrieval:{col}")
                return max(self._generation, shared_gen)
            return self._generation

    def clear(self) -> None:
        with self._lock:
            self._generation += 1
            self._cache.clear()
            if self.shared_cache is not None:
                self.shared_cache.incr("gen:retrieval:global")
                self.shared_cache.clear("retrieval:*")

    def stats(self) -> CacheStats:
        return self._cache.stats()


# =============================================================================
# 9. UTILITY FUNCTIONS & SINGLETON ACCESSORS
# =============================================================================


def _fingerprint(text: str) -> str:
    """Compute normalized SHA-256 fingerprint."""
    normalized = " ".join((text or "").lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def _cosine(a: list[float], b: list[float]) -> float:
    """Calculate cosine similarity between two float vectors."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    denom = norm_a * norm_b
    return (dot / denom) if denom else 0.0


# Module-level singletons
_shared_cache: RedisSharedCache | None = None
_answer_cache: AnswerCache | None = None
_embedding_cache: EmbeddingCache | None = None
_intent_cache: IntentRoutingCache | None = None
_retrieval_cache: RetrievalCache | None = None
_singleton_lock = threading.RLock()


def get_shared_cache(settings: Settings | None = None) -> RedisSharedCache | None:
    """Return the global RedisSharedCache instance if configured, else None."""
    global _shared_cache
    if _shared_cache is not None:
        return _shared_cache

    st = settings or get_settings()
    is_redis = (
        getattr(st, "cache_backend", "memory") == "redis"
        or bool(getattr(st, "redis_url", None))
    )
    if not is_redis or not st.redis_url:
        return None

    with _singleton_lock:
        if _shared_cache is None:
            try:
                _shared_cache = RedisSharedCache(
                    url=st.redis_url,
                    connect_timeout=st.redis_connect_timeout,
                    socket_timeout=st.redis_socket_timeout,
                    max_payload_bytes=st.redis_max_payload_bytes,
                    key_prefix=st.redis_key_prefix,
                    default_ttl=st.cache_default_ttl,
                )
                logger.info(
                    "Redis shared cache connected at %s (prefix=%s)",
                    st.redis_url,
                    st.redis_key_prefix,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to initialize Redis shared cache (%s: %s) - using in-process cache",
                    type(exc).__name__,
                    exc,
                )
                _shared_cache = None
    return _shared_cache


def init_cache_backend(settings: Settings | None = None) -> RedisSharedCache | None:
    """Initialize the shared cache backend during application startup."""
    return get_shared_cache(settings)


def close_cache_backend() -> None:
    """Close the shared cache backend and connection pools during application shutdown."""
    global _shared_cache, _answer_cache, _embedding_cache, _intent_cache, _retrieval_cache
    with _singleton_lock:
        if _shared_cache is not None:
            _shared_cache.close()
            _shared_cache = None
        if _answer_cache is not None:
            backend = getattr(_answer_cache, "_backend", None)
            if hasattr(backend, "close"):
                backend.close()
        _answer_cache = None
        _embedding_cache = None
        _intent_cache = None
        _retrieval_cache = None


def get_cache() -> AnswerCache:
    """Return the global AnswerCache instance."""
    global _answer_cache
    if _answer_cache is None:
        with _singleton_lock:
            if _answer_cache is None:
                _answer_cache = AnswerCache()
    return _answer_cache


def get_embedding_cache() -> EmbeddingCache:
    """Return the global EmbeddingCache instance."""
    global _embedding_cache
    if _embedding_cache is None:
        with _singleton_lock:
            if _embedding_cache is None:
                settings = get_settings()
                _embedding_cache = EmbeddingCache(
                    max_entries=getattr(settings, "embedding_cache_max_entries", 2000),
                    ttl=float(getattr(settings, "embedding_cache_ttl", 86400)),
                    shared_cache=get_shared_cache(settings),
                )
    return _embedding_cache


def get_intent_cache() -> IntentRoutingCache:
    """Return the global IntentRoutingCache instance."""
    global _intent_cache
    if _intent_cache is None:
        with _singleton_lock:
            if _intent_cache is None:
                settings = get_settings()
                _intent_cache = IntentRoutingCache(
                    max_entries=getattr(settings, "intent_cache_max_entries", 1000),
                    ttl=float(getattr(settings, "intent_cache_ttl", 3600)),
                    shared_cache=get_shared_cache(settings),
                )
    return _intent_cache


def get_retrieval_cache() -> RetrievalCache:
    """Return the global RetrievalCache instance."""
    global _retrieval_cache
    if _retrieval_cache is None:
        with _singleton_lock:
            if _retrieval_cache is None:
                settings = get_settings()
                _retrieval_cache = RetrievalCache(
                    max_entries=getattr(settings, "retrieval_cache_max_entries", 500),
                    ttl=float(getattr(settings, "retrieval_cache_ttl", 600)),
                    shared_cache=get_shared_cache(settings),
                )
    return _retrieval_cache


def invalidate_all_knowledge_caches() -> dict[str, int]:
    """Coordinated invalidation across AnswerCache and RetrievalCache.

    Called upon document ingestion, modification, deletion, or corpus sync
    to strictly guarantee that no stale knowledge is ever returned.
    """
    logger.info("Invalidating all knowledge caches (AnswerCache and RetrievalCache)")
    get_cache().clear()
    ret_cache = get_retrieval_cache()
    new_gen = ret_cache.bump_generation()
    shared = get_shared_cache()
    if shared is not None:
        shared.incr("gen:knowledge")
        shared.clear("exact:*")
    return {"answer_cache_cleared": 1, "retrieval_cache_generation": new_gen}


def get_all_cache_stats() -> dict[str, Any]:
    """Retrieve combined observability statistics across all cache tiers."""
    stats = {
        "answer_cache": get_cache().stats(),
        "embedding_cache": get_embedding_cache().stats().to_dict(),
        "intent_cache": get_intent_cache().stats().to_dict(),
        "retrieval_cache": get_retrieval_cache().stats().to_dict(),
    }
    shared = get_shared_cache()
    if shared is not None:
        stats["shared_cache"] = shared.stats()
    return stats
