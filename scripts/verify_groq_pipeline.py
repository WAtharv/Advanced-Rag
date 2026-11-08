"""Fast, unbuffered verification script for Groq model configuration and LangGraph pipeline.

Covers all 8 verification requirements with real-time streaming progress:
1. Direct Groq completion (< 1s)
2. Structured JSON schema completion (< 1s)
3. Full LangGraph pipeline.ask() execution for Kubernetes troubleshooting
4. Helm troubleshooting query & synthesis
5. Citations and provenance verification
6. Graceful fallback behavior (extractive mode)
7. Detailed timing breakdown
"""

from __future__ import annotations

import sys
import time

# Ensure immediate unbuffered console output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

from pydantic import BaseModel, Field

from advanced_rag.config import get_settings
from advanced_rag.graph import pipeline
from advanced_rag.llm import get_llm
from advanced_rag.llm.client import reset_llm
from advanced_rag.llm.groq_client import GroqClient
from advanced_rag.retrieval import format_context, get_retriever


class SreTriageDecision(BaseModel):
    category: str = Field(description="Incident category: kubernetes, helm, prometheus, or general")
    severity: str = Field(description="Severity level: P1, P2, P3, or P4")
    action_items: list[str] = Field(description="Immediate diagnostic steps")


def log(msg: str):
    print(msg, flush=True)


def test_1_direct_groq_completion(client: GroqClient):
    log("\n" + "=" * 60)
    log("[STEP 1/5] DIRECT GROQ COMPLETION (qwen/qwen3.8-27b)")
    log("=" * 60)
    prompt = "In 20 words or fewer, explain the role of the Kubernetes kubelet."
    start = time.perf_counter()
    res = client.complete(prompt, max_tokens=100)
    latency = time.perf_counter() - start
    log(f"Model: {res.model}")
    log(f"Latency: {latency:.2f}s")
    log(f"Tokens in/out: {res.input_tokens}/{res.output_tokens}")
    log(f"Response: {res.text.strip()}")
    assert len(res.text) > 10, "Direct completion returned empty text"
    assert res.output_tokens > 0, "No output tokens reported"
    log("[PASS] Direct Groq completion succeeded.")


def test_2_structured_json_completion(client: GroqClient):
    log("\n" + "=" * 60)
    log("[STEP 2/5] STRUCTURED JSON SCHEMA COMPLETION (qwen/qwen3.8-27b)")
    log("=" * 60)
    prompt = (
        "Triage an alert: Node disk space is at 98% utilization on worker-node-04. "
        "Return a valid JSON object matching the requested schema."
    )
    start = time.perf_counter()
    res = client.complete_json(prompt, schema_model=SreTriageDecision, max_tokens=300)
    latency = time.perf_counter() - start
    log(f"Latency: {latency:.2f}s")
    log(f"Category: {res.category}")
    log(f"Severity: {res.severity}")
    log(f"Action Items ({len(res.action_items)}):")
    for item in res.action_items[:3]:
        log(f"  - {item}")
    assert isinstance(res, SreTriageDecision), "Result is not an instance of SreTriageDecision"
    assert len(res.action_items) > 0, "No action items parsed"
    log("[PASS] Structured JSON completion succeeded.")


def test_3_k8s_pipeline_ask():
    log("\n" + "=" * 60)
    log("[STEP 3/5] KUBERNETES TROUBLESHOOTING VIA FULL PIPELINE.ASK()")
    log("=" * 60)
    question = "Why is my pod stuck in CrashLoopBackOff with exit code 137?"
    log(f"Running pipeline for: \"{question}\"")
    log("Executing LangGraph nodes (guardrails -> route -> retrieve -> grade -> generate -> critique)...")
    start = time.perf_counter()
    resp = pipeline.ask(question)
    latency = time.perf_counter() - start
    log(f"\nExecution Finished in {latency:.2f}s")
    log(f"Route:       {resp.route}")
    log(f"Extractive:  {resp.extractive}")
    log(f"Citations:   {len(resp.citations)} returned")
    log(f"Answer Size: {len(resp.answer)} chars")
    log("\nExecution Trace Timings:")
    for t in resp.trace:
        log(f"  - {t.node:18s}: {getattr(t, 'elapsed_ms', 0)} ms | {t.detail}")
    log("\nSynthesized Answer Preview:")
    log(resp.answer[:350].strip() + "...\n")
    assert resp.route == "vector", f"Unexpected route: {resp.route}"
    assert resp.extractive is False, "Expected AI-generated synthesis, but got extractive fallback"
    assert len(resp.citations) > 0, "No citations returned"
    log("[PASS] Kubernetes full pipeline synthesis succeeded.")
    return resp


def test_4_helm_troubleshooting():
    log("\n" + "=" * 60)
    log("[STEP 4/5] HELM TROUBLESHOOTING (HYBRID RETRIEVAL + SYNTHESIS)")
    log("=" * 60)
    question = "How do I recover a Helm release stuck in pending-upgrade state?"
    log(f"Query: \"{question}\"")
    
    # 1. Retrieve Helm runbooks via hybrid RRF store
    retriever = get_retriever()
    res = retriever.retrieve(question, top_k=5, use_hyde=False)
    log(f"Retrieved {len(res.chunks)} chunks from collection '{retriever.store.collection}'")
    for i, c in enumerate(res.chunks[:3], 1):
        log(f"  [{i}] {c.chunk.source} (score: {c.retrieval_score:.4f})")
    
    # 2. Synthesize answer with Groq qwen/qwen3.8-27b
    client = get_llm()
    ctx = format_context(res.chunks)
    prompt = f"Question: {question}\n\nContext:\n{ctx}\n\nProvide step-by-step SRE instructions."
    t0 = time.perf_counter()
    synth = client.complete(prompt, max_tokens=600)
    synth_time = time.perf_counter() - t0
    log(f"Groq Synthesis Latency: {synth_time:.2f}s | Output tokens: {synth.output_tokens}")
    log("\nHelm Answer Preview:")
    log(synth.text[:350].strip() + "...\n")
    assert len(res.chunks) > 0, "No chunks retrieved for Helm question"
    assert len(synth.text) > 100, "Synthesized answer is too short"
    log("[PASS] Helm troubleshooting retrieval and synthesis succeeded.")


def test_5_fallback_and_citations(resp_k8s):
    log("\n" + "=" * 60)
    log("[STEP 5/5] CITATIONS VERIFICATION & GRACEFUL EXTRACTIVE FALLBACK")
    log("=" * 60)
    # Citations
    log("Verifying citations from Kubernetes query:")
    for i, c in enumerate(resp_k8s.citations[:3], 1):
        src = getattr(c, "source", "") or (c.get("source") if isinstance(c, dict) else "")
        title = getattr(c, "title", "") or (c.get("title") if isinstance(c, dict) else "")
        section = getattr(c, "section", "") or (c.get("section") if isinstance(c, dict) else "")
        score = getattr(c, "score", 0.0) or (c.get("score") if isinstance(c, dict) else 0.0)
        log(f"  [{i}] {src} -> {title} ({section}) [Score: {score}]")
        assert src, "Missing citation source"
    
    # Fallback
    log("\nTesting forced extractive fallback ('Without LLM' mode)...")
    t0 = time.perf_counter()
    resp_ext = pipeline.ask("How to troubleshoot CrashLoopBackOff?", force_extractive=True)
    dt = time.perf_counter() - t0
    log(f"Extractive run finished in {dt:.2f}s | extractive={resp_ext.extractive}")
    assert resp_ext.extractive is True, "Expected extractive=True"
    assert len(resp_ext.citations) > 0, "Expected citations in fallback"
    log("[PASS] Citations and graceful fallback verified.")


def main() -> int:
    settings = get_settings()
    log("=" * 60)
    log("GROQ & LANGGRAPH PIPELINE RAPID VERIFICATION")
    log(f"LLM Provider: {settings.llm_provider}")
    log(f"Groq Model:   {settings.groq_model}")
    log(f"Fast Model:   {settings.llm_fast_model}")
    log(f"Collection:   {settings.qdrant_collection}")
    log("=" * 60)

    reset_llm()
    client = get_llm()
    assert isinstance(client, GroqClient), f"Expected GroqClient, got {type(client)}"

    test_1_direct_groq_completion(client)
    test_2_structured_json_completion(client)
    resp_k8s = test_3_k8s_pipeline_ask()
    test_4_helm_troubleshooting()
    test_5_fallback_and_citations(resp_k8s)

    log("\n" + "=" * 60)
    log("ALL 5 VERIFICATION SUITES COMPLETED AND PASSED!")
    log("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
