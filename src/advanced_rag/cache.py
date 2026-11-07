"""This cache has 2 levels:

Exact Cache → Same question text.
Semantic Cache → Similar question meaning.

## Two-tier answer cache: exact key lookup, then semantic nearest-neighbour.

Exact caching only helps when two engineers type the same question character for
character, which is rare. The semantic tier embeds the question and reuses an
answer whose stored question sits above SEMANTIC_CACHE_THRESHOLD cosine
similarity - that is what actually cuts cost on a support-style workload.

Redis is optional. With REDIS_URL unset the same interface runs over an
in-process dict, so caching behaviour is identical in tests and on a laptop."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from typing import Any, Protocol

from advanced_rag.config import Settings, get_settings

logger = logging.getLogger(__name__)

_EXACT_PREFIX = "rag:exact:"
_SEMANTIC_KEY = "rag:semantic"


class Backend(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str, ttl: int) -> None: ...
    def entries(self, key: str) -> list[str]: ...
    def append(self, key: str, value: str, max_len: int) -> None: ...
    def clear(self) -> None: ...


class MemoryBackend:
    """In-process fallback with the same TTL semantics as the Redis backend."""

    def __init__(self) -> None:
        self._values: dict[str, tuple[float, str]] = {}
        self._lists: dict[str, list[str]] = {}

    def get(self, key: str) -> str | None:
        found = self._values.get(key)
        if found is None:
            return None
        expires_at, value = found
        if expires_at < time.time():
            del self._values[key]
            return None
        return value

    def set(self, key: str, value: str, ttl: int) -> None:
        self._values[key] = (time.time() + ttl, value)

    def entries(self, key: str) -> list[str]:
        return list(self._lists.get(key, []))

    def append(self, key: str, value: str, max_len: int) -> None:
        bucket = self._lists.setdefault(key, [])
        bucket.append(value)
        if len(bucket) > max_len:
            del bucket[: len(bucket) - max_len]

    def clear(self) -> None:
        self._values.clear()
        self._lists.clear()


class RedisBackend:
    def __init__(self, url: str) -> None:
        import redis

        self._redis = redis.Redis.from_url(url, decode_responses=True)
        self._redis.ping()

    def get(self, key: str) -> str | None:
        return self._redis.get(key)

    def set(self, key: str, value: str, ttl: int) -> None:
        self._redis.set(key, value, ex=ttl)

    def entries(self, key: str) -> list[str]:
        return self._redis.lrange(key, 0, -1)

    def append(self, key: str, value: str, max_len: int) -> None:
        pipe = self._redis.pipeline()
        pipe.rpush(key, value)
        pipe.ltrim(key, -max_len, -1)
        pipe.execute()

    def clear(self) -> None:
        for key in self._redis.scan_iter(match="rag:*"):
            self._redis.delete(key)


class AnswerCache:
    """Question -> answer cache with an exact and a semantic tier."""

    def __init__(
        self,
        settings: Settings | None = None,
        backend: Backend | None = None,
        embed_query=None,
        max_semantic_entries: int = 500,
    ) -> None:
        self.settings = settings or get_settings()
        self.max_semantic_entries = max_semantic_entries
        self._embed_query = embed_query
        #: Latched once the embedder proves unobtainable. Without this the
        #: semantic tier logs a full traceback on every single request on a
        #: network that blocks the model download - the exact tier still works,
        #: so the condition is worth one warning, not one per question.
        self._semantic_disabled = False
        self._backend = backend or self._build_backend()

    def _build_backend(self) -> Backend:
        url = self.settings.redis_url                               # If Redis URL exists
        if not url:                                                 # If Redis URL missing 
            logger.info("REDIS_URL unset - using in-process answer cache")
            return MemoryBackend()
        try:
            backend = RedisBackend(url)
            logger.info("Answer cache using Redis at %s", url)
            return backend
        except Exception:
            logger.warning("Redis unreachable at %s - falling back to memory cache", url)
            return MemoryBackend()

    def embed(self, text: str) -> list[float]:                      # Question is converted to embeddings:
        if self._embed_query is None:
            from advanced_rag.retrieval.embeddings import get_embedder

            self._embed_query = get_embedder().embed_query
        return self._embed_query(text)

    def _embed_for_semantic(self, text: str) -> list[float] | None:
        """Embed for the semantic tier, or None if that tier cannot work here.

        The semantic tier is an optimisation on top of the exact tier. Losing it
        degrades hit rate, never correctness, so it is disabled rather than
        retried once the embedder has failed.
        """
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

    # -------------------------------------------------------------------- api

    def lookup(self, question: str) -> tuple[dict[str, Any] | None, str]:                   # User asks a question
        """Return (payload, kind) where kind is "exact", "semantic" or "none"."""
        if not self.settings.enable_cache:
            return None, "none"

        raw = self._backend.get(_EXACT_PREFIX + _fingerprint(question))
        if raw:
            logger.info("Answer cache hit (exact)")
            return json.loads(raw), "exact"

        hit = self._semantic_lookup(question)
        if hit is not None:
            return hit, "semantic"
        return None, "none"

    def store(self, question: str, payload: dict[str, Any]) -> None:                    # Compare with Stored Questions
        if not self.settings.enable_cache:                                              # Loop through cached semantic entries:
            return
        serialized = json.dumps(payload, ensure_ascii=False)
        self._backend.set(
            _EXACT_PREFIX + _fingerprint(question), serialized, self.settings.cache_ttl_seconds
        )
        vector = self._embed_for_semantic(question)
        if vector is None:
            return
        self._backend.append(                                                           # Store New Answer
            _SEMANTIC_KEY,
            json.dumps(                                                                 # Each entry contains:
                {
                    "question": question,
                    "vector": vector,
                    "expires_at": time.time() + self.settings.cache_ttl_seconds,
                    "payload": payload,
                },
                ensure_ascii=False,
            ),
            self.max_semantic_entries,
        )

    def clear(self) -> None:
        self._backend.clear()

    # ---------------------------------------------------------------- private

    def _semantic_lookup(self, question: str) -> dict[str, Any] | None:             # If Exact Miss → Semantic Search
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
            score = _cosine(vector, entry["vector"])                # score = _cosine(new_vector, stored_vector)
            if best is None or score > best[0]:
                best = (score, entry)

        if best is None or best[0] < self.settings.semantic_cache_threshold:        # Check Threshold: if score > SEMANTIC_CACHE_THRESHOLD; Semantic cache hit; Return stored answer
            return None                                                             # No Match Found; Application calls the LLM/RAG pipeline.
        logger.info(
            "Answer cache hit (semantic, similarity=%.3f to %r)", best[0], best[1]["question"]
        )
        return best[1]["payload"]                                   # Pick Best Match


def _fingerprint(question: str) -> str:                         # Check Exact Cache First
                                                                # The question is normalized and converted into a hash:
    return hashlib.sha256(" ".join(question.lower().split()).encode()).hexdigest()[:32]


def _cosine(a: list[float], b: list[float]) -> float:           # Calculate Cosine Similarity
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


_cache: AnswerCache | None = None


def get_cache() -> AnswerCache:
    global _cache
    if _cache is None:
        _cache = AnswerCache()
    return _cache

"""
User Question
      │
      ▼
Exact Cache Lookup
      │
 ┌────┴────┐
 │ Hit     │
 ▼         │
Return     │
            ▼
      Semantic Lookup
            │
      Embed Question
            │
     Cosine Similarity
            │
 ┌──────┴──────┐
 │ Threshold ? │
 └──────┬──────┘
        │Yes
        ▼
 Return Cached Answer
        │
       No
        ▼
     Call RAG/LLM
        │
 Generate Answer
        │
 Store in Exact Cache
        │
 Store in Semantic Cache
        ▼
      Return
      
"This cache uses a two-tier strategy: first it performs an exact hash-based lookup for identical questions, 
and if that fails, it performs embedding-based semantic similarity search using cosine similarity 
to reuse answers for semantically similar questions, reducing LLM calls and cost."
"""