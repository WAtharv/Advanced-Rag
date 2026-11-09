"""Qdrant-backed hybrid vector store.

The collection carries two named vectors per point - a dense embedding and a
sparse (BM25) one - so a single query can fuse lexical and semantic matches.
With QDRANT_URL unset the client runs against a local on-disk store, which keeps
the whole pipeline runnable without Docker.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient, models

from advanced_rag.config import Settings, get_settings
from advanced_rag.models import Chunk, RetrievedChunk
from advanced_rag.retrieval.embeddings import Embedder, get_embedder, normalize_scores

logger = logging.getLogger(__name__)

DENSE = "dense"
SPARSE = "sparse"


class EmbeddedStoreBusyError(RuntimeError):
    """
        API Server running
            +        
        Ingestion script running
        
    both try to access local Qdrant.
    shows a clear message.
    """
    
    """Another process holds the embedded store's exclusive lock.

    Worth its own error: the underlying message is accurate but says nothing about
    *which* processes conflict in this project, and the collision (API running
    while you re-ingest or run the eval) is the single most common local snag.
    """

    def __init__(self, path) -> None:
        super().__init__(
            f"The embedded Qdrant store at {path} is locked by another process.\n"
            "Embedded mode allows exactly one client, so `rag-api`, `rag-ingest` and\n"
            "the evaluation runner cannot overlap. Either:\n"
            "  - stop the other process (the API server is the usual culprit), or\n"
            "  - run the Qdrant container (`docker compose up -d qdrant`) and set\n"
            "    QDRANT_URL=http://localhost:6333, which supports concurrent access."
        )


class HybridStore:
    """
    This is the main Qdrant manager.
    
    Responsibilities:
        Create collection
        Store vectors
        Search vectors
        Handle local/remote Qdrant
    """
    
    """Create, populate and query the hybrid collection."""

    def __init__(
        self,
        settings: Settings | None = None,
        embedder: Embedder | None = None,
        *,
        client: QdrantClient | None = None,
        collection: str | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.embedder = embedder or get_embedder(self.settings)
        self.collection = collection or self.settings.qdrant_collection
        self._client: QdrantClient | None = client

    @property
    def client(self) -> QdrantClient:
        if self._client is None:
            if self.settings.use_remote_qdrant:
                logger.info("Connecting to Qdrant at %s", self.settings.qdrant_url)         # Remote Qdrant
                self._client = QdrantClient(
                    url=self.settings.qdrant_url,
                    api_key=self.settings.qdrant_api_key or None,
                )
            else:
                path = self.settings.absolute(self.settings.qdrant_path)
                path.mkdir(parents=True, exist_ok=True)
                logger.info("Using embedded Qdrant at %s", path)                            # Local Qdrant
                try:
                    self._client = QdrantClient(path=str(path))
                except RuntimeError as exc:
                    if "already accessed by another instance" not in str(exc):
                        raise
                    raise EmbeddedStoreBusyError(path) from exc
        return self._client

    # ------------------------------------------------------------- collection

    def ensure_collection(self, *, recreate: bool = False) -> None:
        """
        Creates collection if missing.
        
        Collection
            ├── dense vector - Used for semantic search.
            └── sparse vector - Used for keyword search.
        """
        exists = self.client.collection_exists(self.collection)
        if exists and recreate:
            logger.warning("Dropping existing collection %s", self.collection)
            self.client.delete_collection(self.collection)
            exists = False
        if exists:
            return

        # The keyword backend has no dense arm, so the collection is created
        # sparse-only rather than with a placeholder dense vector.
        vectors_config: dict[str, models.VectorParams] = {}
        if self.embedder.supports_dense:
            vectors_config[DENSE] = models.VectorParams(
                size=self.embedder.dim, distance=models.Distance.COSINE
            )
            logger.info("Creating collection %s (dense dim=%d + sparse)", self.collection,
                        self.embedder.dim)
        else:
            logger.info("Creating collection %s (sparse only)", self.collection)

        self.client.create_collection(
            collection_name=self.collection,
            vectors_config=vectors_config,
            sparse_vectors_config={
                # IDF is what turns raw term frequencies into BM25 scoring.
                SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)
            },
        )
        if not self.settings.use_remote_qdrant:
            # Embedded Qdrant ignores payload indexes and warns about it. Filtering
            # still works without them, so skip the call rather than emit noise.
            return
        for field in ("doc_type", "source", "component"):
            self.client.create_payload_index(
                collection_name=self.collection,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )

    def count(self) -> int:
        if self.client.collection_exists(self.collection):
            return self.client.count(self.collection, exact=True).count
        if self.collection != "k8s_ops" and self.client.collection_exists("k8s_ops"):
            return self.client.count("k8s_ops", exact=True).count
        return 0

    def close(self) -> None:
        """Release the client. Matters for the embedded store's file lock."""
        if self._client is not None:
            self._client.close()
            self._client = None

    # ---------------------------------------------------------------- writing

    def upsert(self, chunks: Sequence[Chunk], batch_size: int = 64) -> int:
        """
        Stores chunks in Qdrant.
        
        Generate Sparse Embeddings
        Generate Dense Embeddings
        
        build point 
        
        PointStruct(
            id=uuid,
            vector={
                dense: [...],
                sparse: [...]
            },
            payload=metadata
        )
        
        store : Chunks become searchable.
        """
        
        self.ensure_collection()
        written = 0
        for start in range(0, len(chunks), batch_size):
            batch = list(chunks[start : start + batch_size])
            texts = [c.text for c in batch]
            sparse = self.embedder.sparse_documents(texts)
            dense = self.embedder.embed_documents(texts) if self.embedder.supports_dense else None

            points = []
            for index, (chunk, sparse_vec) in enumerate(zip(batch, sparse, strict=True)):
                vector: dict[str, Any] = {
                    SPARSE: models.SparseVector(
                        indices=sparse_vec.indices, values=sparse_vec.values
                    )
                }
                if dense is not None:
                    vector[DENSE] = dense[index]
                points.append(
                    models.PointStruct(
                        id=_point_id(chunk.id), vector=vector, payload=chunk.payload()
                    )
                )
            self.client.upsert(collection_name=self.collection, points=points)              # store 
            written += len(points)
            logger.info("Indexed %d/%d chunks", written, len(chunks))
        return written

    def _scroll_point_ids(self, scroll_filter: models.Filter) -> list[str]:
        """Internal helper to scroll and return point IDs matching a filter."""
        point_ids: list[str] = []
        offset = None
        while True:
            records, next_offset = self.client.scroll(
                collection_name=self.collection,
                scroll_filter=scroll_filter,
                limit=250,
                with_payload=False,
                with_vectors=False,
                offset=offset,
            )
            point_ids.extend([str(r.id) for r in records])
            if next_offset is None or not records:
                break
            offset = next_offset
        return point_ids

    def get_point_ids(self, identifier: str) -> list[str]:
        """Retrieve point IDs for a document by source path, document_id, or canonical path."""
        if not identifier or not identifier.strip():
            return []
        if not self.client.collection_exists(self.collection):
            return []

        ident = identifier.strip()

        # 1. Search by exact source
        filter_source = models.Filter(
            must=[models.FieldCondition(key="source", match=models.MatchValue(value=ident))]
        )
        pids = self._scroll_point_ids(filter_source)
        if pids:
            return pids

        # 2. Search by document_id
        filter_doc_id = models.Filter(
            must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=ident))]
        )
        pids = self._scroll_point_ids(filter_doc_id)
        if pids:
            return pids

        # 3. Search by normalized POSIX path
        normalized = Path(ident).as_posix().lstrip("./")
        if normalized != ident:
            filter_norm = models.Filter(
                must=[models.FieldCondition(key="source", match=models.MatchValue(value=normalized))]
            )
            pids = self._scroll_point_ids(filter_norm)
            if pids:
                return pids

        # 4. Check relative to data/corpus if path was absolute or included data/corpus
        if "data/corpus/" in normalized:
            rel = normalized.split("data/corpus/", 1)[1]
            filter_rel = models.Filter(
                must=[models.FieldCondition(key="source", match=models.MatchValue(value=rel))]
            )
            pids = self._scroll_point_ids(filter_rel)
            if pids:
                return pids

        return []

    def get_point_ids_by_source(self, source: str) -> list[str]:
        """Retrieve all Qdrant point IDs associated with a specific document source."""
        return self.get_point_ids(source)

    def get_indexed_documents(self) -> dict[str, dict[str, Any]]:
        """Retrieve an aggregated mapping of all indexed documents in the collection."""
        if not self.client.collection_exists(self.collection):
            return {}

        docs: dict[str, dict[str, Any]] = {}
        offset = None

        while True:
            records, next_offset = self.client.scroll(
                collection_name=self.collection,
                limit=500,
                with_payload=["source", "document_id", "content_hash", "title", "file_type"],
                with_vectors=False,
                offset=offset,
            )
            for r in records:
                src = r.payload.get("source") if r.payload else None
                if not src:
                    continue
                if src not in docs:
                    docs[src] = {
                        "source": src,
                        "document_id": r.payload.get("document_id", ""),
                        "title": r.payload.get("title", ""),
                        "file_type": r.payload.get("file_type", ""),
                        "point_ids": [],
                        "content_hashes": set(),
                    }
                docs[src]["point_ids"].append(str(r.id))
                ch = r.payload.get("content_hash")
                if ch:
                    docs[src]["content_hashes"].add(ch)

            if next_offset is None or not records:
                break
            offset = next_offset

        for item in docs.values():
            item["chunk_count"] = len(item["point_ids"])

        return docs

    def delete_points(self, point_ids: Sequence[str]) -> int:
        """Delete specific points from Qdrant by their UUIDs."""
        if not point_ids:
            return 0
        if not self.client.collection_exists(self.collection):
            return 0

        self.client.delete(
            collection_name=self.collection,
            points_selector=models.PointIdsList(points=list(point_ids)),
        )
        logger.info("Deleted %d points from %s", len(point_ids), self.collection)
        return len(point_ids)

    def delete_document(self, identifier: str, dry_run: bool = False) -> dict[str, Any]:
        """Delete all chunks belonging to a specific document source or document_id with safety controls."""
        if not identifier or not identifier.strip():
            raise ValueError("Document identifier cannot be empty.")

        clean_id = identifier.strip()
        if clean_id in ("*", "/", "\\", ".", "~") or clean_id.startswith("*"):
            raise ValueError(
                f"Unsafe or ambiguous document identifier '{identifier}'. Wildcards and root paths are forbidden."
            )

        if ".." in clean_id:
            raise ValueError(f"Path traversal detected in identifier '{identifier}'.")

        if self.collection in ("k8s_ops", "k8s_ops_bge_small"):
            raise ValueError(f"Deletion forbidden on protected baseline collection '{self.collection}'.")

        point_ids = self.get_point_ids(clean_id)
        if dry_run:
            return {
                "source": clean_id,
                "deleted_chunks": 0,
                "matched_points": len(point_ids),
                "found": bool(point_ids),
                "dry_run": True,
            }

        deleted_count = self.delete_points(point_ids) if point_ids else 0
        return {
            "source": clean_id,
            "deleted_chunks": deleted_count,
            "matched_points": len(point_ids),
            "found": bool(point_ids),
            "dry_run": False,
        }

    def replace_document(
        self,
        chunks: Sequence[Chunk],
        source: str | None = None,
    ) -> dict[str, Any]:
        """Safely replace an existing document's chunks in Qdrant.

        1. Fetches existing point IDs for the document.
        2. Upserts the new chunks first (failing safely if upsert fails).
        3. Identifies obsolete point IDs no longer present in the updated chunks.
        4. Deletes only the obsolete IDs belonging to this document.
        """
        if not chunks:
            if source:
                res = self.delete_document(source)
                return {
                    "source": source,
                    "upserted": 0,
                    "deleted_obsolete": res["deleted_chunks"],
                    "total_chunks": 0,
                }
            return {"source": "", "upserted": 0, "deleted_obsolete": 0, "total_chunks": 0}

        doc_source = source or chunks[0].source
        self.ensure_collection()

        # Step 1: Query existing point IDs for this document
        existing_point_ids = set(self.get_point_ids_by_source(doc_source))

        # Step 2: Calculate new deterministic point IDs
        new_point_ids = {_point_id(c.id) for c in chunks}

        # Step 3: Upsert new chunks first (if this fails, obsolete IDs are never deleted)
        upserted_count = self.upsert(chunks)

        # Step 4: Isolate obsolete chunk points no longer in the updated version
        obsolete_ids = [pid for pid in existing_point_ids if pid not in new_point_ids]

        # Step 5: Clean up only obsolete points
        deleted_obsolete = self.delete_points(obsolete_ids) if obsolete_ids else 0

        return {
            "source": doc_source,
            "upserted": upserted_count,
            "deleted_obsolete": deleted_obsolete,
            "total_chunks": len(new_point_ids),
        }

    # ---------------------------------------------------------------- reading

    def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        mode: str = "hybrid",
        fusion: str | None = None,
        doc_type: str | None = None,
        category: str | None = None,
        source_prefix: str | None = None,
        document_id: str | None = None,
        source_version: str | None = None,
        namespace: str | None = None,
        collection: str | None = None,
        filter_: models.Filter | None = None,
    ) -> list[RetrievedChunk]:
        """
        Main retrival function 
        
        Supports:
        Dense Search - Semantic meaning search 
        Sparse Search - Keyword matching 
        
        hybrid search : Uses both 
        """
        
        """Retrieve chunks for a query.

        mode:   dense | sparse | hybrid
        fusion: weighted (client-side, honours HYBRID_DENSE_WEIGHT) or rrf
                (server-side reciprocal rank fusion, weight-free).
        """
        top_k = top_k or self.settings.retrieve_top_k

        target_collection = collection or self.collection
        if not self.client.collection_exists(target_collection):
            if (
                self.settings.allow_collection_fallback
                and target_collection != "k8s_ops"
                and self.client.collection_exists("k8s_ops")
            ):
                logger.warning(
                    "Collection %s is missing; explicitly falling back to baseline collection 'k8s_ops'",
                    target_collection,
                )
                target_collection = "k8s_ops"
            else:
                raise RuntimeError(
                    f"Qdrant collection '{target_collection}' does not exist. "
                    "Run 'rag-ingest' to create and index it, or set ALLOW_COLLECTION_FALLBACK=true to fall back to the baseline collection."
                )

        # Check if the target collection actually supports dense vectors
        has_dense = False
        try:
            coll_info = self.client.get_collection(target_collection)
            vectors_cfg = getattr(coll_info.config.params, "vectors", {})
            has_dense = DENSE in vectors_cfg if isinstance(vectors_cfg, dict) else bool(vectors_cfg)
        except Exception:
            has_dense = self.embedder.supports_dense

        if (not self.embedder.supports_dense or not has_dense):
            if mode != "sparse":
                logger.info(
                    "Collection '%s' does not carry dense vectors (or embedder lacks dense support) - serving search as sparse-only (BM25)",
                    target_collection,
                )
                mode = "sparse"
        else:
            logger.info("Serving search as hybrid (dense + sparse) on collection '%s'", target_collection)

        query_filter = filter_ if filter_ is not None else build_filter(
            category=category,
            doc_type=doc_type,
            source_prefix=source_prefix,
            document_id=document_id,
            source_version=source_version,
            namespace=namespace,
        )

        orig_collection = self.collection
        try:
            self.collection = target_collection
            if mode == "dense":
                return self._single(query, top_k, DENSE, query_filter)
            if mode == "sparse":
                return self._single(query, top_k, SPARSE, query_filter)
            effective_fusion = fusion if fusion is not None else getattr(self.settings, "hybrid_fusion", "rrf")
            if effective_fusion == "rrf":
                return self._hybrid_rrf(query, top_k, query_filter)
            return self._hybrid_weighted(query, top_k, query_filter)
        finally:
            self.collection = orig_collection

    def _single(
        self, query: str, top_k: int, using: str, query_filter: models.Filter | None
    ) -> list[RetrievedChunk]:
        """
        Performs one search.
        
        Dense -> vector -> Qdrant search
        Sparse -> BM25 vector -> Qdrant search 
        
        Returns : Retrived chunk with score 
        """
        
        vector: Any
        if using == DENSE:
            vector = self.embedder.embed_query(query)
        else:
            sparse = self.embedder.sparse_query(query)
            vector = models.SparseVector(indices=sparse.indices, values=sparse.values)

        response = self.client.query_points(
            collection_name=self.collection,
            query=vector,
            using=using,
            limit=top_k,
            query_filter=query_filter,
            with_payload=True,
        )
        results = [_to_retrieved(p, p.score) for p in response.points]
        for rank, result in enumerate(results):
            if using == DENSE:
                result.dense_rank = rank
            else:
                result.sparse_rank = rank
        return results

    def _prefetch(self, query: str, limit: int) -> list[models.Prefetch]:
        sparse = self.embedder.sparse_query(query)
        return [
            models.Prefetch(query=self.embedder.embed_query(query), using=DENSE, limit=limit),
            models.Prefetch(
                query=models.SparseVector(indices=sparse.indices, values=sparse.values),
                using=SPARSE,
                limit=limit,
            ),
        ]

    def _hybrid_rrf(
        self, query: str, top_k: int, query_filter: models.Filter | None
    ) -> list[RetrievedChunk]:
        """Server-side reciprocal rank fusion - one round trip, no tunable weight.

        Uses an expanded prefetch candidate pool to prevent shallow-pool truncation
        and starvation before rank aggregation.
        """
        prefetch_limit = getattr(self.settings, "hybrid_prefetch_limit", None) or max(top_k * 2, top_k + 20)
        prefetch_limit = max(prefetch_limit, top_k)
        try:
            response = self.client.query_points(
                collection_name=self.collection,
                prefetch=self._prefetch(query, prefetch_limit),
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=top_k,
                query_filter=query_filter,
                with_payload=True,
            )
            return [_to_retrieved(p, p.score) for p in response.points]
        except Exception as e:
            logger.warning("Server-side RRF fusion failed (%s); falling back to single branch search", e)
            try:
                return self._single(query, top_k, DENSE, query_filter)
            except Exception:
                return self._single(query, top_k, SPARSE, query_filter)

    def _hybrid_weighted(
        self, query: str, top_k: int, query_filter: models.Filter | None
    ) -> list[RetrievedChunk]:
        """Client-side fusion of min-max normalised dense and sparse scores.

        Slightly more work than RRF, but HYBRID_DENSE_WEIGHT becomes a real dial:
        push it up for paraphrased questions, down for exact error strings and
        resource names - which is most of Kubernetes troubleshooting.
        """
        weight = self.settings.hybrid_dense_weight
        # Over-fetch each arm so the fused top_k is drawn from a wider pool.
        pool = max(top_k * 2, top_k + 20)

        dense_hits: list[RetrievedChunk] = []
        try:
            dense_hits = self._single(query, pool, DENSE, query_filter)
        except Exception as e:
            logger.warning("Dense retrieval branch failed in hybrid search: %s", e)

        sparse_hits: list[RetrievedChunk] = []
        try:
            sparse_hits = self._single(query, pool, SPARSE, query_filter)
        except Exception as e:
            logger.warning("Sparse retrieval branch failed in hybrid search: %s", e)

        # Dynamic branch weighting: if one branch fails or returns empty, allocate full weight
        # to the surviving branch rather than penalizing scores by an inactive arm.
        if dense_hits and not sparse_hits:
            dense_weight, sparse_weight = 1.0, 0.0
        elif sparse_hits and not dense_hits:
            dense_weight, sparse_weight = 0.0, 1.0
        elif not dense_hits and not sparse_hits:
            return []
        else:
            dense_weight, sparse_weight = weight, 1.0 - weight

        merged: dict[str, RetrievedChunk] = {}
        for hits, arm_weight, is_dense in (
            (dense_hits, dense_weight, True),
            (sparse_hits, sparse_weight, False),
        ):
            if not hits or arm_weight <= 0.0:
                continue
            normalized = normalize_scores(h.retrieval_score for h in hits)
            for score, hit in zip(normalized, hits, strict=True):
                existing = merged.get(hit.chunk.id)
                if existing is None:
                    hit.retrieval_score = score * arm_weight
                    merged[hit.chunk.id] = hit
                    continue
                existing.retrieval_score += score * arm_weight
                if is_dense:
                    existing.dense_rank = hit.dense_rank
                else:
                    existing.sparse_rank = hit.sparse_rank

        # Deterministic ranking: primary by fused score descending,
        # secondary by dense rank ascending, tertiary by sparse rank ascending,
        # quaternary by stable chunk ID ascending.
        ranked = sorted(
            merged.values(),
            key=lambda r: (
                -r.retrieval_score,
                r.dense_rank if r.dense_rank is not None else 9999,
                r.sparse_rank if r.sparse_rank is not None else 9999,
                r.chunk.id,
            ),
        )
        return ranked[:top_k]


def build_filter(
    *,
    category: str | None = None,
    doc_type: str | None = None,
    source_prefix: str | None = None,
    document_id: str | None = None,
    source_version: str | None = None,
    namespace: str | None = None,
    custom_filter: models.Filter | None = None,
) -> models.Filter | None:
    """Build a unified Qdrant metadata filter across multiple fields.

    Validates and sanitizes inputs (ignoring empty or whitespace-only strings).
    Applies consistently across dense, sparse, and hybrid retrieval.
    If no criteria are provided, returns custom_filter or None.
    """
    conditions: list[Any] = []

    if custom_filter is not None and custom_filter.must:
        conditions.extend(custom_filter.must)

    # Namespace alias handling: if namespace is specified, treat as category/source namespace
    target_category = category
    if not target_category and namespace and isinstance(namespace, str) and namespace.strip():
        target_category = namespace.strip()

    if target_category is not None and isinstance(target_category, str):
        clean_cat = target_category.strip()
        if clean_cat:
            norm_cat = clean_cat.lower()
            if norm_cat in {"kubernetes", "helm", "prometheus", "sre"}:
                conditions.append(
                    models.Filter(
                        should=[
                            models.FieldCondition(key="category", match=models.MatchValue(value=clean_cat)),
                            models.FieldCondition(key="source", match=models.MatchPrefix(prefix=f"{norm_cat}/")),
                            models.FieldCondition(key="category", match=models.MatchValue(value=f"runbooks/{norm_cat}")),
                        ]
                    )
                )
            else:
                conditions.append(
                    models.Filter(
                        should=[
                            models.FieldCondition(key="category", match=models.MatchValue(value=clean_cat)),
                            models.FieldCondition(key="source", match=models.MatchPrefix(prefix=f"{clean_cat}/")),
                        ]
                    )
                )

    if doc_type is not None and isinstance(doc_type, str):
        clean_dt = doc_type.strip()
        if clean_dt:
            conditions.append(models.FieldCondition(key="doc_type", match=models.MatchValue(value=clean_dt)))

    if source_prefix is not None and isinstance(source_prefix, str):
        clean_sp = source_prefix.strip()
        if clean_sp:
            conditions.append(models.FieldCondition(key="source", match=models.MatchPrefix(prefix=clean_sp)))

    if document_id is not None and isinstance(document_id, str):
        clean_did = document_id.strip()
        if clean_did:
            conditions.append(models.FieldCondition(key="document_id", match=models.MatchValue(value=clean_did)))

    if source_version is not None and isinstance(source_version, str):
        clean_sv = source_version.strip()
        if clean_sv:
            conditions.append(models.FieldCondition(key="source_version", match=models.MatchValue(value=clean_sv)))

    if not conditions:
        return custom_filter

    return models.Filter(must=conditions)


def _doc_type_filter(doc_type: str | None) -> models.Filter | None:
    """Filters documents based on doc_type, delegating to build_filter."""
    return build_filter(doc_type=doc_type)


def _point_id(chunk_id: str) -> str:
    """
    Qdrant requires:
        Plain Text : UUID
    
        py: Converts - chunk_id
        
        py: uuid.uuid5(...)

        Plain Text : 2c31a4d4-fc...
    """
    
    """Qdrant point IDs must be UUIDs or unsigned ints; derive one deterministically."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def _to_retrieved(point: Any, score: float) -> RetrievedChunk:
    """
    Converts raw Qdrant result into: RetrievedChunk
    
    Contains:{
            chunk,
            retrieval_score
        }
    
    This is what the RAG pipeline uses.
    """
    
    payload = dict(point.payload or {})
    known = {"chunk_id", "text", "source", "title", "section", "doc_type"}
    chunk = Chunk(
        id=payload.get("chunk_id") or str(point.id),
        text=payload.get("text", ""),
        source=payload.get("source", ""),
        title=payload.get("title", ""),
        section=payload.get("section", ""),
        doc_type=payload.get("doc_type", "runbook"),
        metadata={k: v for k, v in payload.items() if k not in known},
    )
    return RetrievedChunk(chunk=chunk, retrieval_score=float(score))


_store: HybridStore | None = None


def get_active_store() -> HybridStore | None:
    """Return the global store singleton if already initialized and active, else None."""
    global _store
    return _store


def reset_store() -> None:
    """Close and clear the global store singleton to free file locks."""
    global _store
    if _store is not None:
        try:
            _store.close()
        except Exception:
            pass
        _store = None


def get_store() -> HybridStore:
    """
    Singleton pattern.
        get_store()
    
    First call:
        HybridStore()
    
    Next calls:
        same object reused
        
    avoids multiple connections.
    """
    
    global _store
    if _store is None:
        _store = HybridStore()
    return _store


"""
Documents
    ↓
Chunking
    ↓
Embeddings (Dense + Sparse)
    ↓
Qdrant Storage
    ↓
User Query
    ↓
Dense Search + Sparse Search
    ↓
Fusion (Weighted / RRF)
    ↓
Top Relevant Chunks
"""


"""
This module implements a Hybrid Vector Store using Qdrant. During ingestion, each chunk is 
converted into both dense embeddings for semantic search and sparse BM25 vectors for keyword search, 
then stored in a single collection. During retrieval, the query can run dense, sparse, or hybrid search. 
Hybrid mode combines semantic and lexical relevance using either Reciprocal Rank Fusion (RRF) or weighted score fusion, 
which improves retrieval accuracy for RAG systems by handling both paraphrased queries and exact error messages.
"""