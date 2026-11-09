"""FastAPI service.

Endpoints mirror the pipeline's two-phase contract: POST /ask may come back
`awaiting_approval`, and POST /approve resumes that thread.

The same app also serves the web UI as static files at `/`, so one process is
the whole product: `rag-api` then open http://127.0.0.1:8000.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile, status
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from advanced_rag.certs import enable_system_trust_store
from advanced_rag.config import get_settings
from advanced_rag.graph import pipeline
from advanced_rag.ingestion.chunker import chunk_document
from advanced_rag.ingestion.extractors.factory import EXTRACTOR_REGISTRY
from advanced_rag.ingestion.loader import load_document
from advanced_rag.ingestion.registry import get_registry
from advanced_rag.models import AnswerResponse
from advanced_rag.observability import setup_logging
from advanced_rag.retrieval import get_store

logger = logging.getLogger(__name__)

MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20 MB
UPLOAD_DIR = Path("data/uploads")


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4_000)
    thread_id: str | None = None
    #: False selects the deterministic no-LLM path (quoted runbook passages)
    #: even when a model is configured - the UI's "Without LLM" toggle.
    use_llm: bool = True


class ApproveRequest(BaseModel):
    thread_id: str
    approved: bool


class RetrieveRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int | None = None
    top_n: int | None = None
    mode: str = Field("hybrid", pattern="^(dense|sparse|hybrid)$")
    fusion: str = Field("rrf", pattern="^(weighted|rrf)$")
    use_hyde: bool = False
    rerank: bool = False
    category: str | None = None
    doc_type: str | None = None
    source_prefix: str | None = None
    document_id: str | None = None
    source_version: str | None = None
    namespace: str | None = None
    collection: str | None = None

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        stripped = v.strip() if isinstance(v, str) else ""
        if not stripped:
            raise ValueError("Query cannot be empty or whitespace-only")
        return stripped


class DocumentUploadResponse(BaseModel):
    document_id: str
    filename: str
    file_type: str
    chunks_created: int
    ingestion_status: str
    message: str


class DocumentDeleteResponse(BaseModel):
    document_id: str
    deleted_chunks: int
    message: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    enable_system_trust_store()
    logger.info("Starting API (model=%s)", settings.llm_model)
    # Compile the graph up front so the first request does not pay for it.
    pipeline.get_graph()
    yield
    logger.info("Shutting down API")


app = FastAPI(
    title="Kubernetes SRE Copilot",
    version="0.1.0",
    description="Enterprise Advanced RAG: hybrid search, reranking, HyDE, CRAG, "
    "Self-RAG, Text2SQL with approval, caching and guardrails.",
    lifespan=lifespan,
)

from advanced_rag.api.knowledge import router as knowledge_router

app.include_router(knowledge_router)


@app.get("/health")
def health() -> dict[str, Any]:
    from advanced_rag.llm.client import llm_available
    from advanced_rag.retrieval import get_store

    settings = get_settings()
    try:
        indexed = get_store().count()
    except Exception as exc:
        logger.warning("Vector store unreachable: %s", exc)
        indexed = -1
    live_llm = llm_available()
    return {
        "status": "ok" if indexed > 0 else "degraded",
        "indexed_chunks": indexed,
        # Reported separately from `status`: retrieval can be perfectly healthy
        # while no model is configured, and a green badge next to quoted-passage
        # answers would overstate what the service is doing.
        "llm_mode": "live" if live_llm else "offline",
        "model": settings.llm_model if live_llm else "none (extractive answers)",
        "vector_store": settings.qdrant_url or f"embedded:{settings.qdrant_path}",
        "sql_dialect": settings.sql_dialect,
        "cache": "redis" if settings.redis_url else "in-process",
        "features": {
            "hyde": settings.enable_hyde,
            "crag": settings.enable_crag,
            "self_rag": settings.enable_self_rag,
            "text2sql": settings.enable_text2sql,
            "guardrails": settings.enable_guardrails,
            "cache": settings.enable_cache,
        },
    }


@app.post("/ask", response_model=AnswerResponse)
def ask(request: AskRequest) -> AnswerResponse:
    try:
        return pipeline.ask(
            request.question,
            thread_id=request.thread_id,
            force_extractive=not request.use_llm,
        )
    except Exception as exc:
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/approve", response_model=AnswerResponse)
def approve(request: ApproveRequest) -> AnswerResponse:
    try:
        return pipeline.resume(request.thread_id, approved=request.approved)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Resume failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/pending/{thread_id}")
def pending(thread_id: str) -> dict[str, Any]:
    payload = pipeline.pending_approval(thread_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="nothing awaiting approval on that thread")
    return payload


@app.post("/retrieve")
def retrieve(request: RetrieveRequest) -> dict[str, Any]:
    """Retrieval without generation - for tuning and for the UI's compare view."""
    from advanced_rag.retrieval import get_retriever

    candidate_count = request.top_k or 20
    output_count = request.top_n or candidate_count

    result = get_retriever().retrieve(
        request.query,
        use_hyde=request.use_hyde,
        rerank=request.rerank,
        mode=request.mode,
        fusion=request.fusion,
        top_k=candidate_count,
        top_n=output_count,
        category=request.category,
        doc_type=request.doc_type,
        source_prefix=request.source_prefix,
        document_id=request.document_id,
        source_version=request.source_version,
        namespace=request.namespace,
        collection=request.collection,
    )
    return {
        "query": request.query,
        "mode": request.mode,
        "fusion": request.fusion if request.mode == "hybrid" else None,
        "category": request.category,
        "candidate_count": candidate_count,
        "result_count": len(result.chunks),
        "hyde_document": result.hyde_document,
        "reranked": result.reranked,
        # Lets a client tell whether rerank_score drove the ordering or is only
        # being reported alongside it.
        "rerank_is_authoritative": result.cross_encoder,
        "ordered_by": "rerank" if (result.reranked and result.cross_encoder) else "retrieval",
        "latency_ms": {
            "total": result.elapsed_ms,
            "hyde": result.hyde_ms,
            "rerank": result.rerank_ms,
        },
        "results": [
            {
                "source": hit.chunk.source,
                "section": hit.chunk.section,
                "retrieval_score": round(hit.retrieval_score, 4),
                "rerank_score": None if hit.rerank_score is None else round(hit.rerank_score, 4),
                "retrieval_rank": hit.retrieval_rank,
                "text": hit.chunk.text,
            }
            for hit in result.chunks
        ],
    }


@app.post("/cache/clear")
def clear_cache() -> dict[str, str]:
    from advanced_rag.cache import get_cache

    get_cache().clear()
    return {"status": "cleared"}


# ------------------------------------------------------------- document management


def _sanitize_filename(filename: str | None) -> str:
    """Sanitize and validate an uploaded filename."""
    original = Path(filename or "").name
    clean = re.sub(r"[\x00/\\?%*:|\"<>]", "_", original).strip()
    clean = clean.strip(". ")
    return clean


@app.post("/documents/upload", response_model=DocumentUploadResponse)
async def upload_document(
    response: Response,
    file: Annotated[UploadFile, File(...)],
    document_id: Annotated[str | None, Form()] = None,
) -> DocumentUploadResponse:
    """Upload and index a document in one of the 7 supported formats.

    If `document_id` is provided, replaces an existing user-uploaded document.
    """
    clean_name = _sanitize_filename(file.filename)
    if not clean_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid filename",
        )

    ext = f".{clean_name.rsplit('.', 1)[-1].lower()}" if "." in clean_name else ""
    if ext not in EXTRACTOR_REGISTRY:
        supported = ", ".join(sorted({k.lstrip(".") for k in EXTRACTOR_REGISTRY.keys()}))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file format '{ext.lstrip('.')}'. Supported formats: {supported}",
        )

    registry = get_registry()
    is_replacement = False
    existing_record: dict[str, Any] | None = None

    if document_id:
        existing_record = registry.get(document_id)
        if not existing_record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document with ID '{document_id}' not found",
            )
        if existing_record.get("origin") == "built-in":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Built-in corpus documents cannot be modified or replaced",
            )
        is_replacement = True
    else:
        document_id = uuid.uuid4().hex[:12]

    upload_dir = UPLOAD_DIR / document_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    target_path = upload_dir / clean_name

    # Check path traversal
    try:
        resolved_target = target_path.resolve()
        resolved_dir = upload_dir.resolve()
        if resolved_dir not in resolved_target.parents and resolved_target != resolved_dir:
            raise ValueError("Target path outside upload directory")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file path",
        ) from exc

    # Stream file to disk while enforcing the 20MB limit
    total_size = 0
    try:
        with open(target_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):  # 1MB buffer
                total_size += len(chunk)
                if total_size > MAX_UPLOAD_SIZE:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail="File size exceeds maximum allowed limit of 20MB",
                    )
                f.write(chunk)
    except HTTPException:
        if target_path.exists():
            target_path.unlink()
        if not is_replacement:
            shutil.rmtree(upload_dir, ignore_errors=True)
        raise
    except Exception as exc:
        if target_path.exists():
            target_path.unlink()
        if not is_replacement:
            shutil.rmtree(upload_dir, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save uploaded file: {exc}",
        ) from exc

    if total_size == 0:
        if target_path.exists():
            target_path.unlink()
        if not is_replacement:
            shutil.rmtree(upload_dir, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot upload empty file",
        )

    # Extract and chunk
    source = f"uploads/{document_id}/{clean_name}"
    try:
        doc = load_document(target_path)
    except Exception as exc:
        if target_path.exists():
            target_path.unlink()
        if not is_replacement:
            shutil.rmtree(upload_dir, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Document extraction failed: {exc}",
        ) from exc

    doc.source = source
    doc.metadata["source"] = source
    doc.metadata["document_id"] = document_id

    settings = get_settings()
    chunks = chunk_document(
        text=doc.body,
        source=doc.source,
        title=doc.title,
        doc_type=doc.doc_type,
        metadata=doc.metadata,
        sections=doc.sections,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )

    if not chunks:
        if target_path.exists():
            target_path.unlink()
        if not is_replacement:
            shutil.rmtree(upload_dir, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid text chunks could be extracted from document",
        )

    # Store in Qdrant
    store = get_store()
    try:
        if is_replacement and existing_record:
            old_source = existing_record["source"]
            store.delete_document(old_source)
            # Remove old file if filename changed
            old_path = Path(existing_record.get("storage_path", ""))
            if old_path != target_path and old_path.exists():
                old_path.unlink(missing_ok=True)
        store.upsert(chunks)
    except Exception as exc:
        logger.exception("Failed to index document in vector store")
        if target_path.exists():
            target_path.unlink()
        if not is_replacement:
            shutil.rmtree(upload_dir, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Indexing failed: {exc}",
        ) from exc

    # Update metadata registry
    clean_ext = ext.lstrip(".")
    registry.register({
        "document_id": document_id,
        "filename": clean_name,
        "file_type": clean_ext,
        "title": doc.title or clean_name,
        "storage_path": str(target_path.resolve()),
        "source": source,
        "chunk_count": len(chunks),
        "status": "indexed",
    })

    if is_replacement:
        response.status_code = status.HTTP_200_OK
        msg = "Document replaced and indexed successfully"
    else:
        response.status_code = status.HTTP_201_CREATED
        msg = "Document uploaded and indexed successfully"

    return DocumentUploadResponse(
        document_id=document_id,
        filename=clean_name,
        file_type=clean_ext,
        chunks_created=len(chunks),
        ingestion_status="success",
        message=msg,
    )


@app.get("/documents")
def list_documents() -> list[dict[str, Any]]:
    """List all indexed documents, including built-in corpus and user uploads."""
    registry = get_registry()
    return registry.list_all(include_built_in=True)


@app.delete("/documents/{document_id}", response_model=DocumentDeleteResponse)
def delete_document(document_id: str) -> DocumentDeleteResponse:
    """Delete a user-uploaded document from Qdrant and storage.

    Built-in corpus documents cannot be deleted (returns 403 Forbidden).
    """
    registry = get_registry()
    record = registry.get(document_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with ID '{document_id}' not found",
        )

    if record.get("origin") == "built-in":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Built-in corpus documents cannot be deleted",
        )

    store = get_store()
    source = record["source"]
    del_res = store.delete_document(source)
    deleted_chunks = (
        del_res.get("deleted_chunks", 0)
        if isinstance(del_res, dict)
        else int(del_res or 0)
    )

    # Clean up physical files
    storage_path = Path(record.get("storage_path", ""))
    if storage_path.is_file():
        storage_path.unlink(missing_ok=True)
    if storage_path.parent.is_dir() and storage_path.parent.name == document_id:
        shutil.rmtree(storage_path.parent, ignore_errors=True)

    registry.delete(document_id)

    return DocumentDeleteResponse(
        document_id=document_id,
        deleted_chunks=deleted_chunks,
        message="Document deleted successfully",
    )


# --------------------------------------------------------------------- web UI


def _web_dir() -> Path | None:
    """Locate the web UI's static files, or None if they are not installed.

    Two layouts to cover: a source checkout keeps them in `ui/web`, while a
    wheel force-includes them next to this module. A missing directory means
    "API only", not a startup failure - the service is useful without a UI.
    """
    candidates = []
    override = os.environ.get("RAG_WEB_DIR")
    if override:
        candidates.append(Path(override))
    candidates.append(Path(__file__).parent / "web")
    candidates.append(Path(__file__).resolve().parents[3] / "ui" / "web")

    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return None


class _NoCacheStaticFiles(StaticFiles):
    """Serve the UI with revalidate-always semantics.

    Starlette's default lets a browser reuse app.js/styles.css heuristically
    without asking, which is how a cached script ends up running against a newer
    index.html and breaking - e.g. calling into an element the new HTML removed.
    `no-cache` forces an ETag revalidation on every load, so an unchanged asset
    still returns a cheap 304 but a changed one is never silently stale.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


_WEB_DIR = _web_dir()
if _WEB_DIR is None:
    logger.warning("Web UI assets not found - serving the API only")
else:
    # Mounted last so every route declared above keeps priority; `html=True`
    # makes `/` serve index.html.
    app.mount("/", _NoCacheStaticFiles(directory=_WEB_DIR, html=True), name="web")


def run() -> None:
    """`rag-api` entry point."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "advanced_rag.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
