"""End-to-end smoke test: one question through the real pipeline.

Costs a handful of model calls. Run it after `rag-ingest` to confirm the whole
stack is wired up before pointing the UI at it.

    python scripts/smoke.py
    python scripts/smoke.py --question "how many sev1 incidents since June 2026?" --approve
"""

from __future__ import annotations

import argparse
import sys

from advanced_rag.config import get_settings
from advanced_rag.graph import pipeline
from advanced_rag.models import AnswerResponse
from advanced_rag.observability import setup_logging

DEFAULT_QUESTION = "A pod keeps restarting with exit code 137. What happened and how do I fix it?"


def show(response: AnswerResponse) -> None:
    print("\n" + "=" * 78)
    print(f"route={response.route.value}  latency={response.latency_ms}ms  "
          f"tokens={response.input_tokens}->{response.output_tokens}  "
          f"cache={response.cache_kind}")
    print("=" * 78)
    print(response.answer)

    if response.citations:
        print("\nSources:")
        for index, citation in enumerate(response.citations, start=1):
            print(f"  [{index}] {citation.source} › {citation.section}  ({citation.score:.3f})")

    skipped = [o for o in response.guardrails if o.action == "skip"]
    ran = len(response.guardrails) - len(skipped)
    print(f"\nGuardrails ({ran} of {len(response.guardrails)} ran):")
    for outcome in response.guardrails:
        mark = {"allow": "ok", "redact": "redacted", "block": "BLOCKED", "skip": "SKIPPED"}[
            outcome.action
        ]
        print(f"  {outcome.layer:<20} {mark:<9} {outcome.detail}")
    if skipped:
        print(f"  !! failed open: {', '.join(o.layer for o in skipped)}")

    print("\nTrace:")
    for step in response.trace:
        print(f"  {step.node:<20} {step.elapsed_ms:>6} ms  {step.detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument(
        "--approve", action="store_true", help="auto-approve a SQL proposal if one appears"
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    setup_logging(settings.log_level)

    from advanced_rag.retrieval import get_store

    indexed = get_store().count()
    if indexed == 0:
        print("The vector store is empty. Run `rag-ingest` first.", file=sys.stderr)
        return 1
    print(f"{indexed} chunks indexed; asking: {args.question!r}")

    response = pipeline.ask(args.question)
    show(response)

    if response.awaiting_approval:
        print("\n--- awaiting SQL approval ---")
        print(response.sql.sql if response.sql else "(no query)")
        if not args.approve:
            print("\nRe-run with --approve to execute it.")
            return 0
        print("\napproving...")
        show(pipeline.resume(response.thread_id, approved=True))

    return 0


if __name__ == "__main__":
    sys.exit(main())
