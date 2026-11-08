"""Single representative pipeline verification for the optimized Groq configuration.

Measures:
1. Total latency
2. Number of Groq calls made
3. Token usage (input / output)
4. Retrieval latency
5. Generation latency
6. Guardrail behavior & outcomes
7. Citation correctness
8. Graceful fallback behavior
"""

from __future__ import annotations

import sys
import time

# Ensure immediate unbuffered console output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

from advanced_rag.config import get_settings
from advanced_rag.graph import pipeline
from advanced_rag.llm import get_llm
from advanced_rag.llm.client import reset_llm
from advanced_rag.llm.groq_client import GroqClient


def main() -> int:
    settings = get_settings()
    reset_llm()
    client = get_llm()
    assert isinstance(client, GroqClient), f"Expected GroqClient, got {type(client)}"

    # Instrument Groq client to count calls during execution
    groq_call_count = 0
    orig_generate = client._generate_with_retry

    def counting_generate(*args, **kwargs):
        nonlocal groq_call_count
        groq_call_count += 1
        return orig_generate(*args, **kwargs)

    client._generate_with_retry = counting_generate

    question = "Why is my pod stuck in CrashLoopBackOff with exit code 137?"
    print("=" * 65, flush=True)
    print("OPTIMIZED GROQ PIPELINE RUNTIME VERIFICATION", flush=True)
    print(f"Question:    {question}", flush=True)
    print(f"Provider:    {settings.llm_provider}", flush=True)
    print(f"Model:       {settings.groq_model}", flush=True)
    print(f"Collection:  {settings.qdrant_collection}", flush=True)
    print(f"Guardrails:  enable={settings.enable_guardrails}, llm_in={settings.enable_guardrails_llm_input}, llm_out={settings.enable_guardrails_llm_output}", flush=True)
    print(f"HyDE/SelfRAG: hyde={settings.enable_hyde}, self_rag={settings.enable_self_rag}, crag={settings.enable_crag}", flush=True)
    print("=" * 65, flush=True)

    # 1. Execute representative question
    t0 = time.perf_counter()
    resp = pipeline.ask(question)
    total_latency = time.perf_counter() - t0

    # 2. Extract step timings
    retrieval_ms = 0.0
    generation_ms = 0.0
    for step in resp.trace:
        if step.node == "retrieve":
            retrieval_ms = float(getattr(step, "elapsed_ms", 0))
        elif step.node == "generate":
            generation_ms = float(getattr(step, "elapsed_ms", 0))

    print("\n--- MEASURED PERFORMANCE METRICS ---", flush=True)
    print(f"Total Latency:        {total_latency:.2f} seconds", flush=True)
    print(f"Total Groq Calls:     {groq_call_count} API call(s)", flush=True)
    print(f"Tokens (In / Out):    {resp.input_tokens} in / {resp.output_tokens} out", flush=True)
    print(f"Retrieval Latency:    {retrieval_ms:.1f} ms", flush=True)
    print(f"Generation Latency:   {generation_ms:.1f} ms", flush=True)
    print(f"Route:                {resp.route}", flush=True)
    print(f"Extractive:           {resp.extractive}", flush=True)

    # 3. Guardrail behavior
    print("\n--- GUARDRAILS BEHAVIOR ---", flush=True)
    print(f"Total Guardrail Layers Evaluated: {len(resp.guardrails)}", flush=True)
    for g in resp.guardrails:
        print(f"  Layer [{g.layer:18s}]: action={g.action:6s} | passed={g.passed} | {g.detail}", flush=True)

    # Verify deterministic layers ran
    layers_evaluated = {g.layer for g in resp.guardrails}
    assert "shape" in layers_evaluated, "Shape guardrail missing"
    assert "pii_redaction" in layers_evaluated, "PII redaction guardrail missing"
    assert "secret_request" in layers_evaluated, "Secret request guardrail missing"
    assert "injection" in layers_evaluated, "Injection guardrail missing"
    assert "destructive_output" in layers_evaluated, "Destructive output guardrail missing"
    assert "secret_egress" in layers_evaluated, "Secret egress guardrail missing"

    # 4. Citations & Answer Correctness
    print("\n--- CITATION & ANSWER VALIDATION ---", flush=True)
    print(f"Citations Returned:   {len(resp.citations)}", flush=True)
    for i, c in enumerate(resp.citations[:3], 1):
        src = getattr(c, "source", "") or (c.get("source") if isinstance(c, dict) else "")
        title = getattr(c, "title", "") or (c.get("title") if isinstance(c, dict) else "")
        sec = getattr(c, "section", "") or (c.get("section") if isinstance(c, dict) else "")
        score = getattr(c, "score", 0.0) or (c.get("score") if isinstance(c, dict) else 0.0)
        print(f"  [{i}] {src} -> {title} ({sec}) [Score: {score:.4f}]", flush=True)

    print("\nAnswer Preview (first 300 chars):", flush=True)
    clean_preview = resp.answer[:300].replace("\n", " ").strip()
    print(f"\"{clean_preview}...\"", flush=True)

    assert resp.extractive is False, "Expected AI-synthesized answer"
    assert len(resp.citations) > 0, "Expected citations"
    assert "137" in resp.answer or "OOM" in resp.answer or "memory" in resp.answer.lower(), "Answer does not address 137"

    # 5. Graceful Fallback Test (force_extractive)
    print("\n--- GRACEFUL EXTRACTIVE FALLBACK TEST ---", flush=True)
    t_fb = time.perf_counter()
    fb_resp = pipeline.ask(question, force_extractive=True)
    dt_fb = time.perf_counter() - t_fb
    print(f"Fallback Latency:     {dt_fb:.2f} seconds", flush=True)
    print(f"Fallback Extractive:  {fb_resp.extractive}", flush=True)
    print(f"Fallback Citations:   {len(fb_resp.citations)}", flush=True)
    assert fb_resp.extractive is True, "Fallback expected extractive=True"
    assert len(fb_resp.citations) > 0, "Fallback expected citations"
    print("[PASS] Graceful fallback operating as designed.", flush=True)

    print("\n" + "=" * 65, flush=True)
    print("VERIFICATION COMPLETED SUCCESSFULLY!", flush=True)
    print("=" * 65, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

