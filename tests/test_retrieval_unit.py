"""Retrieval logic that can be tested without loading a model.

The fusion arithmetic, score normalisation and context formatting are where
ranking bugs actually hide, and none of them need an embedder.
"""

from __future__ import annotations

import pytest

from advanced_rag.config import Settings
from advanced_rag.models import Chunk, RetrievedChunk
from advanced_rag.retrieval.embeddings import normalize_scores, sigmoid
from advanced_rag.retrieval.retriever import format_context
from advanced_rag.retrieval.vectorstore import HybridStore


def hit(source: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk=Chunk(id=source, text=f"text of {source}", source=source, section="S"),
        retrieval_score=score,
    )


# ----------------------------------------------------------- normalisation


def test_normalize_scores_maps_to_unit_range():
    assert normalize_scores([1.0, 2.0, 3.0]) == [0.0, 0.5, 1.0]


def test_normalize_scores_handles_identical_values():
    assert normalize_scores([5.0, 5.0]) == [1.0, 1.0]
    assert normalize_scores([0.0, 0.0]) == [0.0, 0.0]


def test_normalize_scores_handles_empty():
    assert normalize_scores([]) == []


# ------------------------------------------------------------------ ndcg bound


def test_ndcg_never_exceeds_one_with_repeated_sources():
    """Regression: several chunks from one expected source used to score >1.0.

    Labels are per document but retrieval returns chunks, so a document that
    contributed four chunks was credited four times.
    """
    from advanced_rag.evaluation.retrieval_metrics import ndcg_at_k

    retrieved = [hit("a.md", 0.9), hit("a.md", 0.8), hit("a.md", 0.7), hit("b.md", 0.6)]
    assert ndcg_at_k(retrieved, ["a.md"], 5) == pytest.approx(1.0)
    assert ndcg_at_k(retrieved, ["a.md", "b.md"], 5) <= 1.0


def test_sigmoid_is_monotonic_and_bounded():
    values = [sigmoid(x) for x in (-10, -1, 0, 1, 10)]
    assert values == sorted(values)
    assert all(0.0 < v < 1.0 for v in values)
    assert sigmoid(0) == pytest.approx(0.5)


# ------------------------------------------------------------ fusion logic


class StubStore(HybridStore):
    """HybridStore with the two search arms stubbed out."""

    def __init__(self, settings: Settings, dense, sparse):
        # Deliberately skip HybridStore.__init__ - no embedder, no Qdrant client.
        self.settings = settings
        self.collection = "test"
        self._dense_hits = dense
        self._sparse_hits = sparse

    def _single(self, query, top_k, using, query_filter):
        hits = self._dense_hits if using == "dense" else self._sparse_hits
        results = [h.model_copy(deep=True) for h in hits[:top_k]]
        for rank, result in enumerate(results):
            if using == "dense":
                result.dense_rank = rank
            else:
                result.sparse_rank = rank
        return results


def test_weighted_fusion_favours_the_weighted_arm():
    dense = [hit("a.md", 0.9), hit("b.md", 0.1)]
    sparse = [hit("b.md", 0.9), hit("a.md", 0.1)]

    dense_heavy = StubStore(Settings(hybrid_dense_weight=1.0), dense, sparse)
    ranked = dense_heavy._hybrid_weighted("q", 2, None)
    assert [r.chunk.source for r in ranked] == ["a.md", "b.md"]

    sparse_heavy = StubStore(Settings(hybrid_dense_weight=0.0), dense, sparse)
    ranked = sparse_heavy._hybrid_weighted("q", 2, None)
    assert [r.chunk.source for r in ranked] == ["b.md", "a.md"]


def test_weighted_fusion_rewards_documents_found_by_both_arms():
    # 'both.md' is mid-ranked in each arm; 'dense_only.md' tops just one.
    dense = [hit("dense_only.md", 1.0), hit("both.md", 0.6), hit("filler.md", 0.0)]
    sparse = [hit("sparse_only.md", 1.0), hit("both.md", 0.6), hit("filler2.md", 0.0)]

    store = StubStore(Settings(hybrid_dense_weight=0.5), dense, sparse)
    ranked = store._hybrid_weighted("q", 3, None)
    assert ranked[0].chunk.source == "both.md", "agreement across arms should win"


def test_weighted_fusion_records_both_ranks():
    dense = [hit("a.md", 0.9)]
    sparse = [hit("a.md", 0.5)]
    store = StubStore(Settings(hybrid_dense_weight=0.5), dense, sparse)
    ranked = store._hybrid_weighted("q", 1, None)
    assert ranked[0].dense_rank == 0
    assert ranked[0].sparse_rank == 0


def test_weighted_fusion_deduplicates():
    dense = [hit("a.md", 0.9), hit("b.md", 0.5)]
    sparse = [hit("a.md", 0.9), hit("b.md", 0.5)]
    store = StubStore(Settings(hybrid_dense_weight=0.5), dense, sparse)
    ranked = store._hybrid_weighted("q", 10, None)
    assert len(ranked) == 2


# ---------------------------------------------------------- score selection


def test_rerank_score_takes_precedence_only_when_authoritative():
    """`score` must not trust a rerank score from the lexical fallback.

    This test previously asserted that any rerank score wins, which is what let a
    0.008 lexical score veto correctly-retrieved context at the CRAG floor.
    """
    chunk = hit("a.md", 0.2)
    assert chunk.score == 0.2

    # Non-authoritative: recorded for display, ignored for judgement.
    chunk.rerank_score = 0.95
    assert chunk.rerank_is_authoritative is False
    assert chunk.score == 0.2

    # A cross-encoder score is authoritative and does take precedence.
    chunk.rerank_is_authoritative = True
    assert chunk.score == 0.95


# ------------------------------------------------------- context rendering


def test_format_context_numbers_blocks_for_citation():
    context = format_context([hit("a.md", 0.9), hit("b.md", 0.8)])
    assert context.startswith("[1] ")
    assert "[2] " in context
    assert "text of a.md" in context


def test_format_context_uses_title_and_section():
    chunk = RetrievedChunk(
        chunk=Chunk(
            id="1", text="body", source="runbooks/x.md", title="Runbook X", section="Triage"
        )
    )
    assert "[1] Runbook X - Triage" in format_context([chunk])


def test_format_context_empty():
    assert format_context([]) == ""


# ------------------------------------------------- rerank ordering policy


class _Reranker:
    def __init__(self, is_cross_encoder: bool, scores: list[float]) -> None:
        self.is_cross_encoder = is_cross_encoder
        self.model_name = "stub"
        self._scores = scores

    def score(self, query, documents):
        return self._scores[: len(documents)]


def _retriever(reranker):
    from advanced_rag.retrieval.retriever import Retriever

    return Retriever(settings=Settings(rerank_top_n=3), store=object(), reranker=reranker)


def test_lexical_reranker_scores_but_does_not_reorder():
    """Measured: reordering by the lexical stand-in dropped p@5 from 0.76 to 0.64.

    It must still populate rerank_score, because the CRAG relevance floor reads it.
    """
    chunks = [hit("a.md", 0.9), hit("b.md", 0.5), hit("c.md", 0.1)]
    # Scores that would invert the order if they were allowed to.
    result = _retriever(_Reranker(False, [-6.0, 0.0, 6.0])).rerank("q", chunks)

    assert [c.chunk.source for c in result] == ["a.md", "b.md", "c.md"]
    assert all(c.rerank_score is not None for c in result)
    assert result[2].rerank_score > result[0].rerank_score  # scored, just not obeyed


def test_cross_encoder_reranker_does_reorder():
    chunks = [hit("a.md", 0.9), hit("b.md", 0.5), hit("c.md", 0.1)]
    result = _retriever(_Reranker(True, [-6.0, 0.0, 6.0])).rerank("q", chunks)
    assert [c.chunk.source for c in result] == ["c.md", "b.md", "a.md"]


def test_rerank_respects_top_n_in_both_modes():
    chunks = [hit(f"{i}.md", 1.0 - i / 10) for i in range(6)]
    for is_cross_encoder in (True, False):
        reranker = _Reranker(is_cross_encoder, [1.0] * 6)
        result = _retriever(reranker).rerank("q", chunks, top_n=2)
        assert len(result) == 2
