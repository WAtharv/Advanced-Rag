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
        rerank: bool = True,
        mode: str = "hybrid",
        fusion: str = "weighted",
        top_k: int | None = None,
        top_n: int | None = None,
        doc_type: str | None = None,
    ) -> RetrievalResult:
        use_hyde = self.settings.enable_hyde if use_hyde is None else use_hyde
    
        hyde_doc: str | None = None
        query = question
        if use_hyde:
            hyde_doc = self.generate_hyde(question)
            if hyde_doc:
                # Keep the question in the query so lexical matching still has the
                # user's exact error strings and resource names to work with.
                query = question + "\n\n" + hyde_doc

        chunks = self.store.search(
            query, top_k=top_k or self.settings.retrieve_top_k, mode=mode,
            fusion=fusion, doc_type=doc_type,
        )
        result = RetrievalResult(
            chunks=chunks, query_used=query, hyde_document=hyde_doc, mode=mode
        )

        if rerank and chunks:
            # Rerank against the real question - the HyDE passage is a retrieval
            # aid, and scoring against it rewards documents similar to a guess.
            result.chunks = self.rerank(question, chunks, top_n=top_n)
            result.reranked = True
            result.cross_encoder = self.reranker.is_cross_encoder
        return result
    
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
        top_n = top_n or self.settings.rerank_top_n
        reranker = self.reranker
        raw = reranker.score(question, [c.chunk.text for c in chunks])              # Returns: Logits [4.2, 1.5, 5.8]
        for chunk, logit in zip(chunks, raw, strict=True):
            # Squash to 0..1 so CRAG_RELEVANCE_FLOOR means something stable.
            chunk.rerank_score = sigmoid(logit)                                     # Convert to Probability : 5.8 -> 0.997
            chunk.rerank_is_authoritative = reranker.is_cross_encoder

        if not reranker.is_cross_encoder:                                           # Do: keep the original order 
                                                                                    # Reason:
                                                                                    # Measured performance was worse when reordered.
                                                                                    # Only score is used.
                                                                                    
                                                                                    # sort : rerank_score + retrieval_score -> best documents first 
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

    def generate_hyde(self, question: str) -> str | None:
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
        if not llm_available():
            return None
        try:
            result = self.llm.complete(
                "Kubernetes question: " + question,
                system=prompts.HYDE_SYSTEM,
                model=self.settings.llm_fast_model,
                effort="low",
                max_tokens=600,
            )
        except Exception as exc:  # HyDE is an optimisation - never fail the request for it
            log_degraded(logger, "hyde", "HyDE unavailable, using the raw question", exc)
            return None
        if result.refused or not result.text:
            return None
        return result.text

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
        _retriever = Retriever()
    return _retriever

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