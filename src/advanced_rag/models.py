"""This file contains shared data models used across the entire RAG application (Ingestion → Retrieval → LangGraph → API).

##Shared data models used across ingestion, retrieval, the graph and the API."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    """A retrievable unit of text plus its provenance."""                       # Represents a single piece of document text stored in the vector database.

    id: str
    text: str
    source: str
    title: str = ""
    section: str = ""
    doc_type: str = "runbook"
    file_type: str = ""
    page_number: int | None = None
    row_number: int | None = None
    document_id: str = ""
    content_hash: str = ""
    #: Free-form extras (k8s component, severity, version...) kept in the payload.
    metadata: dict[str, Any] = Field(default_factory=dict)

    def payload(self) -> dict[str, Any]:  # converts chunk into dict for storage
        doc_id = self.document_id or hashlib.sha256(self.source.encode()).hexdigest()[:16]
        c_hash = self.content_hash or hashlib.sha256(self.text.encode()).hexdigest()
        f_type = self.file_type or Path(self.source).suffix.lstrip(".").lower() or "text"
        page = (
            self.page_number
            if self.page_number is not None
            else self.metadata.get("page_number", self.metadata.get("page"))
        )
        row = (
            self.row_number
            if self.row_number is not None
            else self.metadata.get("row_number", self.metadata.get("row"))
        )
        return {
            "chunk_id": self.id,
            "text": self.text,
            "source": self.source,                      # source file/ document
            "title": self.title,
            "section": self.section,
            "doc_type": self.doc_type,                  # runbook, manual, etc.
            "file_type": f_type,
            "document_id": doc_id,
            "content_hash": c_hash,
            "page_number": page,
            "row_number": row,
            **self.metadata,
        }

    def citation(self) -> str:                          # creates a citation string
        where = self.title or self.source
        return f"{where} - {self.section}" if self.section else where


class RetrievedChunk(BaseModel):                        # A chunk returned by retrieval with ranking scores.
    """A chunk with the scores that got it here."""

    chunk: Chunk
    #: Fused retrieval score (RRF or weighted hybrid), higher is better.
    retrieval_score: float = 0.0                        # vector/hybrid retrieval score
    #: Reranker score, normalised to 0..1.
    rerank_score: float | None = None                   # cross-encoder reranker score
    #: True only when `rerank_score` came from a real cross-encoder. The lexical
    #: fallback scorer is informative to look at but must not drive decisions:
    #: measured, it scores 0.005-0.133 on queries where dense retrieval returned
    #: exactly the right document, so treating it as authoritative makes the CRAG
    #: floor reject good context.
    rerank_is_authoritative: bool = False               # whether reranker should be trusted
    dense_rank: int | None = None                       # vector search rank 
    sparse_rank: int | None = None                      # sparse rank 

    @property
    def score(self) -> float:
        """The score this chunk should be judged on."""
        if self.rerank_is_authoritative and self.rerank_score is not None:
            return self.rerank_score
        return self.retrieval_score


class Route(StrEnum):                                   # Determines where the query goes.
    """Where the router sends a question."""

    VECTOR = "vector"
    SQL = "sql"
    BOTH = "both"
    REJECT = "reject"


class Verdict(StrEnum):                                 # CRAG evaluates retrieval quality.
                                                        # Used after retrieval to decide: procceed, retrive more documents, reject context 
    """CRAG's assessment of the retrieved context."""

    CORRECT = "correct"
    AMBIGUOUS = "ambiguous"
    INCORRECT = "incorrect"


class Citation(BaseModel):                              # Stores source references.
                                                        # <- fields 
    source: str
    title: str = ""
    section: str = ""
    score: float = 0.0


class SqlProposal(BaseModel):                           # Represents AI-generated SQL waiting for approval.
    """A generated query awaiting human approval."""

    sql: str
    rationale: str = ""                                 # why query was created
    tables: list[str] = Field(default_factory=list)
    read_only: bool = True
    approved: bool = False
    #: Populated once executed.
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    error: str | None = None

"""
User Question ↓ Generate SQL ↓ Human Approval ↓ Execute ↓ Store Results
"""

class GuardrailOutcome(BaseModel):                  # Stores moderation/safety results.
    """Result of one guardrail layer.

    `passed` means the layer *ran* and cleared the content. A layer that could not
    run - an LLM classifier during an outage - reports `action="skip"` and
    `passed=False`: it did not block, but it also did not vet anything, and
    presenting that as a pass overstates the coverage the request actually got.
    """

    layer: str
    passed: bool
    action: Literal["allow", "redact", "block", "skip"] = "allow"
    detail: str = ""

    @property
    def ran(self) -> bool:
        return self.action != "skip"


class TraceStep(BaseModel):                 # Stores LangGraph execution steps.
                                            # Router → 20ms
                                            # Retriever → 100ms
                                            # CRAG → 50ms
                                            # Generator → 300ms
                                            # Used for pipeline visualization.
    """One graph node execution, for the UI's pipeline view."""

    node: str
    detail: str = ""
    elapsed_ms: int = 0


class AnswerResponse(BaseModel):            # Final API response sent back to frontend.
                                            # Contains everything:
                                                # Question & Answer
                                                # Routing: route 
                                                # References - citations
                                                # SQL Information: SQL, awating_approval
                                                # Caching: cached, cache_kind
                                                # generation type: extractive 
                                                # safety : guardrails, blocked
                                                # Graph Trace: Trace 
                                                # Metrics : input_tokens, output_tokens, latency_ms 
    """What the API returns."""

    question: str
    answer: str
    route: Route = Route.VECTOR
    citations: list[Citation] = Field(default_factory=list)
    sql: SqlProposal | None = None
    #: True when the run stopped to ask for SQL approval.
    awaiting_approval: bool = False
    thread_id: str | None = None
    cached: bool = False
    cache_kind: Literal["none", "exact", "semantic"] = "none"
    #: True when the answer is quoted retrieved passages rather than generated
    #: prose, because no LLM is configured. Clients must surface this: an
    #: extractive answer is a different product from a synthesised one.
    extractive: bool = False
    guardrails: list[GuardrailOutcome] = Field(default_factory=list)
    blocked: bool = False
    trace: list[TraceStep] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


"""
Document
   ↓
Chunk
   ↓
Vector DB

User Question
      ↓
Route
      ↓
Retriever
      ↓
RetrievedChunk
      ↓
CRAG Verdict
      ↓
(CORRECT/AMBIGUOUS/INCORRECT)
      ↓
Generate Answer
      ↓
Guardrails
      ↓
Trace Collection
      ↓
AnswerResponse
      ↓
Frontend/API Response

This file defines the Pydantic data models used across the RAG system. Chunk stores 
document pieces, RetrievedChunk holds retrieval scores, Route decides whether to use 
vector search or SQL, Verdict is used by CRAG for retrieval evaluation, SqlProposal 
manages generated SQL queries, GuardrailOutcome tracks safety checks, TraceStep logs 
LangGraph execution, and AnswerResponse is the final API response returned to the user.
"""