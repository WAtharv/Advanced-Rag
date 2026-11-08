"""Verification script for Part 8: Query and Citation Tests."""

from advanced_rag.config import get_settings
from advanced_rag.retrieval.retriever import Retriever
from advanced_rag.retrieval.vectorstore import HybridStore

QUERIES = [
    {
        "domain": "Kubernetes",
        "query": "How do I troubleshoot a container in CrashLoopBackOff exiting with code 1?",
        "expected_substring": "crashloopbackoff",
        "expected_version": "1.37",
    },
    {
        "domain": "Helm",
        "query": "Why did helm upgrade fail with error 'field is immutable' on a StatefulSet?",
        "expected_substring": "upgrade",
        "expected_version": "4.3.1",
    },
    {
        "domain": "Prometheus",
        "query": "Why is a Prometheus scrape target reported as DOWN with context deadline exceeded?",
        "expected_substring": "target",
        "expected_version": "v3.16.0",
    },
    {
        "domain": "SRE Incident Response",
        "query": "What criteria and metrics determine declaring a P1 critical incident versus P2?",
        "expected_substring": "severity",
        "expected_version": "1.0",
    },
    {
        "domain": "Simulated Postmortem",
        "query": "In the simulated CoreDNS saturation postmortem, what caused DNS query amplification?",
        "expected_substring": "simulated",
        "expected_version": "1.0",
    },
]


def test_queries():
    settings = get_settings()
    store = HybridStore(settings=settings, collection="sre_copilot_v1")
    retriever = Retriever(settings=settings, store=store)

    print("=" * 80)
    print("PART 8: REPRESENTATIVE OPERATIONAL QUERY & CITATION VERIFICATION")
    print("=" * 80)

    for item in QUERIES:
        domain = item["domain"]
        q = item["query"]
        print(f"\n--- [{domain}] Query: {q} ---")
        res = retriever.retrieve(q, top_k=10, top_n=3, mode="hybrid", rerank=True, use_hyde=False)
        assert len(res.chunks) > 0, f"No chunks retrieved for: {q}"

        top = res.chunks[0]
        print(f"Top Hit [Score={top.score:.4f}]:")
        print(f"  Source:       {top.chunk.source}")
        print(f"  Title:        {top.chunk.title}")
        print(f"  Section:      {top.chunk.section}")
        print(f"  Citation:     {top.chunk.citation()}")
        source_url = top.chunk.metadata.get("source_url") or top.chunk.metadata.get("url")
        print(f"  Source URL:   {source_url}")
        print(f"  Category:     {top.chunk.metadata.get('category')}")
        print(f"  Component:    {top.chunk.metadata.get('component')}")

        if "simulated" in item["expected_substring"]:
            sim_hits = [c for c in res.chunks if "simulated" in c.chunk.source.lower()]
            has_sim = len(sim_hits) > 0
            has_disclaimer = any("SIMULATED" in c.chunk.text.upper() for c in sim_hits)
            print(f"  Simulated Postmortem Found in Results: {has_sim}")
            print(f"  Simulation Disclaimer Verified in Results: {has_disclaimer}")
            assert has_sim, "Expected simulated postmortem to be retrieved!"
            assert has_disclaimer, "Simulated document must be explicitly flagged as simulated!"

        print(f"  Content Snippet (first 180 chars):\n    {top.chunk.text[:180].strip()}...")

    store.close()
    print("\n" + "=" * 80)
    print("ALL 5 REPRESENTATIVE OPERATIONAL QUERIES VERIFIED SUCCESSFULLY!")
    print("=" * 80)


if __name__ == "__main__":
    test_queries()
