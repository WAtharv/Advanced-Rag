"""This module provides embedding (vector generation) and reranking (relevance scoring) for an Advanced RAG system.

It supports 3 retrieval modes:

    Keyword Mode → Local BM25 only
    FastEmbed Mode → Dense + Sparse embeddings using ONNX models
    Gemini Mode → Dense embeddings from Gemini API + Local BM25

# Local embedding and reranking models.

fastembed runs ONNX models on CPU, so hybrid search and cross-encoder reranking
need no second API key and no GPU. Models are loaded lazily and cached for the
process because construction costs a few seconds each.

ONNX : A stadard file format used to represent ML and DL models."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from functools import lru_cache

from advanced_rag.config import get_settings
from advanced_rag.retrieval import bm25

logger = logging.getLogger(__name__)

#: SPARSE_MODEL values that select the local BM25 implementation outright.
LOCAL_SPARSE_ALIASES = frozenset({"local-bm25", "local", "bm25-local", "none"})

#: RERANK_MODEL values that select the local lexical scorer outright.
LOCAL_RERANK_ALIASES = frozenset({"local-lexical", "local", "lexical", "none"})


class SparseVector:                                 # Stores sparse vectors in Qdrant format: index/value - Used for: BM25 retirieval, Sparse embeddings 
    """Index/value pair as Qdrant expects it for sparse vectors."""

    __slots__ = ("indices", "values")

    def __init__(self, indices: Sequence[int], values: Sequence[float]) -> None:
        self.indices = [int(i) for i in indices]
        self.values = [float(v) for v in values]


class ModelUnavailableError(RuntimeError):                  
    """fastembed could not obtain a model, with the ways out spelled out."""

    def __init__(self, model_name: str, cause: Exception) -> None:
        self.model_name = model_name
        super().__init__(
            f"Could not load the local model {model_name!r}: {cause}\n"
            "fastembed downloads these from huggingface.co. If that host is blocked\n"
            "on this network, pick one of:\n"
            "  1. have huggingface.co allowlisted for this machine;\n"
            "  2. set HF_ENDPOINT to an internal Hugging Face mirror;\n"
            "  3. copy a populated fastembed cache onto this machine and set\n"
            "     MODEL_CACHE_DIR to it in .env."
        )


class KeywordEmbedder:                              # Used when RETRIEVAL_BACKEND=keyword
                                                    # Only supports BM25, does not supports Dense embeddings                       
                                                    # Text -> BM25 -> Sparse vector -> search 
    """Sparse-only encoder using the local BM25 implementation.

    Drop-in for `Embedder` minus the dense arm, so the store, retriever and graph
    work unchanged - they branch on `supports_dense` rather than on the class.
    """

    supports_dense = False
    dense_model_name = "(none)"
    sparse_model_name = "local-bm25"

    @property
    def dim(self) -> int:
        raise DenseUnavailableError()

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise DenseUnavailableError()

    def embed_query(self, text: str) -> list[float]:
        raise DenseUnavailableError()

    def sparse_documents(self, texts: Sequence[str]) -> list[SparseVector]:
        return [SparseVector(*bm25.encode_document(text)) for text in texts]

    def sparse_query(self, text: str) -> SparseVector:
        return SparseVector(*bm25.encode_query(text))


class DenseUnavailableError(RuntimeError):            # Thrown when someone tries: embed_query(), while using BM25-only mode.
    def __init__(self) -> None:
        super().__init__(
            "The keyword retrieval backend has no dense arm. Set "
            "RETRIEVAL_BACKEND=fastembed once the ONNX models are available."
        )


class Embedder:                                        # Main embedding engine.
                                                       # which runs:ONNX models -> CPU (No GPU needed)
    """Dense + sparse text encoders backed by fastembed.

    The two arms degrade independently. That matters on a network that blocks
    huggingface.co: six of fastembed's dense models are also mirrored on Google
    Cloud Storage and still download, while *every* sparse model and reranker is
    HF-only. All-or-nothing fallback would throw away a working dense arm because
    the sparse one could not load, so the sparse arm falls back to the local BM25
    implementation on its own.
    """

    supports_dense = True

    def __init__(
        self, dense_model: str, sparse_model: str, cache_dir: str | None = None
    ) -> None:
        self.dense_model_name = dense_model
        self.configured_sparse_model = sparse_model
        self.cache_dir = cache_dir
        self._dense = None
        self._sparse = None
        #: None until resolved, then "fastembed" or "local".
        self._sparse_provider: str | None = None
        self._dim: int | None = None

    @property
    def sparse_model_name(self) -> str:
        """The sparse encoder actually in use, once resolved."""
        if self._sparse_provider == "local":
            return "local-bm25"
        return self.configured_sparse_model

    def _kwargs(self) -> dict:
        return {"cache_dir": self.cache_dir} if self.cache_dir else {}

    # ---------------------------------------------------------------- loading

    @property
    def dense(self):                            # Loads: TextEmbedding, ex: BAAI/bge-small-en
                                                # Flow : Question -> Dense Model -> 768-dim vector 
                                                # Methods: embed_query(), embed_documents()
        if self._dense is None:
            from fastembed import TextEmbedding

            logger.info("Loading dense embedding model %s", self.dense_model_name)
            try:
                self._dense = TextEmbedding(model_name=self.dense_model_name, **self._kwargs())
            except Exception as exc:
                raise ModelUnavailableError(self.dense_model_name, exc) from exc
        return self._dense

    def _sparse_impl(self):                      # Loads: SparseTextEmbedding, ex: Qdrant/bm42
                                                 # if loading fails:
                                                 # HF blocked -> Facllback -> Local BM25 
                                                 # This is why _sparse_impl() exists.
        """The fastembed sparse encoder, or None if the local BM25 is standing in."""
        if self._sparse_provider is None:
            if self.configured_sparse_model in LOCAL_SPARSE_ALIASES:
                # Explicit opt-out. Worth having as a setting rather than relying on
                # the fallback: fastembed retries a blocked download three times
                # with backoff, so probing a model you know is unreachable costs
                # ~40 s on every process start.
                logger.info("Sparse arm: local BM25 (configured explicitly)")
                self._sparse_provider = "local"
                return None

            from fastembed import SparseTextEmbedding

            logger.info("Loading sparse embedding model %s", self.configured_sparse_model)
            try:
                self._sparse = SparseTextEmbedding(
                    model_name=self.configured_sparse_model, **self._kwargs()
                )
                self._sparse_provider = "fastembed"
            except Exception as exc:
                logger.warning(
                    "Sparse model %s unavailable (%s) - using the local BM25 "
                    "implementation for the sparse arm",
                    self.configured_sparse_model,
                    type(exc).__name__,
                )
                self._sparse_provider = "local"
        return self._sparse if self._sparse_provider == "fastembed" else None

    @property
    def dim(self) -> int:
        """Dense vector width, discovered from the model description."""
        if self._dim is None:
            from fastembed import TextEmbedding

            for desc in TextEmbedding.list_supported_models():
                if desc["model"] == self.dense_model_name:
                    self._dim = int(desc["dim"])
                    break
            else:  # unknown model - pay one embedding to find out
                self._dim = len(next(iter(self.dense.embed(["dimension probe"]))))
        return self._dim

    # --------------------------------------------------------------- encoding

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self.dense.embed(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        # query_embed applies the model's query-side prefix where one is needed.
        settings = get_settings()
        if getattr(settings, "embedding_cache_enabled", True) and getattr(
            settings, "enable_cache", True
        ):
            from advanced_rag.cache import get_embedding_cache

            return get_embedding_cache().get_or_compute_dense(
                text=text,
                model_name=self.dense_model_name,
                dim=self.dim,
                compute_fn=lambda: next(iter(self.dense.query_embed([text]))).tolist(),
            )
        return next(iter(self.dense.query_embed([text]))).tolist()

    def sparse_documents(self, texts: Sequence[str]) -> list[SparseVector]:
        impl = self._sparse_impl()
        if impl is None:
            return [SparseVector(*bm25.encode_document(text)) for text in texts]
        return [SparseVector(e.indices, e.values) for e in impl.embed(list(texts))]

    def sparse_query(self, text: str) -> SparseVector:
        impl = self._sparse_impl()
        if impl is None:
            return SparseVector(*bm25.encode_query(text))
        embedding = next(iter(impl.query_embed([text])))
        return SparseVector(embedding.indices, embedding.values)


class GeminiEmbedder:                            # Used when, RETRIEVAL_BACKEND=gemini
                                                 # Document -> Gemini Embedding API -> Dense Vector + Local BM25 -> Sparse Vector
                                                 # So you get : Hybrid retrieval(Dense + Sparse) without hugging face 
    """Dense embeddings via the Gemini API, sparse via the local BM25 impl.

    Same surface as `Embedder`, so the store, retriever and graph branch on
    `supports_dense`/`dim` and never learn which backend they got. This is the
    on-network path to a real dense arm where huggingface.co is blocked: the
    dense vectors come from the Gemini embeddings API (which is reachable), and
    the sparse arm reuses the pure-Python BM25 implementation, since every
    fastembed sparse model is HF-only just like the dense ones.
    """

    supports_dense = True
    sparse_model_name = "local-bm25"

    #: Documents and queries embed into the same space but with side-specific
    #: optimisation, which measurably helps asymmetric (question -> passage)
    #: retrieval - exactly the HyDE gap this project already works around.
    _DOC_TASK = "RETRIEVAL_DOCUMENT"             # Used for indexing documents.
    _QUERY_TASK = "RETRIEVAL_QUERY"              # Used for user questions.
                                                 # This improves the Questions <-> document maching 
    def __init__(self, model: str, dim: int, api_key: str | None = None) -> None:
        self.dense_model_name = model
        self._dim = dim
        self._api_key = api_key
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google import genai

            from advanced_rag.certs import enable_system_trust_store

            # The embedding call is HTTPS to Google; on a TLS-inspecting proxy it
            # needs the OS trust store like every other outbound request. Both
            # calls are idempotent, so doing it here covers every entry point.
            enable_system_trust_store()
            self._client = genai.Client(api_key=self._api_key or None)
        return self._client

    @property
    def dim(self) -> int:
        return self._dim

    # --------------------------------------------------------------- encoding

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(list(texts), self._DOC_TASK)

    def embed_query(self, text: str) -> list[float]:
        settings = get_settings()
        if getattr(settings, "embedding_cache_enabled", True) and getattr(
            settings, "enable_cache", True
        ):
            from advanced_rag.cache import get_embedding_cache

            return get_embedding_cache().get_or_compute_dense(
                text=text,
                model_name=self.dense_model_name,
                dim=self.dim,
                compute_fn=lambda: self._embed([text], self._QUERY_TASK)[0],
            )
        return self._embed([text], self._QUERY_TASK)[0]

    def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        from google.genai import types

        config = types.EmbedContentConfig(
            task_type=task_type, output_dimensionality=self._dim
        )
        response = _with_retry(
            lambda: self.client.models.embed_content(
                model=self.dense_model_name, contents=texts, config=config
            )
        )
        # Truncating below the model's native 3072 dims leaves the vectors
        # un-normalised, so cosine distance needs an explicit L2 pass. Harmless
        # at full width, where they already arrive normalised.
        return [_l2_normalize(list(e.values)) for e in response.embeddings]

    # The sparse arm is the local BM25 implementation, exactly as KeywordEmbedder
    # uses it - fastembed's sparse models are HF-only and unreachable here.
    def sparse_documents(self, texts: Sequence[str]) -> list[SparseVector]:
        return [SparseVector(*bm25.encode_document(text)) for text in texts]

    def sparse_query(self, text: str) -> SparseVector:
        return SparseVector(*bm25.encode_query(text))


def _l2_normalize(vector: list[float]) -> list[float]:
    import math

    norm = math.sqrt(sum(v * v for v in vector))
    if norm < 1e-12:
        return vector
    return [v / norm for v in vector]


def _with_retry(call, *, attempts: int = 3, base_delay: float = 2.0):             # Retries Gemini API if:
                                                                                  # 403 -> RESOURCE_EXHAUSTED
                                                                                  # 503 -> UNAVAILABLE 
    """Retry a Gemini call through transient rate-limit / unavailability errors.

    Scoped to the embeddings path for now; the broader client retry is P1. Only
    retries errors that look transient (429/503) so a bad request fails fast.
    """
    import time

    for attempt in range(1, attempts + 1):
        try:
            return call()
        except Exception as exc:
            message = str(exc)
            transient = any(
                token in message
                for token in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE")
            )
            if not transient or attempt == attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "Gemini embeddings transient error (attempt %d/%d), retrying in %.0fs: %s",
                attempt, attempts, delay, message[:120],
            )
            time.sleep(delay)


class LexicalReranker:                              # Used when: RERANK_MODEL=local
                                                    # Query -> BM25 coverage check -> Score 
    """Fallback "reranker" for the keyword backend.

    Not a cross-encoder and no substitute for one - it scores query-term coverage,
    which cannot see word order or meaning. It exists so downstream code that
    depends on a 0..1 relevance score (the CRAG floor, the UI) still has one.
    """

    is_cross_encoder = False
    model_name = "local-idf-coverage"

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        # Returned as logits, because the caller pushes these through a sigmoid.
        # Mapping coverage 0..1 onto roughly -6..6 keeps that output well spread.
        coverage = bm25.weighted_coverage(query, list(documents))
        return [(value - 0.5) * 12.0 for value in coverage]


class Reranker:                                     # After retrieval: Needs better re-ranking -> This is reranking
    """Cross-encoder reranker: scores (query, passage) pairs jointly."""

    is_cross_encoder = True                         # cross endcoder reads : (Query + Document)
                                                    # Question + Passage -> Cross encoder -> Relevance score 
                                                    # More accurate than BM25.

    def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model = None

    @property
    def model(self):
        if self._model is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder     

            logger.info("Loading reranker %s", self.model_name)
            kwargs = {"cache_dir": self.cache_dir} if self.cache_dir else {}
            try:
                self._model = TextCrossEncoder(model_name=self.model_name, **kwargs)
            except Exception as exc:
                raise ModelUnavailableError(self.model_name, exc) from exc
        return self._model

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        if not documents:
            return []
        return [float(s) for s in self.model.rerank(query, list(documents))]


_RERANK_SYSTEM = """You are a retrieval reranker for a Kubernetes operations
assistant. You are given a question and a numbered list of passages. Rate how
well each passage helps answer the question, from 0.0 (irrelevant) to 1.0
(directly answers it). Judge each passage independently on its own merits, not
relative to the others. Return a relevance score for every passage index."""


class GeminiReranker:
    """#
    Question
    ↓

    Passage 1
    Passage 2
    Passage 3

    ↓

    Gemini LLM
    ↓
    Score each passage
    ↓
    Reorder
    
    Then converted to logits.
    """
    
    
    """LLM-scored reranker: one structured call rates every candidate 0..1.

    Not a bi-directional cross-encoder, but authoritative in the sense the rest
    of the pipeline needs - a model reads the question and each passage together
    and rates relevance, which is what drives reordering and the CRAG relevance
    floor. This is the reranker for a network that blocks huggingface.co (so the
    fastembed cross-encoder is unreachable) but can reach the Gemini API.
    """

    #: True so the retriever treats the scores as authoritative: it reorders by
    #: them and the CRAG floor trusts them, exactly as it does for a cross-encoder.
    is_cross_encoder = True
    model_name = "gemini-llm-reranker"

    def __init__(self, settings=None, llm=None) -> None:
        self.settings = settings or get_settings()
        self._llm = llm

    @property
    def llm(self):
        if self._llm is None:
            from advanced_rag.llm.client import get_llm

            self._llm = get_llm()
        return self._llm

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        from advanced_rag.llm.client import llm_available

        docs = list(documents)
        if not docs:
            return []
        # Neutral logits (-> sigmoid 0.5) preserve the incoming order when no model
        # is available; the retriever tiebreaks on the retrieval score.
        if not llm_available():
            return [0.0 for _ in docs]

        from pydantic import BaseModel, Field

        class _PassageScore(BaseModel):
            index: int = Field(description="0-based index of the passage")
            relevance: float = Field(description="0.0 (irrelevant) to 1.0 (directly answers)")

        class _RerankScores(BaseModel):
            scores: list[_PassageScore]

        numbered = "\n\n".join(f"[{i}] {text}" for i, text in enumerate(docs))
        prompt = f"Question:\n{query}\n\nPassages:\n{numbered}\n\nScore every passage."
        try:
            result = self.llm.complete_json(
                prompt,
                _RerankScores,
                system=_RERANK_SYSTEM,
                model=self.settings.llm_fast_model,
                effort="low",
                max_tokens=4_000,
            )
        except Exception as exc:  # a rerank failure must not fail the request
            logger.warning(
                "Gemini reranker unavailable (%s) - preserving retrieval order",
                type(exc).__name__,
            )
            return [0.0 for _ in docs]

        by_index = {s.index: max(0.0, min(1.0, s.relevance)) for s in result.scores}
        # Map 0..1 relevance onto roughly -6..6 so the caller's sigmoid recovers a
        # well-spread score - the same convention LexicalReranker uses.
        return [(by_index.get(i, 0.0) - 0.5) * 12.0 for i in range(len(docs))]


def normalize_scores(scores: Iterable[float]) -> list[float]:       # Converts scores into Range 
                                                                    # Useful because:
                                                                    # BM25, Cosine, CrossEncoder -> all produce different scales.
    """Min-max normalise to [0, 1] so heterogeneous scores can be compared.

    Cross-encoder logits, cosine similarity and BM25 live on different scales;
    the CRAG relevance floor and weighted fusion both need a common one.
    """
    values = list(scores)
    if not values:
        return []
    low, high = min(values), max(values)
    if high - low < 1e-9:
        return [1.0 if high > 0 else 0.0 for _ in values]
    return [(v - low) / (high - low) for v in values]


def sigmoid(x: float) -> float:                                     # Converts logits into plain text 0.997
    """Map a cross-encoder logit to a calibrated-ish 0..1 relevance."""
    import math

    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    exp_x = math.exp(x)
    return exp_x / (1.0 + exp_x)


def _cache_dir(settings: Settings | None = None) -> str | None:
    settings = settings or get_settings()
    val = settings.model_cache_dir
    if val is None or str(val).strip() in ("", "."):
        return None
    return str(settings.absolute(val))


def _prepare_downloads() -> None:                               # Before downloading models:
    """Everything fastembed needs before it touches the network.

    Deliberately here rather than in each entry point. Requiring every CLI, the
    API and the eval runner to remember a setup call is a bug waiting to happen -
    and it happened: the eval runner omitted the trust-store call and died with
    CERTIFICATE_VERIFY_FAILED even though the model was already cached. This is
    the boundary where the network is actually used, so it is the right place.
    Both calls are idempotent.
    """
    import os

    from advanced_rag.certs import enable_system_trust_store

    enable_system_trust_store()                                 # Fixes: CERTIFICATE_VERIFY_FAILED

    # pydantic-settings loads .env into a Settings object, not into os.environ, so
    # a token configured there is invisible to huggingface_hub without this. An
    # already-set HF_TOKEN wins, so the shell can override .env.
    token = get_settings().hf_token                         
    if token and not os.environ.get("HF_TOKEN"):
        os.environ["HF_TOKEN"] = token                         # Set HuggingFace Token from .env
        logger.info("Using the configured Hugging Face token for model downloads")


@lru_cache(maxsize=16)
def _get_embedder_cached(
    backend: str,
    dense_model: str,
    sparse_model: str,
    cache_dir: str | None,
    embed_model: str,
    embed_dim: int,
    google_api_key: str | None,
) -> Embedder | KeywordEmbedder | GeminiEmbedder:
    """Resolve the retrieval backend, probing fastembed when set to "auto"."""
    if backend == "keyword":
        logger.info("Retrieval backend: local BM25 (keyword)")
        return KeywordEmbedder()

    if backend == "gemini":
        logger.info(
            "Retrieval backend: Gemini dense %s (dim=%d) + sparse local BM25",
            embed_model, embed_dim,
        )
        return GeminiEmbedder(
            embed_model, embed_dim, api_key=google_api_key
        )

    _prepare_downloads()
    embedder = Embedder(dense_model, sparse_model, cache_dir=cache_dir)
    if backend == "fastembed":
        return embedder

    # "auto": force the dense model to load now, so the fallback happens once at
    # startup rather than mid-request on the first query. Only the dense arm is
    # decisive - the sparse arm falls back to local BM25 by itself.
    try:
        _ = embedder.dim  # property access is what triggers the download/load
    except ModelUnavailableError as exc:
        logger.warning(
            "Dense model unavailable, falling back to keyword-only retrieval "
            "(no dense arm, no cross-encoder): %s",
            exc.__cause__ or exc,
        )
        return KeywordEmbedder()
    # Resolve the sparse arm now as well, so the line below reports the provider
    # actually in use rather than the one that was merely configured.
    embedder._sparse_impl()
    logger.info(
        "Retrieval backend: fastembed dense %s (dim=%d) + sparse %s",
        embedder.dense_model_name,
        embedder.dim,
        embedder.sparse_model_name,
    )
    return embedder


def get_embedder(settings: Settings | None = None) -> Embedder | KeywordEmbedder | GeminiEmbedder:
    """Resolve the embedder from configuration with caching per config signature."""
    s = settings or get_settings()
    return _get_embedder_cached(
        backend=s.retrieval_backend,
        dense_model=s.dense_model,
        sparse_model=s.sparse_model,
        cache_dir=_cache_dir(s),
        embed_model=s.embed_model,
        embed_dim=s.embed_dim,
        google_api_key=s.google_api_key,
    )


get_embedder.cache_clear = _get_embedder_cached.cache_clear


@lru_cache(maxsize=16)
def _get_reranker_cached(
    rerank_model: str,
    backend: str,
    supports_dense: bool,
    cache_dir: str | None,
) -> Reranker | LexicalReranker:
    """Cross-encoder when the models are available, lexical overlap otherwise."""
    if (
        rerank_model in LOCAL_RERANK_ALIASES
        or backend == "keyword"
        or not supports_dense
    ):
        logger.info("Reranker: local lexical scorer (no cross-encoder)")
        return LexicalReranker()

    if backend == "gemini":
        logger.info("Reranker: Gemini LLM-scored (authoritative)")
        return GeminiReranker(get_settings())

    _prepare_downloads()
    reranker = Reranker(rerank_model, cache_dir=cache_dir)
    try:
        _ = reranker.model
    except Exception as exc:
        logger.warning(
            "Cross-encoder %s unavailable (%s), falling back to lexical overlap: %s",
            rerank_model,
            type(exc).__name__,
            getattr(exc, "__cause__", None) or exc,
        )
        return LexicalReranker()
    return reranker


def get_reranker(settings: Settings | None = None) -> Reranker | LexicalReranker:
    """Resolve the reranker from configuration with caching per config signature."""
    s = settings or get_settings()
    embedder = get_embedder(s)
    return _get_reranker_cached(
        rerank_model=s.rerank_model,
        backend=s.retrieval_backend,
        supports_dense=embedder.supports_dense,
        cache_dir=_cache_dir(s),
    )


get_reranker.cache_clear = _get_reranker_cached.cache_clear


"""
User Query
     |
     v
get_embedder()                          keyword/gemini(BAAI/bge-small-en-v1.5)+bm25/fastembed(ONNX)
     |
     +------------------+
     |                  |
 Dense Vector      Sparse Vector
     |                  |
     +--------Hybrid Search--------+
                    |
             Top K Documents
                    |
             get_reranker()             keyword(lexical)/gemini CE/fastembed Cross encoder
                    |
          CrossEncoder / Gemini
                    |
           Relevance Scores
                    |
             Reordered Docs
                    |
                   LLM
                    |
                  Answer
                  
This file acts as the Embedding + Hybrid Search + Reranking layer of the RAG pipeline, 
automatically switching between BM25, FastEmbed (ONNX), and Gemini backends while providing 
fallback mechanisms when models are unavailable.
"""