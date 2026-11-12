"""User Question
      ↓
HyDE (optional)
      ↓
Hybrid Search
(Dense + Sparse)
      ↓
Top K Documents
      ↓
Cross Encoder Rerank
      ↓
Best Context
      ↓
LLM Answer

## The retrieval pipeline: HyDE -> hybrid search -> cross-encoder rerank.

Kept independent of LangGraph so it can be exercised and evaluated on its own -
`python -m advanced_rag.evaluation.runner --retrieval` drives exactly these entry
points to compare strategies without generating anything."""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field

from advanced_rag.config import Settings, get_settings
from advanced_rag.llm import prompts
from advanced_rag.llm.client import LLMClient, get_llm, llm_available
from advanced_rag.models import Chunk, RetrievedChunk
from advanced_rag.observability import log_degraded
from advanced_rag.retrieval.embeddings import get_reranker, sigmoid
from advanced_rag.retrieval.vectorstore import HybridStore, get_store

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:              
    """
    Stores retrieval output: chunks, query_used, hyde_document, mode, reranked, cross_encoder
    # Example:
        Question: Pod crash issue
        ↓
        Retrieved 10 chunks
        ↓
        Saved in RetrievalResult
    
    Useful properties: top_score, highest_chunk_score (mean_score/Average score). 
    """
    
    """Everything the graph needs to know about one retrieval pass."""

    chunks: list[RetrievedChunk] = field(default_factory=list)
    query_used: str = ""
    hyde_document: str | None = None
    mode: str = "hybrid"
    reranked: bool = False
    #: True only when a real cross-encoder scored the results, so callers can
    #: tell a strong rerank score from a lexical-overlap stand-in.
    cross_encoder: bool = False
    elapsed_ms: float = 0.0
    hyde_ms: float | None = None
    rerank_ms: float | None = None

    @property
    def top_score(self) -> float:
        return max((c.score for c in self.chunks), default=0.0)

    @property
    def mean_score(self) -> float:
        if not self.chunks:
            return 0.0
        return sum(c.score for c in self.chunks) / len(self.chunks)


class Retriever:
    """
    Main retrieval orchestrator.
    
    Loads: store, re-ranker, LLM, settings
    """

    """Composes the retrieval stages behind one call."""

    def __init__(
        self,
        settings: Settings | None = None,
        store: HybridStore | None = None,
        reranker=None,
        llm: LLMClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.store = store or get_store()
        self._reranker = reranker
        self._llm = llm

    @property
    def reranker(self):
        if self._reranker is None:
            self._reranker = get_reranker()
        return self._reranker

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = get_llm()
        return self._llm

    # -------------------------------------------------------------- retrieval
    """
    # Most important function.
    
    Question
        ↓
    Generate HyDE
        ↓
    Hybrid Search
        ↓
    Top K Results
        ↓
    Rerank
        ↓
    Return
    """
    def retrieve(                           
        self,
        question: str,
        *,
        use_hyde: bool | None = None,
        rerank: bool | None = None,
        mode: str = "hybrid",
        fusion: str | None = None,
        top_k: int | None = None,
        top_n: int | None = None,
        doc_type: str | None = None,
        category: str | None = None,
        source_prefix: str | None = None,
        document_id: str | None = None,
        source_version: str | None = None,
        namespace: str | None = None,
        collection: str | None = None,
        filter_=None,
    ) -> RetrievalResult:
        t0 = time.perf_counter()
        use_hyde = self.settings.enable_hyde if use_hyde is None else use_hyde
        should_rerank = self.settings.enable_rerank if rerank is None else rerank
        effective_fusion = fusion if fusion is not None else getattr(self.settings, "hybrid_fusion", "rrf")
    
        hyde_doc: str | None = None
        hyde_ms: float | None = None
        query = question
        if use_hyde:
            t_hyde = time.perf_counter()
            hyde_doc = self.generate_hyde(question)
            hyde_ms = round((time.perf_counter() - t_hyde) * 1000.0, 2)
            if hyde_doc and hyde_doc.strip():
                # Keep the question in the query so lexical matching still has the
                # user's exact error strings and resource names to work with.
                query = question + "\n\n" + hyde_doc.strip()

        chunks = self.store.search(
            query,
            top_k=top_k or self.settings.retrieve_top_k,
            mode=mode,
            fusion=effective_fusion,
            doc_type=doc_type,
            category=category,
            source_prefix=source_prefix,
            document_id=document_id,
            source_version=source_version,
            namespace=namespace,
            collection=collection,
            filter_=filter_,
        )
        for rank, chunk in enumerate(chunks, start=1):
            chunk.retrieval_rank = rank

        rerank_ms: float | None = None
        reranked = False
        cross_encoder = False
        if should_rerank and chunks:
            t_rerank = time.perf_counter()
            # Rerank against the real question - the HyDE passage is a retrieval
            # aid, and scoring against it rewards documents similar to a guess.
            chunks = self.rerank(question, chunks, top_n=top_n)
            rerank_ms = round((time.perf_counter() - t_rerank) * 1000.0, 2)
            reranked = True
            try:
                cross_encoder = bool(self.reranker.is_cross_encoder)
            except Exception:
                cross_encoder = False

        total_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return RetrievalResult(
            chunks=chunks,
            query_used=query,
            hyde_document=hyde_doc,
            mode=mode,
            reranked=reranked,
            cross_encoder=cross_encoder,
            elapsed_ms=total_ms,
            hyde_ms=hyde_ms,
            rerank_ms=rerank_ms,
        )
    
    """
    Question:
        Why is pod in CrashLoopBackOff?
    HyDE generates:
        A pod enters CrashLoopBackOff when...
        
    Now query becomes : riginal Question + Generated Answer
    
    Benifits : Question language + Document language -> better retrieval s
    """

    """
    Hybrid search : 
    
    Query
        ↓
    Dense Embedding
        ↓
    Vector Search + BM25 Search
        ↓
    Fusion
        ↓
    Top K Documents
    """

    def rerank(
        self, question: str, chunks: list[RetrievedChunk], *, top_n: int | None = None
    ) -> list[RetrievedChunk]:
        """
        Cross Encoder or Gemini Reranker or Lexical Reranker
        
        Hybrid Search = Candidate Generation
        Cross Encoder = Precision Improvement
        """
        
        """Re-score candidates with a cross-encoder and keep the best `top_n`.

        The bi-encoder that produced the candidates scored query and document
        separately; the cross-encoder reads them together, which is what makes it
        worth the extra pass over a small candidate set.
        """
        if not chunks:
            return []
        top_n = top_n or self.settings.rerank_top_n
        try:
            reranker = self.reranker
            raw = reranker.score(question, [c.chunk.text for c in chunks])              # Returns: Logits [4.2, 1.5, 5.8]
            for chunk, logit in zip(chunks, raw, strict=True):
                # Squash to 0..1 so CRAG_RELEVANCE_FLOOR means something stable.
                chunk.rerank_score = sigmoid(logit)                                     # Convert to Probability : 5.8 -> 0.997
                chunk.rerank_is_authoritative = reranker.is_cross_encoder

            if not reranker.is_cross_encoder:                                           # Do: keep the original order 
                # Measured: reordering by the lexical stand-in *lowered* precision@5
                # from 0.76 to 0.64 on the golden set. It is too weak a signal to
                # overrule fusion, so it only supplies a score for the CRAG floor and
                # the retrieval order is preserved.
                return chunks[:top_n]

            # Retrieval score is the tiebreaker: where the reranker cannot separate two
            # passages, fall back on the order the retriever already earned instead of
            # letting an arbitrary tie decide.
            return sorted(
                chunks,
                key=lambda c: (c.rerank_score or 0.0, c.retrieval_score),
                reverse=True,
            )[:top_n]
        except Exception as exc:
            log_degraded(
                logger,
                "rerank",
                "CrossEncoder reranking failed; falling back to original retrieval ranking",
                exc,
            )
            return chunks[:top_n]

    def generate_hyde(self, question: str, *, model: str | None = None) -> str | None:
        """
        Question
            ↓
        Fake Answer
            ↓           
        Embed Fake Answer
        """
        
        """Draft a hypothetical answer passage to embed instead of the question.

        Questions and documents are written differently; a question rarely shares
        much surface form with the runbook that answers it. Embedding a fake
        answer closes that gap.
        """
        if not question or not isinstance(question, str) or len(question.strip()) < 3:
            return None
        if not llm_available():
            return None
        target_model = model or self.settings.llm_fast_model
        try:
            result = self.llm.complete(
                "Kubernetes question: " + question.strip(),
                system=prompts.HYDE_SYSTEM,
                model=target_model,
                effort="low",
                max_tokens=250,
            )
        except Exception as exc:  # HyDE is an optimisation - never fail the request for it
            log_degraded(logger, "hyde", "HyDE unavailable, using the raw question", exc)
            return None
        if result.refused or not result.text or not result.text.strip():
            return None
        return result.text.strip()

    def rewrite_query(self, question: str, *, n: int = 3) -> list[str]:                # (Corrective RAG)
        """CRAG's corrective step: propose alternative queries after a bad pass."""
        from pydantic import BaseModel, Field

        class Rewrites(BaseModel):
            queries: list[str] = Field(description="alternative search queries")

        if not llm_available():
            return []
        try:
            rewrites = self.llm.complete_json(
                "Original question: " + question + "\n\nPropose " + str(n) + " queries.",
                Rewrites,
                system=prompts.QUERY_REWRITE_SYSTEM,
                max_tokens=800,
            )
        except Exception as exc:
            log_degraded(logger, "query_rewrite", "Query rewriting unavailable", exc)
            return []
        return [q.strip() for q in rewrites.queries[:n] if q.strip()]

    def retrieve_multi(                         
        self, queries: list[str], *, top_k: int | None = None, top_n: int | None = None,
        question: str | None = None,
    ) -> list[RetrievedChunk]:
        """
        Query 1
        ↓
        Query 2
        ↓
        Query 3
        ↓
        Combine Results
        ↓
        Remove Duplicates
        ↓
        Rerank
        ↓
        Return

        results -> multi recall
        You don't miss relevant documents 
        """
        """Run several queries and rerank the deduplicated union."""
        pooled: dict[str, RetrievedChunk] = {}
        for query in queries:
            for hit in self.store.search(query, top_k=top_k or self.settings.retrieve_top_k):
                current = pooled.get(hit.chunk.id)
                if current is None or hit.retrieval_score > current.retrieval_score:
                    pooled[hit.chunk.id] = hit
        if not pooled:
            return []
        return self.rerank(question or queries[0], list(pooled.values()), top_n=top_n)


def select_context_chunks(
    chunks: list[RetrievedChunk],
    *,
    max_chunks: int = 5,
    max_tokens: int = 2500,
    min_score_ratio: float = 0.20,
    enable_deduplication: bool = True,
) -> list[RetrievedChunk]:
    """Filter, deduplicate, and budget retrieved chunks for LLM context.

    1. Relevance thresholding: drop chunks scoring below (top_score * min_score_ratio).
       Always preserves at least 1 chunk if candidates are provided.
    2. Near-duplicate / sliding-window deduplication: drop overlapping chunks from
       the same source document whose token Jaccard similarity exceeds 0.75.
    3. Token budget enforcement: cumulative token limit (max_tokens) using len(text)//4
       heuristic, stopping when budget is exhausted (always keeping >= 1 chunk).
    4. Bounded to at most max_chunks.
    """
    if not chunks:
        return []

    # 1. Relevance thresholding
    top_score = max((c.score for c in chunks), default=0.0)
    if top_score > 0.0 and min_score_ratio > 0.0:
        threshold = top_score * min_score_ratio
        filtered = [c for c in chunks if c.score >= threshold]
        if not filtered:
            filtered = [chunks[0]]
    else:
        filtered = list(chunks)

    # 2. Near-duplicate / sliding-window deduplication
    deduped: list[RetrievedChunk] = []
    for candidate in filtered:
        if not enable_deduplication:
            deduped.append(candidate)
            continue

        cand_words = set(re.findall(r"\w+", candidate.chunk.text.lower()))
        is_duplicate = False
        for accepted in deduped:
            if candidate.chunk.source and accepted.chunk.source == candidate.chunk.source:
                acc_words = set(re.findall(r"\w+", accepted.chunk.text.lower()))
                union = len(cand_words | acc_words)
                if union > 0:
                    jaccard = len(cand_words & acc_words) / union
                    if jaccard > 0.75:
                        is_duplicate = True
                        break
        if not is_duplicate:
            deduped.append(candidate)

    if not deduped:
        deduped = [chunks[0]]

    # 3. Token budgeting and chunk count capping
    selected: list[RetrievedChunk] = []
    used_tokens = 0

    for chunk in deduped:
        if len(selected) >= max_chunks:
            break
        # Heuristic: 1 token ~= 4 chars (min 1 token)
        chunk_tokens = max(1, len(chunk.chunk.text) // 4)
        if selected and (used_tokens + chunk_tokens > max_tokens):
            break
        selected.append(chunk)
        used_tokens += chunk_tokens

    return selected or [chunks[0]]


def format_context(chunks: list[RetrievedChunk]) -> str:            # Converts retrieved chunks into prompt context.
    """Render chunks as the numbered blocks the answer prompt cites as [n]."""
    blocks = []
    for index, hit in enumerate(chunks, start=1):
        chunk = hit.chunk
        header = "[" + str(index) + "] " + chunk.citation()
        blocks.append(header + "\n" + chunk.text.strip())
    return "\n\n".join(blocks)


def as_chunks(chunks: list[RetrievedChunk]) -> list[Chunk]:         # Simple helper function.
                                                                    # Converts: RetrievedChunk -> Chunk
    return [c.chunk for c in chunks]


_retriever: Retriever | None = None
_RETRIEVER_LOCK = threading.Lock()


def get_retriever() -> Retriever:
    """
    Singleton pattern.
    
    Insted of:
    Retriever()
    Retriever()
    Retriever()

    Only one object is created: _retriever & reused 
    """
    
    global _retriever
    if _retriever is None:
        with _RETRIEVER_LOCK:
            if _retriever is None:
                _retriever = Retriever()
    return _retriever


def reset_retriever() -> None:
    """Close and clear the global retriever singleton and its underlying store."""
    global _retriever
    if _retriever is not None:
        try:
            if hasattr(_retriever, "store") and _retriever.store is not None:
                _retriever.store.close()
        except Exception:
            pass
        _retriever = None

"""
User Question
      ↓
generate_hyde()
      ↓
Question + Hypothetical Answer
      ↓
Hybrid Store Search
      ↓
Dense Search + BM25 Search
      ↓
Top K Chunks
      ↓
Cross Encoder Reranker
      ↓
Top N Chunks
      ↓
format_context()            # Converts retrieved chunks into prompt context.
      ↓
Context for LLM
      ↓
Final Answer

This file is the retrieval orchestration layer that performs HyDE query expansion → hybrid retrieval 
→ cross-encoder reranking → context preparation before sending the best chunks to the LLM.
"""