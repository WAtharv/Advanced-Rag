"""Local runtime verification script for Enterprise Advanced RAG / Kubernetes SRE Copilot.

Tests:
1. Server startup and endpoint reachability
2. Health check and active collection resolution
3. OpenAPI and documentation endpoints
4. Knowledge-base statistics and documents endpoints
5. Ingestion status endpoint
6. Web UI static assets serving (index.html, app.js, styles.css)
7. Real RAG queries across Kubernetes, Helm, Prometheus, and SRE incident response
8. Hybrid search, RRF fusion, citations, and metadata verification
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from typing import Any
import urllib.request
import urllib.error

# Ensure runtime environment defaults to sre_copilot_v1
os.environ.setdefault("QDRANT_COLLECTION", "sre_copilot_v1")
os.environ.setdefault("LLM_PROVIDER", "offline")

import uvicorn
from advanced_rag.config import get_settings
from advanced_rag.api.main import app

logger = logging.getLogger("runtime_verify")


class ServerThread(threading.Thread):
    def __init__(self, host: str, port: int):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
        )
        self.server = uvicorn.Server(self.config)

    def run(self):
        self.server.run()

    def stop(self):
        self.server.should_exit = True


def http_get(url: str, timeout: float = 10.0) -> tuple[int, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "LocalVerifier/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            ct = resp.headers.get("content-type", "")
            if "application/json" in ct:
                return resp.status, json.loads(data.decode("utf-8"))
            return resp.status, data.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as err:
        data = err.read().decode("utf-8", errors="replace")
        try:
            return err.code, json.loads(data)
        except Exception:
            return err.code, data


def http_post(url: str, payload: dict, timeout: float = 30.0) -> tuple[int, Any]:
    raw = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=raw,
        headers={
            "User-Agent": "LocalVerifier/1.0",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            return resp.status, json.loads(data.decode("utf-8"))
    except urllib.error.HTTPError as err:
        data = err.read().decode("utf-8", errors="replace")
        try:
            return err.code, json.loads(data)
        except Exception:
            return err.code, data


def main() -> int:
    settings = get_settings()
    host = "127.0.0.1"
    port = 8011  # Use dedicated port for verification to avoid clashes

    print("=" * 70)
    print("STARTING LOCAL APPLICATION RUNTIME VERIFICATION")
    print(f"Host: {host} | Port: {port} | Collection: {settings.qdrant_collection}")
    print("=" * 70)

    server = ServerThread(host, port)
    server.start()

    # Wait for server to come up
    base_url = f"http://{host}:{port}"
    health_url = f"{base_url}/health"
    ready = False
    for attempt in range(20):
        time.sleep(0.5)
        try:
            status, body = http_get(health_url, timeout=2.0)
            if status == 200:
                ready = True
                break
        except Exception:
            pass

    if not ready:
        print("[FAIL] Server did not become ready within 10 seconds.")
        server.stop()
        return 1

    print("[PASS] Server successfully started and listening on:", base_url)

    # -------------------------------------------------------------------------
    # PART 4: BACKEND VERIFICATION
    # -------------------------------------------------------------------------
    print("\n" + "-" * 50)
    print("PART 4: VERIFYING BACKEND ENDPOINTS")
    print("-" * 50)

    # 1. Health Endpoint
    status, health_data = http_get(health_url)
    print(f"1. /health -> Status: {status}")
    print(f"   Health Status:   {health_data.get('status')}")
    print(f"   Indexed Chunks:  {health_data.get('indexed_chunks')}")
    print(f"   LLM Mode:        {health_data.get('llm_mode')}")
    print(f"   Model:           {health_data.get('model')}")
    print(f"   Vector Store:    {health_data.get('vector_store')}")
    print(f"   SQL Dialect:     {health_data.get('sql_dialect')}")
    assert status == 200, "Health check failed"

    # 2. Docs / OpenAPI
    status_docs, _ = http_get(f"{base_url}/docs")
    status_openapi, openapi_data = http_get(f"{base_url}/openapi.json")
    print(f"2. /docs -> Status: {status_docs} | /openapi.json -> Status: {status_openapi}")
    assert status_docs == 200 and status_openapi == 200
    print(f"   API Title: {openapi_data.get('info', {}).get('title')} v{openapi_data.get('info', {}).get('version')}")

    # 3. Knowledge Stats
    status_stats, stats_data = http_get(f"{base_url}/api/knowledge/stats?collection=sre_copilot_v1")
    print(f"3. /api/knowledge/stats -> Status: {status_stats}")
    inv = stats_data.get("inventory", {})
    idx = stats_data.get("indexing", {})
    src = stats_data.get("source_information", {})
    print(f"   Total Documents:     {inv.get('total_documents')}")
    print(f"   Categories Count:    {len(inv.get('by_category', {}))}")
    print(f"   Total Qdrant Points: {idx.get('total_indexed_chunks')}")
    print(f"   Indexed Documents:   {idx.get('indexed_documents_count')}")
    print(f"   Source URL Coverage: {src.get('source_url_percentage')}%")
    print(f"   License Compliance:  {src.get('license_completeness_percentage')}%")
    assert status_stats == 200

    # 4. Knowledge Documents (Paginated)
    status_docs_api, docs_payload = http_get(f"{base_url}/api/knowledge/documents?page=1&page_size=5")
    print(f"4. /api/knowledge/documents -> Status: {status_docs_api}")
    print(f"   Total Tracked:  {docs_payload.get('total')}")
    print(f"   Page Items:     {len(docs_payload.get('items', []))}")
    print(f"   First Doc ID:   {docs_payload.get('items', [{}])[0].get('document_id')}")
    print(f"   First Title:    {docs_payload.get('items', [{}])[0].get('title')}")
    assert status_docs_api == 200 and docs_payload.get("total", 0) > 0

    # 5. Ingestion Status
    status_ingest, ingest_data = http_get(f"{base_url}/api/knowledge/ingestion/status")
    print(f"5. /api/knowledge/ingestion/status -> Status: {status_ingest}")
    print(f"   Current Operation: {ingest_data.get('current_operation_status')}")
    print(f"   Last Sync:         {ingest_data.get('last_sync_timestamp')}")
    assert status_ingest == 200

    # 6. Knowledge Categories
    status_cats, cats_data = http_get(f"{base_url}/api/knowledge/categories?collection=sre_copilot_v1")
    print(f"6. /api/knowledge/categories -> Status: {status_cats} ({len(cats_data)} categories)")
    for c in cats_data[:4]:
        print(f"   - {c.get('category')}: {c.get('document_count')} docs, {c.get('indexed_chunk_count')} chunks")
    assert status_cats == 200

    # 7. Retrieval Endpoint (/retrieve)
    status_ret, ret_data = http_post(
        f"{base_url}/retrieve",
        {"query": "CrashLoopBackOff triage commands", "top_k": 5, "mode": "hybrid", "fusion": "rrf"},
    )
    print(f"7. /retrieve -> Status: {status_ret}")
    chunks = ret_data.get("chunks", [])
    print(f"   Retrieved Chunks: {len(chunks)}")
    for i, c in enumerate(chunks[:3], 1):
        print(f"   [{i}] Score: {c.get('score'):.4f} | {c.get('source')} | {c.get('title')} ({c.get('section')})")
    assert status_ret == 200 and len(chunks) > 0

    # -------------------------------------------------------------------------
    # PART 6: FRONTEND STATIC ASSETS VERIFICATION
    # -------------------------------------------------------------------------
    print("\n" + "-" * 50)
    print("PART 6: VERIFYING FRONTEND SERVING")
    print("-" * 50)
    status_html, html_content = http_get(f"{base_url}/")
    print(f"1. GET / (index.html) -> Status: {status_html} ({len(html_content)} chars)")
    assert status_html == 200
    assert "Kubernetes SRE Copilot" in html_content
    assert "app.js" in html_content
    assert "styles.css" in html_content

    status_js, js_content = http_get(f"{base_url}/app.js")
    print(f"2. GET /app.js -> Status: {status_js} ({len(js_content)} chars)")
    assert status_js == 200 and len(js_content) > 1000

    status_css, css_content = http_get(f"{base_url}/styles.css")
    print(f"3. GET /styles.css -> Status: {status_css} ({len(css_content)} chars)")
    assert status_css == 200 and len(css_content) > 1000

    status_md, md_content = http_get(f"{base_url}/markdown.js")
    print(f"4. GET /markdown.js -> Status: {status_md} ({len(md_content)} chars)")
    assert status_md == 200 and len(md_content) > 1000

    # -------------------------------------------------------------------------
    # PART 5: REAL RAG QUERY VERIFICATION
    # -------------------------------------------------------------------------
    print("\n" + "-" * 50)
    print("PART 5: REAL RAG QUERIES THROUGH /ask")
    print("-" * 50)

    queries = [
        "Explain how to troubleshoot CrashLoopBackOff.",
        "How do I investigate an OOMKilled pod?",
        "How do I diagnose a failed Helm upgrade?",
        "How do I investigate Prometheus targets being DOWN?",
        "What should an SRE do during a P1 incident?",
    ]

    for idx, q in enumerate(queries, 1):
        print(f"\n[Query {idx}/5] \"{q}\"")
        status_ask, ask_res = http_post(f"{base_url}/ask", {"question": q, "mode": "hybrid"})
        print(f"Status: {status_ask}")
        assert status_ask == 200, f"Query failed with status {status_ask}"

        route = ask_res.get("route")
        extractive = ask_res.get("extractive", False)
        answer = ask_res.get("answer", "")
        citations = ask_res.get("citations", [])
        trace = ask_res.get("trace", [])

        print(f"Route:       {route}")
        print(f"Extractive:  {extractive}")
        print(f"Citations:   {len(citations)} source citations returned")
        print(f"Trace Steps: {len(trace)} nodes executed ({', '.join(t.get('node') for t in trace)})")

        for c_idx, cit in enumerate(citations[:3], 1):
            print(f"  Citation {c_idx}: {cit.get('source')} -> {cit.get('title')} ({cit.get('section')}) [Score: {cit.get('score'):.4f}]")

        clean_snippet = answer[:200].replace("\n", " ").strip()
        print(f"Answer snippet: {clean_snippet}...")
        assert len(citations) > 0, f"Query '{q}' returned no citations"
        assert len(answer) > 50, f"Query '{q}' returned an empty or stub answer"

    # -------------------------------------------------------------------------
    # SHUTDOWN
    # -------------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("ALL RUNTIME TESTS PASSED! SHUTTING DOWN TEST SERVER.")
    print("=" * 70)
    server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

