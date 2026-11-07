# RAG Engine v2 — Architecture & Technical Workflow

## 1. Tech Stack

- **Language:** Python
- **API Framework:** FastAPI
- **Orchestration:** LangGraph
- **LLMs:** Gemini (chat and embeddings), Claude Haiku (fast LLM)
- **Embedding & Sparse Retrieval:** FastEmbed / ONNX, `BAAI/bge-small`, BM25
- **Vector Database:** Qdrant with hybrid search
- **Reranking:** FastEmbed `TextCrossEncoder` — `Xenova/ms-marco-MiniLM-L-6-v2` (local)
- **Fallback Rerankers:** Gemini-based LLM reranker, lexical IDF scorer
- **Evaluation:** RAGAS, Hit@K, Recall@K, MRR, NDCG@K, Precision@K

## 2. End-to-End Pipeline

### Step 1: Data Ingestion

**Components:** Document loader, chunker, `rag-ingest` CLI

1. Load documents from supported sources.
2. Clean noise and normalize document content.
3. Understand document structure and extract relevant text.
4. Split documents into manageable chunks.
5. Generate dense embeddings and prepare sparse BM25 representations.
6. Index the processed chunks in Qdrant for retrieval.

### Step 2: Application Startup

1. Load configuration and credentials from `.env`.
2. Initialize the FastAPI application.
3. Initialize models, database connections, caches, and retrieval components.
4. Compile the LangGraph workflow.

### Step 3: Request Entry — `/ask`

1. Receive the user's question through the `/ask` endpoint.
2. Validate and normalize the request.
3. Pass the request into the compiled LangGraph pipeline.

### Step 4: Input Guardrails

Run six input checks before processing the request:

- **Shape validation:** Check the structure and validity of the input.
- **PII redaction:** Detect and handle personally identifiable information.
- **Secret-request detection:** Identify requests to reveal credentials or sensitive information.
- **SQL injection detection:** Identify potentially malicious SQL input.
- **Intent classification:** Determine whether the request violates platform policy.
- **Scope validation:** Ensure the request falls within the system's supported scope.

**Blocked requests** short-circuit the workflow and return an error response without proceeding to retrieval or generation.

### Step 5: Cache Lookup

Check the cache before performing expensive retrieval or generation:

1. **Exact-match lookup:** Check whether the same question has already been answered.
2. **Semantic lookup:** Use embeddings to find a sufficiently similar cached question.
3. **Cache hit:** Return the cached answer when valid.
4. **Cache miss:** Continue to query routing and retrieval.

### Step 6: Query Routing

The router classifies the request into one of four paths:

| Route | Action |
|---|---|
| `vector` | Retrieve relevant information from the vector store. |
| `sql` | Query structured data through the SQL workflow. |
| `both` | Retrieve vector and SQL results, then merge the results. |
| `reject` | Reject unsupported or disallowed requests. |

### Step 7: Hybrid Retrieval

Retrieve relevant documents using dense and sparse search.

- **Dense retrieval:** Semantic similarity using embeddings.
- **Sparse retrieval:** Keyword-based matching using BM25.
- **Hybrid retrieval:** Combine dense and sparse results to improve retrieval coverage.

Supported retrieval configurations include:

- FastEmbed dense embeddings + BM25
- Gemini-based embeddings + BM25
- FastEmbed dense embeddings with ONNX execution + BM25

The retrieved candidates are passed to the reranking stage.

### Step 8: Cross-Encoder Reranking

**Primary reranker:** FastEmbed `TextCrossEncoder` with `Xenova/ms-marco-MiniLM-L-6-v2`.

1. Receive candidate documents from retrieval.
2. Score each question-document pair using the cross-encoder.
3. Sort the candidates by relevance score.
4. Pass the highest-ranked documents to the generation stage.

**Fallback strategies:**
- Gemini-based LLM reranker
- Lexical IDF-based scorer

### Step 9: Corrective RAG (CRAG)

When the retrieved information is insufficient or has low relevance:

1. Evaluate retrieval quality.
2. Rewrite or refine the original query.
3. Run retrieval again using the improved query.
4. Use the improved results to construct the context.

**Purpose:** Correct weak retrieval instead of blindly generating an answer from irrelevant documents.

### Step 10: SQL Execution with Human Approval

For SQL-based requests:

1. Prepare the SQL operation.
2. Trigger a LangGraph interrupt to pause execution.
3. Request human approval before proceeding.
4. Resume the workflow after approval.
5. Execute the approved query or operation.
6. Render database rows into a structured answer context.

The generated response uses the retrieved database results and other relevant context.

### Step 11: Answer Generation and Self-RAG

Generate an answer using the LLM and the available context.

**Self-RAG** enables the workflow to assess whether retrieval is needed, evaluate retrieved evidence, and revise the response when necessary.

The goal is to produce an answer that is relevant, supported by retrieved evidence, and aligned with the request.

### Step 12: Output Guardrails

Run three output checks before returning the response.

#### `destructive_output`
Detect potentially destructive commands such as `kubectl delete` and `helm uninstall`. Add a change-control notice where appropriate.

#### `secret_egress`
Detect and redact secrets exposed in generated text, such as credentials, API keys, and tokens.

#### `output_review`
Use an LLM-based review to assess whether the answer is grounded, safe, and free from unsupported claims.

### Step 13: Finalization and Response

1. Finalize the validated answer.
2. Store the answer in the cache when appropriate.
3. Return the response as JSON through the FastAPI endpoint.

### Step 14: Observability

Trace and log individual workflow nodes, capturing:

- Node execution and request flow
- Latency and processing time
- Token usage
- Guardrail verdicts
- Retrieval and reranking outcomes
- Cache hits and misses
- Errors and execution status

---

## 3. Caching Strategy

The caching layer supports two lookup methods:

- **Exact caching:** Reuse answers for identical questions.
- **Semantic caching:** Use embeddings to identify semantically similar questions.

Blocked requests short-circuit the workflow and return an error response. Cache entries should be validated against relevant context, permissions, and freshness requirements before reuse.

## 4. RAG Evaluation Metrics

| Metric | What It Measures |
|---|---|
| **Hit@K** | Whether at least one relevant document appears in the top K retrieved results. |
| **Recall@K** | The proportion of all relevant documents retrieved within the top K. |
| **MRR (Mean Reciprocal Rank)** | How highly the first relevant document is ranked. |
| **NDCG@K** | How well documents are ranked, accounting for their graded relevance. |
| **Precision@K** | The proportion of the top K results that are relevant. |

Evaluation also includes test cases covering retrieval quality, answer correctness, grounding, and safety behavior.

## 5. RAGAS Evaluation

RAGAS is used to evaluate retrieval-augmented generation quality.

- **Faithfulness:** Measures whether the generated answer is supported by the provided context, helping identify unsupported claims.
- **Context Relevance:** Assesses whether the retrieved context is relevant to the question.

These metrics complement retrieval metrics by evaluating the quality of the context and the generated answer.

## 6. Key Concepts

### Grounded Generation
An answer is grounded when its claims are supported by available evidence, such as retrieved documents or database results, rather than relying solely on the LLM's learned knowledge.

### Corrective RAG (CRAG)
Evaluates retrieval quality and attempts to improve weak results through query refinement or another retrieval pass.

### Self-RAG
Allows the system to assess retrieval needs, evaluate evidence, and revise generated responses to improve relevance and factual support.

### Cross-Encoder Reranking
A cross-encoder evaluates the question and each candidate document together, producing a relevance score used to reorder retrieval results.

### Human-in-the-Loop SQL
LangGraph interrupts the workflow to obtain human approval before executing an operation that requires authorization.

## 7. High-Level Workflow

```text
Document Sources
      |
      v
Loader -> Cleaning -> Chunking -> Embeddings
      |                         |
      |                         v
      |                    Qdrant Index
      |
      v
FastAPI Application <- .env Configuration
      |
      v
POST /ask
      |
      v
Input Guardrails (6 Checks)
      |
      +---- Blocked ----> Error Response
      |
      v
Exact / Semantic Cache
      |
      +---- Cache Hit ---> Cached Response
      |
      v
Query Router
      |
      +---- Vector -----> Hybrid Retrieval
      |
      +---- SQL --------> Approval Interrupt
      |                         |
      |                         v
      |                    SQL Execution
      |
      +---- Both -------> Vector + SQL Results
      |
      +---- Reject -----> Rejection Response
      |
      v
Cross-Encoder Reranking
      |
      v
CRAG: Evaluate and Refine Retrieval
      |
      v
Context Construction
      |
      v
LLM Generation + Self-RAG
      |
      v
Output Guardrails (3 Checks)
      |
      v
Finalize Answer
      |
      v
Cache Answer When Appropriate
      |
      v
JSON Response

Observability: Trace and Log Workflow Nodes
Evaluation: Retrieval Metrics + RAGAS
```

## 8. Project Highlights

- End-to-end document ingestion and retrieval pipeline.
- Hybrid dense and sparse retrieval with cross-encoder reranking.
- Corrective RAG and Self-RAG capabilities.
- Multi-path vector and SQL query routing.
- Human approval for SQL operations using LangGraph interrupts.
- Input and output guardrails for safer request handling.
- Exact and semantic response caching.
- Node-level observability and RAG evaluation using RAGAS and ranking metrics.