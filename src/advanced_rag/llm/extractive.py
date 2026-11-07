"""Answer composition with no language model.

Retrieval is fully offline already - the keyword BM25 backend needs no download
and no API key - so the pipeline can still do the useful half of its job without
credentials: find the right passages and cite them. What it cannot do is
synthesise prose, and pretending otherwise would be the one unacceptable
outcome. So this module quotes, and says that it is quoting.

The output is markdown because that is what the UI and the API contract already
carry; nothing downstream needs to know an answer came from here.
"""

from __future__ import annotations

from advanced_rag.models import RetrievedChunk, Verdict

#: Chunks are ~400 chars at the median, but a policy document section can run
#: long, and an answer that is one wall of text is not readable.
MAX_PASSAGE_CHARS = 1_200

#: More than this and the reader is scrolling rather than triaging.
MAX_PASSAGES = 4

PREAMBLE = (
    "**No language model is configured, so this answer is extractive.** The text "
    "below is quoted verbatim from the indexed runbooks - it has not been "
    "summarised, reconciled or checked for relevance by a model. Read the "
    "passages and judge them yourself."
)

NOTHING_FOUND = (
    "**No language model is configured, so this answer is extractive** - and "
    "retrieval returned no passages for this question.\n\n"
    "Nothing in the indexed corpus matched. Either the question is outside what "
    "these runbooks cover, or the wording shares too little with them: without a "
    "model there is no query rewriting or HyDE to bridge that gap, so phrasing "
    "matters more than it otherwise would. Try the vocabulary the runbooks use "
    "(component names, exit codes, event reasons)."
)


def _quote(text: str) -> str:
    """Render a passage as a markdown blockquote, truncated if it is very long."""
    body = " ".join(text.split())
    if len(body) > MAX_PASSAGE_CHARS:
        # Cut on a word boundary so the ellipsis does not land mid-token.
        body = body[:MAX_PASSAGE_CHARS].rsplit(" ", 1)[0] + " […]"
    return "> " + body


def extractive_answer(
    chunks: list[RetrievedChunk],
    *,
    verdict: Verdict | None = None,
    sql: str = "",
    sql_rows_text: str = "",
) -> str:
    """Compose a cited, quoted answer from retrieved chunks alone.

    `verdict` is threaded through so that a CRAG floor rejection is still
    reported: the passages are shown either way, but the reader is told they
    scored badly rather than being left to assume they are on-topic.
    """
    if not chunks:
        return NOTHING_FOUND

    parts = [PREAMBLE]

    if verdict is Verdict.INCORRECT:
        parts.append(
            "Retrieval scored these passages as a **weak match** for the question. "
            "They are shown so you can judge, but treat them as a starting point "
            "for a manual search rather than an answer."
        )

    for index, chunk in enumerate(chunks[:MAX_PASSAGES], start=1):
        where = chunk.chunk.title or chunk.chunk.source
        heading = f"{where} › {chunk.chunk.section}" if chunk.chunk.section else where
        parts.append(f"**[{index}] {heading}**\n\n{_quote(chunk.chunk.text)}")

    if len(chunks) > MAX_PASSAGES:
        parts.append(
            f"*{len(chunks) - MAX_PASSAGES} further passage(s) matched and are "
            "listed under Sources.*"
        )

    if sql_rows_text:
        # Unreachable on the default offline path (the router cannot classify a
        # SQL question without a model), but the node passes it through when a
        # query did run, and silently dropping executed rows would be worse.
        parts.append("**Query executed against the operations database**")
        if sql:
            parts.append("```sql\n" + sql.strip() + "\n```")
        parts.append("```\n" + sql_rows_text.strip() + "\n```")

    return "\n\n".join(parts)
