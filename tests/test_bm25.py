"""Tests for the pure-Python BM25 encoder and the keyword backend.

This is the retrieval path that runs when the ONNX models cannot be downloaded,
so its arithmetic and its Qdrant-compatibility both need pinning down.
"""

from __future__ import annotations

import pytest

from advanced_rag.config import Settings
from advanced_rag.retrieval import bm25
from advanced_rag.retrieval.embeddings import (
    DenseUnavailableError,
    KeywordEmbedder,
    LexicalReranker,
)

# ------------------------------------------------------------------ tokeniser


def test_tokenize_lowercases_and_drops_stopwords():
    assert bm25.tokenize("The pod is in a CrashLoopBackOff") == ["pod", "crashloopbackoff"]


def test_tokenize_keeps_kubernetes_identifiers_whole():
    tokens = bm25.tokenize("crashloopbackoff imagepullbackoff")
    assert "crashloopbackoff" in tokens
    assert "imagepullbackoff" in tokens


def test_tokenize_emits_both_whole_and_split_identifiers():
    """A query for `imagefs.available` must match a doc that wrote them apart."""
    tokens = bm25.tokenize("imagefs.available")
    assert "imagefs.available" in tokens
    # Parts are stemmed like any other token; both sides apply the same rule.
    assert bm25.stem("imagefs") in tokens
    assert bm25.stem("available") in tokens


def test_tokenize_keeps_numbers():
    assert "137" in bm25.tokenize("exit code 137")


def test_negation_words_survive():
    """"node not ready" must not become "node ready"."""
    assert "not" in bm25.tokenize("the node is not ready")
    assert "no" in bm25.tokenize("no healthy upstream")


@pytest.mark.parametrize(
    "family",
    [
        ("restart", "restarts", "restarting", "restarted"),
        ("pod", "pods"),
        ("node", "nodes"),
        ("evict", "evicts", "evicting", "evicted", "eviction"),
        ("change", "changes", "changing", "changed"),
        ("deploy", "deploys", "deploying", "deployed"),
        ("policy", "policies"),
        ("threshold", "thresholds"),
    ],
)
def test_stemming_unifies_inflections(family):
    """Every surface form in a family must collapse to one stem.

    This is the property that matters - "pods" failing to reach "pod" silently
    halves recall on half the corpus.
    """
    stems = {bm25.stem(word) for word in family}
    assert len(stems) == 1, f"{family} -> {stems}"


@pytest.mark.parametrize("token", ["137", "1.29.6", "502", "v2.31.0"])
def test_numeric_and_versioned_tokens_are_untouched(token):
    assert bm25.stem(token) == token


@pytest.mark.parametrize("token", ["pod", "not", "no", "off"])
def test_short_tokens_are_untouched(token):
    assert bm25.stem(token) == token


# ------------------------------------------------------------------- hashing


def test_token_id_is_stable_and_in_range():
    first = bm25.token_id("crashloopbackoff")
    assert first == bm25.token_id("crashloopbackoff")
    assert 0 <= first <= 0x7FFFFFFF


def test_token_id_differs_between_tokens():
    assert bm25.token_id("oomkilled") != bm25.token_id("crashloop")


def test_token_id_matches_fnv1a_independently_computed():
    """Regression guard: `hash()` is salted per process and would break restarts.

    The expected value is recomputed here rather than hardcoded, so the test
    pins the *algorithm* instead of a number someone might have guessed.
    """

    def fnv1a_31(text: str) -> int:
        value = 0x811C9DC5
        for byte in text.encode("utf-8"):
            value = ((value ^ byte) * 0x01000193) & 0xFFFFFFFF
        return value & 0x7FFFFFFF

    for token in ("pod", "oomkilled", "crashloopbackoff", "137"):
        assert bm25.token_id(token) == fnv1a_31(token)


# -------------------------------------------------------------- bm25 weights


def test_encode_document_returns_sorted_unique_indices():
    indices, values = bm25.encode_document("pod pod node restart")
    assert indices == sorted(indices)
    assert len(set(indices)) == len(indices)
    assert len(indices) == len(values) == 3  # pod, node, restart


def test_repeated_terms_score_higher_but_saturate():
    _, once = bm25.encode_document("oomkilled")
    _, twice = bm25.encode_document("oomkilled oomkilled")
    _, ten = bm25.encode_document(" ".join(["oomkilled"] * 10))
    assert twice[0] > once[0]
    # Saturation: going 2 -> 10 must add less than going 1 -> 2 did.
    assert ten[0] - twice[0] < (twice[0] - once[0]) * 10
    assert ten[0] < bm25.K1 + 1


def test_length_normalisation_penalises_long_documents():
    short = bm25.encode_document("oomkilled")[1][0]
    padded = "oomkilled " + " ".join(f"filler{i}" for i in range(300))
    long_indices, long_values = bm25.encode_document(padded)
    target = bm25.token_id(bm25.stem("oomkilled"))
    long_score = long_values[long_indices.index(target)]
    assert long_score < short


def test_encode_query_is_presence_only():
    indices, values = bm25.encode_query("pod pod oomkilled")
    assert values == [1.0, 1.0]
    assert len(indices) == 2


def test_encode_empty_text():
    assert bm25.encode_document("") == ([], [])
    assert bm25.encode_document("the a of") == ([], [])


def test_query_and_document_indices_line_up():
    """The two encoders must agree on token ids or nothing ever matches."""
    doc_indices, _ = bm25.encode_document("the pod was OOMKilled")
    query_indices, _ = bm25.encode_query("oomkilled pods")
    assert set(query_indices) <= set(doc_indices)


# ---------------------------------------------------------- lexical overlap


def test_lexical_overlap_scores_relevance():
    query = "exit code 137 oomkilled"
    relevant = "Exit code 137 is SIGKILL, almost always OOMKilled."
    irrelevant = "Ingress 502 means no healthy upstream was reached."
    assert bm25.lexical_overlap(query, relevant) > bm25.lexical_overlap(query, irrelevant)
    assert bm25.lexical_overlap(query, relevant) == pytest.approx(1.0)


def test_lexical_overlap_bounds():
    assert bm25.lexical_overlap("", "anything") == 0.0
    assert bm25.lexical_overlap("pod", "") == 0.0
    assert 0.0 <= bm25.lexical_overlap("a b c pod", "pod") <= 1.0


# ------------------------------------------------------------ keyword backend


def test_keyword_embedder_produces_qdrant_shaped_sparse_vectors():
    embedder = KeywordEmbedder()
    vectors = embedder.sparse_documents(["pod OOMKilled", "ingress 502"])
    assert len(vectors) == 2
    for vector in vectors:
        assert len(vector.indices) == len(vector.values)
        assert all(isinstance(i, int) for i in vector.indices)
        assert all(isinstance(v, float) for v in vector.values)


def test_keyword_embedder_refuses_dense_clearly():
    embedder = KeywordEmbedder()
    assert embedder.supports_dense is False
    for call in (lambda: embedder.dim, lambda: embedder.embed_query("x")):
        with pytest.raises(DenseUnavailableError, match="RETRIEVAL_BACKEND=fastembed"):
            call()


def test_lexical_reranker_orders_by_relevance():
    reranker = LexicalReranker()
    assert reranker.is_cross_encoder is False
    scores = reranker.score(
        "exit code 137 oomkilled",
        ["Exit code 137 is SIGKILL and means OOMKilled", "Ingress 502 bad gateway"],
    )
    assert scores[0] > scores[1]


# ------------------------------------------------------- ranking resolution


def test_weighted_coverage_breaks_ties_that_plain_coverage_cannot():
    """Regression: five passages once all scored an identical 0.298 in the UI.

    Plain coverage quantises to k/n, so distinct passages collide and the
    reranking silently becomes a no-op. IDF weighting must separate them.
    """
    query = "pod keeps restarting exited with code 137 oomkilled memory"
    documents = [
        "Exit code 137 is SIGKILL. The container was OOMKilled for exceeding memory.",
        "A pod restarting repeatedly enters CrashLoopBackOff with a back-off timer.",
        "Pods stuck in Pending have not been bound to a node by the scheduler.",
        "Triage steps: read the previous container logs and the pod events.",
    ]

    weighted = bm25.weighted_coverage(query, documents)

    # 1. Not flat - this is the defect that shipped.
    assert len(set(weighted)) > 1, f"flat ranking: {weighted}"

    # 2. The passage naming the rare, decisive terms wins; the off-topic Pending
    #    passage does not outrank it.
    assert weighted.index(max(weighted)) == 0
    assert weighted[0] > weighted[2]

    # 3. Any remaining tie must be *justified* - documents can only score equally
    #    when they matched the same query terms. (Docs 2 and 3 both match only
    #    "pod", so no weighting scheme could separate them; that is correct
    #    behaviour, not a resolution failure.)
    query_terms = set(bm25.tokenize(query))
    matched = [query_terms & set(bm25.tokenize(doc)) for doc in documents]
    for i in range(len(documents)):
        for j in range(i + 1, len(documents)):
            if weighted[i] == weighted[j]:
                assert matched[i] == matched[j], (
                    f"docs {i} and {j} tied on {weighted[i]} with different evidence: "
                    f"{matched[i]} vs {matched[j]}"
                )

    # 4. Weighting separates strong from weak more sharply than plain coverage,
    #    which is what stops five passages landing on one score.
    plain = [bm25.lexical_overlap(query, doc) for doc in documents]
    assert (max(weighted) / max(min(weighted), 1e-9)) > (
        max(plain) / max(min(plain), 1e-9)
    ), f"weighting should widen the spread: plain={plain} weighted={weighted}"


def test_weighted_coverage_is_bounded_and_handles_degenerate_input():
    docs = ["pod oomkilled memory", "unrelated text entirely"]
    scores = bm25.weighted_coverage("pod oomkilled memory", docs)
    assert all(0.0 <= s <= 1.0 for s in scores)
    assert bm25.weighted_coverage("", docs) == [0.0, 0.0]
    assert bm25.weighted_coverage("pod", []) == []


def test_weighted_coverage_rewards_rare_terms_over_common_ones():
    """A term present in every candidate cannot be what distinguishes them."""
    documents = [
        "pod pod pod",                 # only the ubiquitous term
        "pod oomkilled",               # plus the rare, decisive one
    ]
    scores = bm25.weighted_coverage("pod oomkilled", documents)
    assert scores[1] > scores[0]


def test_lexical_reranker_no_longer_returns_all_identical_scores():
    reranker = LexicalReranker()
    scores = reranker.score(
        "pod keeps restarting exited with code 137",
        [
            "Exit code 137 is SIGKILL, meaning OOMKilled.",
            "Pods stuck in Pending were never scheduled.",
            "Triage: read the previous container logs.",
            "Ingress returns 502 when no upstream is healthy.",
        ],
    )
    assert len(set(scores)) > 1, f"reranker produced a flat ranking: {scores}"


def test_lexical_reranker_scores_survive_the_sigmoid():
    """The retriever pushes these through sigmoid(); they must spread across 0..1."""
    from advanced_rag.retrieval.embeddings import sigmoid

    reranker = LexicalReranker()
    perfect, none_ = reranker.score("pod oomkilled", ["pod oomkilled", "totally unrelated"])
    assert sigmoid(perfect) > 0.9
    assert sigmoid(none_) < 0.1


def test_backend_selection_honours_keyword_setting(monkeypatch):
    from advanced_rag.retrieval import embeddings

    monkeypatch.setattr(embeddings, "get_settings", lambda: Settings(retrieval_backend="keyword"))
    embeddings.get_embedder.cache_clear()
    embeddings.get_reranker.cache_clear()
    try:
        assert isinstance(embeddings.get_embedder(), KeywordEmbedder)
        assert isinstance(embeddings.get_reranker(), LexicalReranker)
    finally:
        embeddings.get_embedder.cache_clear()
        embeddings.get_reranker.cache_clear()


def test_auto_backend_falls_back_when_models_are_missing(monkeypatch):
    """The whole point of "auto": a blocked download must not be fatal."""
    from advanced_rag.retrieval import embeddings

    class Unavailable(embeddings.Embedder):
        @property
        def dim(self):
            raise embeddings.ModelUnavailableError("bge-small", RuntimeError("403 from proxy"))

    monkeypatch.setattr(embeddings, "get_settings", lambda: Settings(retrieval_backend="auto"))
    monkeypatch.setattr(embeddings, "Embedder", Unavailable)
    embeddings.get_embedder.cache_clear()
    try:
        assert isinstance(embeddings.get_embedder(), KeywordEmbedder)
    finally:
        embeddings.get_embedder.cache_clear()
