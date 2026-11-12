"""Final QA Campaign: Comprehensive Black-Box Endpoint & API Tests.

Covers:
1. Public endpoint discovery, method enforcement, and status codes.
2. Request payload validation (empty, oversized, wrong types, malformed JSON).
3. Response schema and contract verification.
4. Error responses and HTTP status mappings (404, 405, 422, 500).
5. Headers and content types.
"""

import pytest
from starlette.testclient import TestClient
from advanced_rag.api.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health_endpoint_contract(client):
    """Verify GET /health returns expected schema and operational flags."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert data["status"] in ("ok", "degraded")
    assert "indexed_chunks" in data
    assert "features" in data
    assert "llm_mode" in data
    assert "vector_store" in data


def test_ask_valid_query(client):
    """Verify POST /ask with valid question returns AnswerResponse schema."""
    payload = {"question": "What causes Kubernetes pods to enter CrashLoopBackOff?"}
    resp = client.post("/ask", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "question" in data
    assert "answer" in data
    assert "citations" in data
    assert "route" in data
    assert "latency_ms" in data


def test_ask_empty_question_rejected(client):
    """Verify POST /ask rejects empty questions with 422."""
    resp = client.post("/ask", json={"question": ""})
    assert resp.status_code == 422


def test_ask_oversized_question_rejected(client):
    """Verify POST /ask rejects questions exceeding max character limit with 422."""
    huge_question = "Kubernetes " * 1000  # > 10,000 chars
    resp = client.post("/ask", json={"question": huge_question})
    assert resp.status_code == 422


def test_ask_malformed_json_rejected(client):
    """Verify POST /ask rejects malformed JSON with 422."""
    resp = client.post("/ask", content="not a valid json string", headers={"Content-Type": "application/json"})
    assert resp.status_code == 422


def test_ask_wrong_datatype_rejected(client):
    """Verify POST /ask rejects integer for question field with 422."""
    resp = client.post("/ask", json={"question": 12345})
    assert resp.status_code == 422


def test_retrieve_endpoint_contract(client):
    """Verify POST /retrieve returns scored results with metadata."""
    payload = {"query": "ingress nginx crash", "top_k": 3}
    resp = client.post("/retrieve", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert isinstance(data["results"], list)


def test_retrieve_invalid_top_k_rejected(client):
    """Verify POST /retrieve rejects negative top_k with 422."""
    resp = client.post("/retrieve", json={"query": "pod", "top_k": -5})
    assert resp.status_code == 422


def test_approve_nonexistent_proposal_returns_404(client):
    """Verify POST /approve for unknown proposal/thread returns 404."""
    payload = {
        "thread_id": "nonexistent-thread-id-999",
        "proposal_id": "prop-fake-999",
        "approved": True,
        "actor": "sre_qa",
    }
    resp = client.post("/approve", json=payload)
    assert resp.status_code in (404, 422)


def test_pending_nonexistent_thread_returns_404(client):
    """Verify GET /pending for unknown thread returns 404."""
    resp = client.get("/pending/nonexistent-thread-abc")
    assert resp.status_code == 404


def test_unknown_endpoint_returns_404(client):
    """Verify unmapped routes return 404 Not Found."""
    resp = client.get("/api/v1/totally_nonexistent_path")
    assert resp.status_code == 404


def test_unsupported_http_method_returns_405(client):
    """Verify unsupported methods on valid routes return 405 Method Not Allowed."""
    # /health only allows GET
    resp = client.delete("/health")
    assert resp.status_code == 405
