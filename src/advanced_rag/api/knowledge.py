"""FastAPI router for knowledge-base statistics, document registry, and ingestion status.

Exposes read-only endpoints:
- GET /api/knowledge/stats
- GET /api/knowledge/documents
- GET /api/knowledge/documents/{document_id}
- GET /api/knowledge/ingestion/status
- GET /api/knowledge/categories
"""

from __future__ import annotations

import math
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from advanced_rag.config import Settings, get_settings
from advanced_rag.ingestion.persistent_registry import PersistentDocumentRegistry
from advanced_rag.ingestion.stats import KnowledgeBaseStatsService
from advanced_rag.retrieval.vectorstore import HybridStore, get_active_store, get_store

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

_global_registry: PersistentDocumentRegistry | None = None


def get_persistent_registry() -> PersistentDocumentRegistry:
    """Dependency provider for persistent document registry."""
    global _global_registry
    if _global_registry is None:
        _global_registry = PersistentDocumentRegistry()
    return _global_registry


def get_stats_service(
    registry: Annotated[PersistentDocumentRegistry, Depends(get_persistent_registry)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> KnowledgeBaseStatsService:
    """Dependency provider for knowledge base statistics service."""
    return KnowledgeBaseStatsService(registry=registry, settings=settings)


# -----------------------------------------------------------------------------
# Response Models
# -----------------------------------------------------------------------------
class DocumentItemResponse(BaseModel):
    document_id: str
    source: str
    title: str
    file_type: str
    content_hash: str
    category: str | None = None
    component: str | None = None
    doc_type: str = "runbook"
    source_url: str | None = None
    source_repository: str | None = None
    source_version: str | None = None
    source_commit: str | None = None
    spdx_license: str | None = None
    attribution: str | None = None
    document_status: str
    indexed_chunk_count: int
    collection_name: str
    created_at: str | None = None
    updated_at: str | None = None
    last_indexed_at: str | None = None
    last_error: str | None = None


class PaginatedDocumentsResponse(BaseModel):
    items: list[DocumentItemResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class CategorySummaryResponse(BaseModel):
    category: str
    document_count: int
    indexed_chunk_count: int
    status: str


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------
@router.get("/stats")
def get_knowledge_stats(
    collection: Annotated[str | None, Query(description="Qdrant collection name to evaluate")] = None,
    service: KnowledgeBaseStatsService = Depends(get_stats_service),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Retrieve full knowledge-base statistics (Inventory, Indexing, Ingestion, Sources)."""
    target_collection = collection or settings.qdrant_collection
    active = get_active_store()
    if active is not None and active._client is not None:
        if target_collection and target_collection != active.collection:
            custom_store = HybridStore(settings=settings, collection=target_collection, client=active.client)
            return service.get_full_statistics(store=custom_store)
        return service.get_full_statistics(store=active)

    store = None
    try:
        store = HybridStore(settings=settings, collection=target_collection)
        return service.get_full_statistics(store=store)
    except Exception as exc:
        return service.get_full_statistics(store=None)
    finally:
        if store is not None:
            store.close()


@router.get("/documents", response_model=PaginatedDocumentsResponse)
def list_knowledge_documents(
    page: Annotated[int, Query(ge=1, description="Page number starting at 1")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 20,
    category: Annotated[str | None, Query(description="Filter by category")] = None,
    status: Annotated[str | None, Query(description="Filter by document status")] = None,
    component: Annotated[str | None, Query(description="Filter by component")] = None,
    file_type: Annotated[str | None, Query(description="Filter by file type")] = None,
    registry: PersistentDocumentRegistry = Depends(get_persistent_registry),
) -> PaginatedDocumentsResponse:
    """Retrieve paginated document metadata records with optional filtering."""
    offset = (page - 1) * page_size
    records, total = registry.list_documents(
        category=category,
        status=status,
        component=component,
        file_type=file_type,
        limit=page_size,
        offset=offset,
    )
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    items = [
        DocumentItemResponse(
            document_id=r["document_id"],
            source=r["source"],
            title=r["title"],
            file_type=r["file_type"],
            content_hash=r["content_hash"],
            category=r["category"],
            component=r["component"],
            doc_type=r["doc_type"],
            source_url=r["source_url"],
            source_repository=r["source_repository"],
            source_version=r["source_version"],
            source_commit=r["source_commit"],
            spdx_license=r["spdx_license"],
            attribution=r["attribution"],
            document_status=r["document_status"],
            indexed_chunk_count=r["indexed_chunk_count"],
            collection_name=r["collection_name"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            last_indexed_at=r["last_indexed_at"],
            last_error=r["last_error"],
        )
        for r in records
    ]

    return PaginatedDocumentsResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/documents/{document_id}", response_model=DocumentItemResponse)
def get_knowledge_document(
    document_id: str,
    registry: PersistentDocumentRegistry = Depends(get_persistent_registry),
) -> DocumentItemResponse:
    """Retrieve detailed metadata for a single document by its document_id."""
    doc = registry.get(document_id)
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with ID '{document_id}' not found",
        )

    return DocumentItemResponse(
        document_id=doc["document_id"],
        source=doc["source"],
        title=doc["title"],
        file_type=doc["file_type"],
        content_hash=doc["content_hash"],
        category=doc["category"],
        component=doc["component"],
        doc_type=doc["doc_type"],
        source_url=doc["source_url"],
        source_repository=doc["source_repository"],
        source_version=doc["source_version"],
        source_commit=doc["source_commit"],
        spdx_license=doc["spdx_license"],
        attribution=doc["attribution"],
        document_status=doc["document_status"],
        indexed_chunk_count=doc["indexed_chunk_count"],
        collection_name=doc["collection_name"],
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
        last_indexed_at=doc["last_indexed_at"],
        last_error=doc["last_error"],
    )


@router.get("/ingestion/status")
def get_knowledge_ingestion_status(
    service: KnowledgeBaseStatsService = Depends(get_stats_service),
) -> dict[str, Any]:
    """Retrieve operational ingestion status, timestamps, and latest run metrics."""
    return service.get_ingestion_status()


@router.get("/categories", response_model=list[CategorySummaryResponse])
def get_knowledge_categories(
    collection: Annotated[str | None, Query(description="Qdrant collection name to evaluate")] = None,
    service: KnowledgeBaseStatsService = Depends(get_stats_service),
    settings: Settings = Depends(get_settings),
) -> list[CategorySummaryResponse]:
    """Retrieve category list with document counts and indexed chunk counts."""
    target_collection = collection or settings.qdrant_collection
    active = get_active_store()
    if active is not None and active._client is not None:
        if target_collection and target_collection != active.collection:
            custom_store = HybridStore(settings=settings, collection=target_collection, client=active.client)
            summaries = service.get_categories_summary(store=custom_store)
        else:
            summaries = service.get_categories_summary(store=active)
    else:
        store = None
        try:
            store = HybridStore(settings=settings, collection=target_collection)
            summaries = service.get_categories_summary(store=store)
        except Exception:
            summaries = service.get_categories_summary(store=None)
        finally:
            if store is not None:
                store.close()

    return [
        CategorySummaryResponse(
            category=s["category"],
            document_count=s["document_count"],
            indexed_chunk_count=s["indexed_chunk_count"],
            status=s["status"],
        )
        for s in summaries
    ]

