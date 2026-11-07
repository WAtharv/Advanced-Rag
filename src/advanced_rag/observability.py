"""This file provides:
        Logging configuration
        Handled-error (degraded mode) logging
        Execution time tracking for pipeline nodes

## Logging setup and lightweight per-run timing."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager

from advanced_rag.models import TraceStep

_CONFIGURED = False


def setup_logging(level: str = "INFO") -> None:                                        # Configures logging only once
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),                           # First call
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    # These are chatty at INFO and drown out the pipeline's own logs.
    for noisy in ("httpx", "httpcore", "anthropic", "urllib3", "qdrant_client"):       # Reduce noisy library logs
        logging.getLogger(noisy).setLevel(logging.WARNING)                             # set to : WARNING
    _CONFIGURED = True


_DEGRADED_SEEN: set[str] = set()


def log_degraded(logger: logging.Logger, key: str, message: str, exc: BaseException) -> None:       # Degraded Mode Logging; Used when a feature fails but the system can continue working.
    """Report a handled degradation: in full once, then one line per occurrence.                    # Ex: DE retriever fails; 
                                                                                                    # Instead of:Error -> Stop application; 
    `logger.exception` on an expected fallback is a real problem, not just noise.                   # System fallback: Continue with normal retrieval
    HyDE failing on 15 eval cases emitted 15 identical 40-line tracebacks and               
    buried the results table completely. The first occurrence still carries the                     # First Failure: log_degraded(...)
    stack trace, because that is what you need to diagnose an unexpected cause.
    """
    summary = f"{message}: {type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
    if key in _DEGRADED_SEEN:                                                                       # Repeated Failure: Prevents hundreds of duplicate stack traces.
        logger.warning("%s (repeat)", summary)
        return
    _DEGRADED_SEEN.add(key)
    logger.warning(summary, exc_info=logger.isEnabledFor(logging.DEBUG))
    logger.debug("Full traceback for %s", key, exc_info=exc)


def reset_degraded_log() -> None:                                                                   # Reset Degraded Logs: Used mainly in testing; Forgets all previously seen failures.
    """Test hook - forget which degradations have already been reported."""
    _DEGRADED_SEEN.clear()


@contextmanager
def timed(trace: list[TraceStep], node: str, detail: str = ""):                                     # Timing Context Manager: trace, node 
    """Append a TraceStep for a graph node, recording its wall time.                                # Measures how long a pipeline step takes.

    The step is written on the way out even if the node raises, so a failed run
    still shows how far the pipeline got.
    """                                                                                             # Enter Block: with timed(trace, "retrieve"):
    start = time.perf_counter()                                                                     # Starts timer 
    step = TraceStep(node=node, detail=detail)                                                      # node="retrieve"
    try:                                                                                            # Execute Node
        yield step
    finally:                                                                                        # Even If Error Occurs; Beucase of finally: The timing is still recorded; Helps identify where pipeline failed.
        step.elapsed_ms = int((time.perf_counter() - start) * 1000)                                 # Exit Block; 
        trace.append(step)                                                                          # Append 


"""
Application Starts
        │
        ▼
setup_logging()
        │
        ▼
Run Graph Node
        │
        ▼
with timed(trace,"retrieve")
        │
        ▼
Measure Execution Time
        │
        ▼
Node Success/Failure
        │
        ▼
Store TraceStep
        │
        ▼
Continue Pipeline

"This module configures application logging, prevents repetitive degradation errors from flooding logs, 
and measures execution time of each pipeline node by recording TraceStep objects even when a node fails."
"""