"""This file measures retrieval quality without using an LLM.

It helps evaluate whether:
    Hybrid Search improved retrieval
    HyDE improved retrieval
    Reranking improved retrieval
    CRAG rewriting improved retrieval

## Retrieval metrics that need no LLM judge.

These are the metrics to tune on first: they are deterministic, cost nothing per
run, and every advanced retrieval layer in this project (hybrid fusion, HyDE,
reranking, CRAG rewriting) is supposed to move them.
"""

from __future__ import annotations

from dataclasses import dataclass

from advanced_rag.models import RetrievedChunk


@dataclass
class RetrievalScores:                                                                        # RetrievalScores
    hit_rate: float
    recall: float
    mrr: float
    ndcg: float
    precision_at_k: float
    cases: int

    def as_row(self, label: str) -> dict[str, object]:
        return {            
            "strategy": label,
            "hit@k": round(self.hit_rate, 3),
            "recall": round(self.recall, 3),
            "mrr": round(self.mrr, 3),
            "ndcg": round(self.ndcg, 3),
            "p@k": round(self.precision_at_k, 3),
            "cases": self.cases,
        }


def _sources(chunks: list[RetrievedChunk]) -> list[str]:                                      # Extracts source document names.
    return [hit.chunk.source for hit in chunks]


def hit_at_k(retrieved: list[RetrievedChunk], expected: list[str], k: int) -> float:          # Did we retrieve at least one correct document in Top-K?
    """1.0 if any expected source appears in the top k."""
    found = set(_sources(retrieved[:k]))
    return 1.0 if found & set(expected) else 0.0


def recall_at_k(retrieved: list[RetrievedChunk], expected: list[str], k: int) -> float:       # How many expected documents were found?
    """Fraction of the expected sources present in the top k."""
    if not expected:
        return 0.0
    found = set(_sources(retrieved[:k]))
    return len(found & set(expected)) / len(set(expected))


def reciprocal_rank(retrieved: list[RetrievedChunk], expected: list[str]) -> float:            # Measures how early the first relevant document appears; Better rankings give higher scores.
    """1/rank of the first relevant result - rewards putting it first, not just present."""
    wanted = set(expected)
    for rank, source in enumerate(_sources(retrieved), start=1):
        if source in wanted:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved: list[RetrievedChunk], expected: list[str], k: int) -> float:          #  Are relevant documents near the top? 
    """Binary-gain NDCG: discounts relevant hits that sit lower in the list.                   # Higher rank gets higher credit.

    Labels are per *document*, but retrieval returns chunks and several chunks can             # Why credited only once? 
    share a source. Credit is therefore given once per source, at its best rank -              # Same document may produce multiple chunks: DocA gets counted 3 times
    otherwise a document that contributed four chunks scores 4x and NDCG exceeds               # NDCG could exceed: Which is invalid -> So each source document gets credit only once.
    1.0, which is meaningless.
    """
    import math

    wanted = set(expected)
    if not wanted:
        return 0.0

    gains: list[float] = []
    credited: set[str] = set()
    for source in _sources(retrieved[:k]):
        relevant = source in wanted and source not in credited
        if relevant:
            credited.add(source)
        gains.append(1.0 if relevant else 0.0)

    dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
    ideal_hits = min(len(wanted), k)
    idcg = sum(1.0 / math.log2(index + 2) for index in range(ideal_hits))
    return dcg / idcg if idcg else 0.0


def precision_at_k(retrieved: list[RetrievedChunk], expected: list[str], k: int) -> float:      # How many retrieved results are actually relevant?
    """Share of the top k that came from an expected source.

    Low precision with high recall is the signature of a retriever that needs
    reranking rather than better search.                                                        # Interpretation : High Recall + Low Precision
    """                                                                                         # Retriever finds answers, but returns lots of noise
    window = _sources(retrieved[:k])                                                            # Usually indicates: Need reranking
    if not window:
        return 0.0
    wanted = set(expected)
    return sum(1 for source in window if source in wanted) / len(window)


def score_all(
    results: list[tuple[list[RetrievedChunk], list[str]]], k: int = 5                           # Calculates average metrics over all test cases.
) -> RetrievalScores:
    """Average every metric over (retrieved, expected) pairs."""
    scored = [(r, e) for r, e in results if e]
    if not scored:
        return RetrievalScores(0.0, 0.0, 0.0, 0.0, 0.0, 0)
    count = len(scored)
    return RetrievalScores(
        hit_rate=sum(hit_at_k(r, e, k) for r, e in scored) / count,
        recall=sum(recall_at_k(r, e, k) for r, e in scored) / count,
        mrr=sum(reciprocal_rank(r, e) for r, e in scored) / count,
        ndcg=sum(ndcg_at_k(r, e, k) for r, e in scored) / count,
        precision_at_k=sum(precision_at_k(r, e, k) for r, e in scored) / count,
        cases=count,
    )


def fact_coverage(answer: str, expected_facts: list[str]) -> float:                           # Measures answer completeness.
    """Share of expected substrings present in the answer, case-insensitively.                # Expected Facts:["exit code 137", "CPU threshold 80%"]
                                                                                              # Generated Answer:Restart service, Issue occurs when CPU threshold exceeds 80%. 
    Crude but useful: it catches an answer that talks around the question without             # Found facts:CPU threshold 80% 
    ever naming the threshold, exit code or resource the runbook specifies.                   # Missing:exit code 137
    """                                                                                       # Coverage:1/2 = 0.50
    if not expected_facts:
        return 1.0
    lowered = answer.lower()
    return sum(1 for fact in expected_facts if fact.lower() in lowered) / len(expected_facts)


"""
Question
    │
    ▼
Retriever Returns Chunks
    │
    ▼
Compare with Ground Truth
    │
    ├── Hit@K
    ├── Recall@K
    ├── MRR
    ├── NDCG
    └── Precision@K
    │
    ▼
Average Across Cases
    │
    ▼
RetrievalScores
    │
    ▼
Evaluate Answer
    │
    ▼
Fact Coverage

"This module evaluates retrieval quality using deterministic metrics like Hit@K, Recall@K, MRR, NDCG, Precision@K, and Fact Coverage, 
helping measure how accurately and how highly relevant documents are retrieved without requiring an LLM-based evaluator."
"""