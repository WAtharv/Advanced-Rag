"""This file creates a BM25-based sparse encoder without downloading any model from Hugging Face.

Text
 ↓
Tokens
 ↓
BM25 Sparse Vector
 ↓
Qdrant Sparse Search

Used for keyword/lexical retrieval in Hybrid RAG.

## Pure-Python BM25 sparse encoder - no model download.

fastembed's `Qdrant/bm25` is not a neural model; it is a tokeniser, a stopword
list, a stemmer and a hash. It still fetches those assets from Hugging Face,
which fails on a network that blocks it. This module reimplements the same idea
locally so sparse retrieval works with nothing downloaded.

The scoring split matches how Qdrant expects BM25 to be expressed as a sparse
vector: the document vector carries the term-frequency component, and Qdrant's
IDF modifier on the collection supplies the corpus half at query time.

    document value = tf * (k1 + 1) / (tf + k1 * (1 - b + b * len / avg_len))
    query value    = 1.0"""

from __future__ import annotations

import re
"""
BM25 Parameters
k1: Controls term frequency importance. (Many repetitions give diminishing returns.)
B: Length normalization. (Prevents large documents from always scoring higher.)
AVG_LEN: Assumed average document length. (Used in BM25 formula.)
"""

#: BM25 term-frequency saturation. 1.2 is the standard default.
K1 = 1.2
#: BM25 length normalisation strength.
B = 0.75
#: Assumed average document length in tokens. fastembed uses a fixed 256 rather
#: than a corpus statistic so that encoding stays streaming and stateless; the
#: same choice keeps document and query encoders independent here.
AVG_LEN = 256

_TOKEN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")            # Tokenization Pattern : Extracts words such as (kubernetes, imagefs.available, pod-123, v1_29)

#: Deliberately small. Aggressive stopword removal hurts on operational text,
#: where "no", "not" and "off" carry real meaning ("node not ready").
STOPWORDS = frozenset(                                          # STOPWORDS : Common words removed, But important operational words like (not, off) are intentionaly kept
    """
    a an the and or but if then than that this these those there here
    is are was were be been being am do does did doing done
    have has had having i you he she it we they me him her them us
    my your his its our their of in on at to for from by with as
    about into over under again further once during
    """.split()
)


def _load_stemmer():                                            # running → run & changing → chang
    """Porter2 via snowballstemmer, with a hand-rolled fallback.

    snowballstemmer is pure Python from PyPI - no model download - so it works on
    a network that blocks Hugging Face. The fallback exists so this module never
    hard-depends on it; it is noticeably worse (it misses "change"/"changing").
    """
    try:
        import snowballstemmer

        return snowballstemmer.stemmer("english").stemWord
    except ImportError:  # pragma: no cover - exercised only without the package
        return _fallback_stem


def _fallback_stem(token: str) -> str:
    """
    Simple custom stemmer.
     - No external dependency
    """
    
    if len(token) <= 3 or not token.isalpha():
        return token
    for suffix, keep in (("ingly", 5), ("edly", 4), ("ing", 3), ("ies", 3), ("ed", 2), ("es", 2)):
        if token.endswith(suffix) and len(token) - keep >= 4:
            base = token[: len(token) - keep]
            if suffix == "ies":
                return base + "y"
            # Undo the doubled consonant in "stopping" -> "stopp" -> "stop".
            if len(base) > 3 and base[-1] == base[-2] and base[-1] not in "sl":
                base = base[:-1]
            return base
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


_STEM = _load_stemmer()


def stem(token: str) -> str:                         # Converts word to root form.
    """Reduce a token to its stem.

    Applied identically on both the document and query side - consistency is what
    makes retrieval work, so a linguistically odd stem is harmless as long as it
    is the same on both sides. Non-alphabetic tokens (`137`, `1.29.6`) and
    compound identifiers are passed through untouched.
    """
    if len(token) <= 3 or not token.isalpha():
        return token
    return _STEM(token)


def tokenize(text: str) -> list[str]:                  # Convert to lowercase -> Extract tokens -> Remove stopwords -> Stem tokens -> Split compound identifiers 
    """Lowercase, split on non-alphanumerics, drop stopwords, stem.

    Dotted and hyphenated identifiers are kept whole *and* split, so a query for
    `imagefs.available` matches a document that wrote `imagefs available`.
    """
    tokens: list[str] = []
    for match in _TOKEN.finditer(text.lower()):
        raw = match.group(0)
        if raw in STOPWORDS:
            continue
        tokens.append(stem(raw))
        if any(sep in raw for sep in "._-"):
            for part in re.split(r"[._-]", raw):
                if len(part) > 1 and part not in STOPWORDS:
                    tokens.append(stem(part))
    return tokens


def token_id(token: str) -> int:                        # Converts token into a deterministic integer.
    """Stable 31-bit index for a token.

    Python's `hash()` is salted per process, which would silently break every
    stored vector on restart - so this uses an explicit FNV-1a instead.
    """
    value = 0x811C9DC5
    for byte in token.encode("utf-8"):
        value = ((value ^ byte) * 0x01000193) & 0xFFFFFFFF
    return value & 0x7FFFFFFF


def encode_document(text: str) -> tuple[list[int], list[float]]:            # Creates sparse BM25 vector for documents.
    """BM25 term-frequency weights for one document."""
    tokens = tokenize(text)                                                 # Tokenize
    if not tokens:
        return [], []

    counts: dict[int, int] = {}
    for token in tokens:
        index = token_id(token)
        counts[index] = counts.get(index, 0) + 1                            # Count Frequency

    length_norm = K1 * (1 - B + B * len(tokens) / AVG_LEN)
    indices, values = [], []
    for index, tf in sorted(counts.items()):
        indices.append(index)
        values.append(tf * (K1 + 1) / (tf + length_norm))                    # BM25 Formula -> Calculates weights -> Output (This becomes the Qdrant sparse vector)
    return indices, values


def encode_query(text: str) -> tuple[list[int], list[float]]:               # Creates sparse query vector -> why ? -> Because Qudrant adds -> IDF weighting -> during search
    """Query side: presence only. Qdrant's IDF modifier supplies the weighting."""
    unique = sorted({token_id(token) for token in tokenize(text)})
    return unique, [1.0] * len(unique)


def lexical_overlap(query: str, document: str) -> float:                    # Simple overlap score -> QUERY -> DOCUMENT -> Matched (2/3) -> result 0.67 
                                                                            # Purpose : quick sanity check, Not ideal for large condidate sets
    """Fraction of the query's distinct terms that appear in the document.

    Unweighted, so on a short query it can only take a handful of values (k/n for
    n query terms) - which makes it useless for *ranking*, because everything
    ties. Kept for the single-pair sanity check; use `weighted_coverage` to rank.
    """
    query_terms = set(tokenize(query))
    if not query_terms:
        return 0.0
    document_terms = set(tokenize(document))
    return len(query_terms & document_terms) / len(query_terms)


def weighted_coverage(query: str, documents: list[str]) -> list[float]:     # More advanced reranking method -> QUERY -> CANDIDATE DOC'S 
                                                                            # 1. Calculate rarity.
                                                                            # 2. Give larger weight to rare term.
                                                                            # 3. Score documents 
                                                                            #    get's much higher score 
                                                                            
                                                                            # why ?
                                                                            # Matching:
                                                                            #       Plain Text : OOMKilled
                                                                            #   is much more important than matching:
                                                                            #       Plain Text : pod 
                                                                            #   because "pod" appears almost everywhere.
                                                                            
    """Rank documents by IDF-weighted query-term coverage, scored 0..1.

    Plain coverage quantises into k/n buckets, so on a 7-term query five
    candidates routinely land on the identical score and the reranking does
    nothing. Weighting each matched term by its rarity *within the candidate set*
    breaks those ties in the right direction: matching "oomkilled" (in one
    passage) counts for far more than matching "pod" (in all of them).

    IDF is computed from the candidates rather than the whole corpus on purpose -
    reranking only has to order this shortlist, and the candidate set is exactly
    the population the distinction matters over.
    """
    import math

    query_terms = set(tokenize(query))
    if not query_terms or not documents:
        return [0.0] * len(documents)

    doc_terms = [set(tokenize(text)) for text in documents]
    total = len(documents)

    weights: dict[str, float] = {}
    for term in query_terms:
        containing = sum(1 for terms in doc_terms if term in terms)
        # Smoothed IDF; a term in every candidate gets a small but non-zero weight
        # so a document matching it still beats one matching nothing.
        weights[term] = math.log(1 + (total - containing + 0.5) / (containing + 0.5))

    denominator = sum(weights.values())
    if denominator <= 0:
        return [0.0] * len(documents)

    return [
        sum(weights[term] for term in query_terms & terms) / denominator for terms in doc_terms
    ]

# Overall Pipeline
"""
Document
   ↓
tokenize()
   ↓
stem()
   ↓
token_id()              # converts token into deterministic integers
   ↓
encode_document()       # create sparse BM25 vector 
   ↓
Sparse BM25 Vector
   ↓
Stored in Qdrant
"""

# During Search
"""
User Query
     ↓
tokenize()
     ↓
encode_query()
     ↓
Qdrant Sparse Search
     ↓  
Matching Documents
     ↓
weighted_coverage()     # Advance re-ranking method 
     ↓
Better Ranking
"""

"""
This module implements a pure Python BM25 sparse encoder for lexical retrieval without requiring Hugging Face downloads. 
It tokenizes text, removes stopwords, applies stemming, generates deterministic token IDs using FNV-1a hashing, and 
computes BM25 term-frequency weights for documents. Queries are encoded as sparse vectors with uniform weights, 
while Qdrant applies IDF during retrieval. The module also provides lexical overlap and weighted coverage scoring for 
reranking retrieved candidates based on rare and important query terms.
"""