from advanced_rag.cache import AnswerCache, MemoryBackend
from advanced_rag.config import Settings
from advanced_rag.evaluation.retrieval_metrics import (
    fact_coverage,
    hit_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    score_all,
)
from advanced_rag.models import Chunk, RetrievedChunk


def hit(source: str, score: float = 1.0) -> RetrievedChunk:
    return RetrievedChunk(
        chunk=Chunk(id=source + str(score), text="t", source=source), retrieval_score=score
    )


# ------------------------------------------------------------------- cache


def fake_embedder():
    """Deterministic 3-d 'embedding' keyed on words, so similarity is predictable."""
    vocabulary = ["crashloop", "oom", "ingress"]

    def embed(text: str) -> list[float]:
        lowered = text.lower()
        vector = [1.0 if word in lowered else 0.0 for word in vocabulary]
        return vector if any(vector) else [0.1, 0.1, 0.1]

    return embed


def build_cache(**overrides) -> AnswerCache:
    settings = Settings(enable_cache=True, cache_ttl_seconds=60, **overrides)
    return AnswerCache(settings=settings, backend=MemoryBackend(), embed_query=fake_embedder())


def test_exact_cache_hit():
    cache = build_cache()
    cache.store("Why is my pod in crashloop?", {"answer": "because"})
    payload, kind = cache.lookup("Why is my pod in crashloop?")
    assert kind == "exact"
    assert payload["answer"] == "because"


def test_exact_cache_is_whitespace_and_case_insensitive():
    cache = build_cache()
    cache.store("Why  is my POD in crashloop?", {"answer": "because"})
    payload, kind = cache.lookup("why is my pod in   crashloop?")
    assert kind == "exact"
    assert payload is not None


def test_semantic_cache_hit_on_paraphrase():
    cache = build_cache(semantic_cache_threshold=0.9)
    cache.store("pod is in crashloop", {"answer": "restart loop"})
    payload, kind = cache.lookup("my container keeps hitting crashloop again")
    assert kind == "semantic"
    assert payload["answer"] == "restart loop"


def test_semantic_cache_misses_unrelated_question():
    cache = build_cache(semantic_cache_threshold=0.9)
    cache.store("pod is in crashloop", {"answer": "restart loop"})
    payload, kind = cache.lookup("ingress is returning errors")
    assert kind == "none"
    assert payload is None


def test_cache_disabled_never_hits():
    cache = build_cache()
    cache.store("q", {"answer": "a"})
    cache.settings = Settings(enable_cache=False)
    assert cache.lookup("q") == (None, "none")


def test_cache_clear():
    cache = build_cache()
    cache.store("q", {"answer": "a"})
    cache.clear()
    assert cache.lookup("q")[1] == "none"


def test_memory_backend_ttl_expiry():
    backend = MemoryBackend()
    backend.set("k", "v", ttl=-1)
    assert backend.get("k") is None


def test_memory_backend_list_is_capped():
    backend = MemoryBackend()
    for index in range(10):
        backend.append("l", str(index), max_len=3)
    assert backend.entries("l") == ["7", "8", "9"]


# ----------------------------------------------------------------- metrics


def test_hit_and_recall():
    retrieved = [hit("a.md"), hit("b.md"), hit("c.md")]
    assert hit_at_k(retrieved, ["b.md"], 3) == 1.0
    assert hit_at_k(retrieved, ["z.md"], 3) == 0.0
    assert recall_at_k(retrieved, ["a.md", "z.md"], 3) == 0.5


def test_reciprocal_rank_rewards_position():
    assert reciprocal_rank([hit("a.md"), hit("b.md")], ["a.md"]) == 1.0
    assert reciprocal_rank([hit("a.md"), hit("b.md")], ["b.md"]) == 0.5
    assert reciprocal_rank([hit("a.md")], ["z.md"]) == 0.0


def test_precision_at_k():
    retrieved = [hit("a.md"), hit("b.md"), hit("c.md"), hit("d.md")]
    assert precision_at_k(retrieved, ["a.md", "b.md"], 4) == 0.5


def test_ndcg_prefers_earlier_hits():
    early = ndcg_at_k([hit("a.md"), hit("x.md"), hit("y.md")], ["a.md"], 3)
    late = ndcg_at_k([hit("x.md"), hit("y.md"), hit("a.md")], ["a.md"], 3)
    assert early == 1.0
    assert late < early


def test_score_all_skips_cases_without_expectations():
    scores = score_all([([hit("a.md")], ["a.md"]), ([hit("b.md")], [])], k=1)
    assert scores.cases == 1
    assert scores.hit_rate == 1.0


def test_fact_coverage():
    assert fact_coverage("Exit code 137 means OOMKilled", ["137", "oomkilled"]) == 1.0
    assert fact_coverage("Exit code 137", ["137", "oomkilled"]) == 0.5
    assert fact_coverage("anything", []) == 1.0
