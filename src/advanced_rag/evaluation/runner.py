"""`python -m advanced_rag.evaluation.runner` - evaluate retrieval and answers.

Three modes, cheapest first:

    --retrieval   deterministic retrieval metrics across strategies (no LLM calls
                  except HyDE, so it is the loop to iterate in)
    --guardrails  block/allow accuracy on the guardrail cases
    --answers     end-to-end answers, scored on fact coverage and (with
                  `pip install -e ".[eval]"`) Ragas faithfulness

Results are written to eval_results/ as JSON so runs can be diffed.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime

from advanced_rag.config import PROJECT_ROOT, get_settings
from advanced_rag.evaluation import dataset
from advanced_rag.evaluation.retrieval_metrics import fact_coverage, score_all
from advanced_rag.observability import setup_logging

logger = logging.getLogger(__name__)

RESULTS_DIR = PROJECT_ROOT / "eval_results"

#: (label, retrieve kwargs) - the progression the course builds up to.
STRATEGIES: list[tuple[str, dict]] = [
    ("dense only", {"mode": "dense", "rerank": False, "use_hyde": False}),
    ("sparse only (BM25)", {"mode": "sparse", "rerank": False, "use_hyde": False}),
    ("hybrid (RRF)", {"mode": "hybrid", "fusion": "rrf", "rerank": False, "use_hyde": False}),
    ("hybrid (weighted)", {"mode": "hybrid", "rerank": False, "use_hyde": False}),
    ("hybrid + rerank", {"mode": "hybrid", "rerank": True, "use_hyde": False}),
    ("hybrid + rerank + HyDE", {"mode": "hybrid", "rerank": True, "use_hyde": True}),
]


def evaluate_retrieval(top_n: int = 5) -> list[dict]:
    from advanced_rag.retrieval import get_retriever

    retriever = get_retriever()
    base = [c for c in dataset.RETRIEVAL_AND_ANSWER if c.expected_sources]
    hard = [c for c in dataset.HARD_RETRIEVAL if c.expected_sources]
    cases = base + hard
    hard_start = len(base)  # everything at this index or beyond is a hard case
    rows: list[dict] = []

    for label, kwargs in STRATEGIES:
        started = time.perf_counter()
        pairs = []
        hard_pairs = []
        hyde_produced = 0
        cross_encoded = 0
        for index, case in enumerate(cases):
            result = retriever.retrieve(case.question, top_n=top_n, **kwargs)
            pair = (result.chunks, case.expected_sources)
            pairs.append(pair)
            if index >= hard_start:
                # The paraphrase-only cases: this is where BM25 and dense diverge.
                hard_pairs.append(pair)
            if result.hyde_document:
                hyde_produced += 1
            if result.cross_encoder:
                cross_encoded += 1

        row = score_all(pairs, k=top_n).as_row(label)
        hard_scores = score_all(hard_pairs, k=top_n)
        row["hard_hit@k"] = round(hard_scores.hit_rate, 3)
        row["hard_recall"] = round(hard_scores.recall, 3)
        row["hard_mrr"] = round(hard_scores.mrr, 3)
        row["hard_cases"] = hard_scores.cases
        row["seconds"] = round(time.perf_counter() - started, 1)

        # Record what actually ran. A strategy whose distinguishing feature was
        # unavailable is a duplicate of the row above it, and saying so is the
        # difference between an honest table and a misleading one.
        notes = []
        if kwargs.get("use_hyde") and hyde_produced == 0:
            notes.append("HyDE produced nothing (LLM unavailable) - same as no HyDE")
        if kwargs.get("rerank") and cross_encoded == 0:
            notes.append("lexical reranker, not a cross-encoder")
        row["notes"] = "; ".join(notes)
        rows.append(row)
        print(_format_row(row))
        if row["notes"]:
            print(f"      ^ {row['notes']}")
    return rows


def evaluate_negatives(top_n: int = 5) -> dict:
    """Out-of-corpus questions: a good retriever should signal 'nothing relevant'.

    Measured as the top *authoritative* rerank score falling below the CRAG floor
    - i.e. the pipeline would decline rather than answer from an unrelated runbook.
    Only meaningful with an authoritative reranker (the gemini backend); with the
    lexical stand-in it is reported as not meaningful.
    """
    from advanced_rag.retrieval import get_retriever

    settings = get_settings()
    retriever = get_retriever()
    floor = settings.crag_relevance_floor
    cases = dataset.NEGATIVE_RETRIEVAL
    rows: list[dict] = []
    abstained = 0
    authoritative = True

    for case in cases:
        result = retriever.retrieve(case.question, use_hyde=False)
        authoritative = result.cross_encoder
        top = max((c.rerank_score or 0.0 for c in result.chunks), default=0.0)
        below = top < floor
        abstained += 1 if below else 0
        rows.append(
            {"question": case.question, "top_rerank_score": round(top, 3), "below_floor": below}
        )
        print(
            f"  {'ABSTAIN ' if below else 'RETRIEVE'} top={top:.3f} (floor {floor}) "
            f"{case.question[:55]}"
        )

    total = len(cases)
    rate = round(abstained / total, 3) if total else 0.0
    if not authoritative:
        print("  NOTE: reranker is not authoritative here - abstention is not meaningful")
    else:
        print(f"\n  abstained on {abstained}/{total} ({rate:.0%}) out-of-corpus questions")
    return {
        "floor": floor,
        "authoritative_reranker": authoritative,
        "abstention_rate": rate,
        "abstained": abstained,
        "total": total,
        "cases": rows,
    }


def evaluate_guardrails() -> dict:
    from advanced_rag.guardrails import get_guardrails

    guardrails = get_guardrails()
    correct = 0
    failures: list[dict] = []
    for question, should_block in dataset.GUARDRAIL_CASES:
        result = guardrails.check_input(question)
        if result.blocked == should_block:
            correct += 1
        else:
            layer = next((o.layer for o in result.outcomes if o.action == "block"), "none")
            failures.append(
                {
                    "question": question,
                    "expected_block": should_block,
                    "actually_blocked": result.blocked,
                    "layer": layer,
                }
            )
        print(
            f"  {'OK ' if result.blocked == should_block else 'MISS'} "
            f"blocked={result.blocked!s:5} expected={should_block!s:5} {question[:60]}"
        )

    total = len(dataset.GUARDRAIL_CASES)
    print(f"\nguardrail accuracy: {correct}/{total} ({correct / total:.0%})")
    return {"accuracy": correct / total, "correct": correct, "total": total, "failures": failures}


def evaluate_answers(limit: int | None = None) -> dict:
    from advanced_rag.graph import pipeline

    cases = dataset.RETRIEVAL_AND_ANSWER[: limit or len(dataset.RETRIEVAL_AND_ANSWER)]
    records: list[dict] = []

    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {case.question[:70]}")
        response = pipeline.ask(case.question)
        coverage = fact_coverage(response.answer, case.expected_facts)
        cited = {c.source for c in response.citations}
        records.append(
            {
                "question": case.question,
                "answer": response.answer,
                "reference": case.reference,
                "contexts": [c.source for c in response.citations],
                "fact_coverage": round(coverage, 3),
                "cited_expected_source": bool(cited & set(case.expected_sources)),
                "route": response.route.value,
                "blocked": response.blocked,
                "latency_ms": response.latency_ms,
                "tokens": response.input_tokens + response.output_tokens,
            }
        )
        print(f"      coverage={coverage:.2f} latency={response.latency_ms}ms")

    scored = [r for r in records if not r["blocked"]]
    summary = {
        "cases": len(records),
        "mean_fact_coverage": round(
            sum(r["fact_coverage"] for r in scored) / max(len(scored), 1), 3
        ),
        "cited_expected_rate": round(
            sum(1 for r in scored if r["cited_expected_source"]) / max(len(scored), 1), 3
        ),
        "mean_latency_ms": int(sum(r["latency_ms"] for r in scored) / max(len(scored), 1)),
        "total_tokens": sum(r["tokens"] for r in records),
    }
    print("\n" + json.dumps(summary, indent=2))
    return {"summary": summary, "records": records}


def evaluate_ragas(records: list[dict]) -> dict | None:
    """Optional Ragas pass over the answers already generated."""
    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import answer_relevancy, faithfulness
    except ImportError:
        print('ragas not installed - run: pip install -e ".[eval]"')
        return None

    from advanced_rag.retrieval import get_retriever

    retriever = get_retriever()
    rows = {"question": [], "answer": [], "contexts": [], "ground_truth": []}
    for record in records:
        if record["blocked"]:
            continue
        chunks = retriever.retrieve(record["question"], use_hyde=False).chunks
        rows["question"].append(record["question"])
        rows["answer"].append(record["answer"])
        rows["contexts"].append([hit.chunk.text for hit in chunks])
        rows["ground_truth"].append(record["reference"])

    result = evaluate(Dataset.from_dict(rows), metrics=[faithfulness, answer_relevancy])
    print(result)
    return {k: float(v) for k, v in result.items() if isinstance(v, (int, float))}


def _format_row(row: dict) -> str:
    hard = ""
    if row.get("hard_cases"):
        # The headline metrics stay near 1.00 (the easy set saturates them); the
        # hard-subset columns are the ones that separate the strategies.
        hard = (
            f"  | hard(n={row['hard_cases']}): hit@k={row['hard_hit@k']:.2f} "
            f"recall={row['hard_recall']:.2f} mrr={row['hard_mrr']:.2f}"
        )
    return (
        f"  {row['strategy']:<26} hit@k={row['hit@k']:.2f} recall={row['recall']:.2f} "
        f"mrr={row['mrr']:.2f} ndcg={row['ndcg']:.2f} p@k={row['p@k']:.2f} "
        f"({row['seconds']}s){hard}"
    )


def warn_if_saturated(rows: list[dict]) -> None:
    """Say so when the metrics cannot tell the strategies apart.

    A table of 1.00s looks like success and is actually the eval set failing to
    discriminate - the single easiest way to draw a wrong conclusion here.
    """
    ranking_keys = ("hit@k", "recall", "mrr", "ndcg")
    if not rows:
        return
    saturated = [k for k in ranking_keys if all(row[k] >= 0.999 for row in rows)]
    identical = [
        k for k in ranking_keys if len({row[k] for row in rows}) == 1 and k not in saturated
    ]
    if saturated:
        print(
            "\n  NOTE: " + ", ".join(saturated) + " are pegged at 1.00 for every strategy.\n"
            "  The eval set is saturated - it cannot show which strategy is better.\n"
            "  Only p@k still discriminates. Add harder cases before drawing conclusions."
        )
    elif identical:
        print("\n  NOTE: " + ", ".join(identical) + " are identical across strategies.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rag-eval")
    parser.add_argument("--retrieval", action="store_true", help="retrieval strategy comparison")
    parser.add_argument("--guardrails", action="store_true", help="guardrail block/allow accuracy")
    parser.add_argument("--answers", action="store_true", help="end-to-end answer quality")
    parser.add_argument("--ragas", action="store_true", help="add Ragas metrics to --answers")
    parser.add_argument("--limit", type=int, default=None, help="cap the number of answer cases")
    parser.add_argument("--top-n", type=int, default=5)
    args = parser.parse_args(argv)

    if not (args.retrieval or args.guardrails or args.answers):
        args.retrieval = True  # cheapest useful default

    setup_logging(get_settings().log_level)
    # This runner is an entry point that makes HTTPS calls (Gemini) and may
    # download models (fastembed), so route TLS through the OS trust store like
    # the API and ingest CLI do - otherwise a corporate CA fails verification.
    from advanced_rag.certs import enable_system_trust_store

    enable_system_trust_store()
    report: dict = {"generated_at": datetime.now(UTC).isoformat()}

    if args.retrieval:
        print("\n== retrieval strategies ==")
        report["retrieval"] = evaluate_retrieval(top_n=args.top_n)
        warn_if_saturated(report["retrieval"])
        print("\n== out-of-corpus negatives (abstention) ==")
        report["negatives"] = evaluate_negatives(top_n=args.top_n)
    if args.guardrails:
        print("\n== guardrails ==")
        report["guardrails"] = evaluate_guardrails()
    if args.answers:
        print("\n== end-to-end answers ==")
        answers = evaluate_answers(limit=args.limit)
        report["answers"] = answers
        if args.ragas:
            report["ragas"] = evaluate_ragas(answers["records"])

    # Release the embedded Qdrant client explicitly; leaving it to interpreter
    # shutdown produces a confusing ImportError from its __del__.
    try:
        from advanced_rag.retrieval import get_store

        get_store().close()
    except Exception:  # nothing to close is fine
        pass

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = RESULTS_DIR / f"eval-{stamp}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {path.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
