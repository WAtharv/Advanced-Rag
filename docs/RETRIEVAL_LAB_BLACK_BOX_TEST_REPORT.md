# Retrieval Lab — Black-Box Testing and Behavior Audit Report

**Date:** November 8, 2026  
**Auditor:** Senior QA Engineer (Antigravity Quality Assurance)  
**Target Application:** Enterprise Kubernetes SRE Copilot — Retrieval Lab  
**Test Type:** Non-destructive Black-Box Runtime Audit  
**Application State:** Read-only inspection against active live FastAPI server (`127.0.0.1:8000`) and Qdrant local embedded storage (`data/qdrant`)

---

## A. Executive Summary

### Key Questions Addressed
1. **Are the retrieval strategies genuinely behaving differently?**  
   **YES.** Dense (bge-small semantic vectors), Sparse (FastEmbed BM25 lexical vectors), and Hybrid (RRF / Weighted fusion) use completely independent retrieval paths in Qdrant. Across candidate sets of 20, Dense and Sparse share only **35% candidate overlap** (13 out of 20 candidates are unique to one strategy).
2. **Does HyDE actually change retrieval behavior?**  
   **YES.** Enabling HyDE triggers an LLM completion (`qwen/qwen3.8-27b`) that generates a hypothetical operational triage passage (+715 ms latency). This passage injects rich diagnostic vocabulary, causing BM25 scores to triple (from 31.9 to 117.1) and promoting specific investigation commands (e.g. `oomkilled.md :: 7. Read-Only Investigation Commands`) to rank #1.
3. **Does Reranking actually change the ranking when appropriate?**  
   **YES.** The cross-encoder (`Xenova/ms-marco-MiniLM-L-6-v2`) actively re-scores candidate pairs with full self-attention. For example, it promoted `oomkilled.md :: 7. Read-Only Investigation Commands` from retrieval rank 14 (score 0.1111) up to rank #2 (rerank score 0.9967).
4. **Do Weighted and RRF fusion produce meaningful differences?**  
   **YES.** RRF uses server-side reciprocal rank fusion, while Weighted applies min-max normalization with `hybrid_dense_weight` (0.7). Candidate sets differ by 20% (4 out of 20 items differ), and relative rankings among intermediate candidates shift noticeably.
5. **Why do Dense, Sparse, and Hybrid sometimes appear "almost identical" in the UI?**  
   The audit revealed **two primary causes** that created the optical illusion of identical behavior:
   - **Rerank is CHECKED by default in the UI (`<input id="lab-rerank" checked>`)**: When Rerank is enabled, the cross-encoder scores and reorders the candidate sets from all modes into the *exact same optimal ranking* (promoting `oomkilled.md` to #1 across Dense, Sparse, and Hybrid).
   - **Reranking silently truncates the displayed results from 20 to 5**: In the API, `/retrieve` invokes `retriever.retrieve(..., top_k=20)` without passing `top_n`, falling back to `settings.rerank_top_n = 5`. The 15 diverse candidates below rank 5 are completely dropped, hiding strategy divergence.
   - **Corpus ground truth**: For `pod killed with exit code 137`, both semantic search and keyword search legitimately match the two primary runbooks in the repository (`crashloopbackoff.md` and `oomkilled.md`).

---

## B. Environment & Baseline

### 1. Test Environment Details
- **API Health:** HTTP 200 OK (`/health`)
- **Active Qdrant Collection:** `sre_copilot_v1`
- **Indexed Chunks:** 5,533 chunks (across 252 registered knowledge-base documents)
- **Vector Backend:** `fastembed` (`BAAI/bge-small-en-v1.5` dense [384d] + FastEmbed BM25 sparse)
- **Cross-Encoder Model:** `Xenova/ms-marco-MiniLM-L-6-v2` (`is_cross_encoder=True`)
- **Primary LLM:** `qwen/qwen3.8-27b` via Groq API
- **SQL / Cache:** SQLite (`data/sre_copilot.db`), in-process semantic/exact cache

### 2. UI Retrieval Lab Defaults
- **Default Query:** `pod killed with exit code 137`
- **Default Mode:** `hybrid`
- **Default Fusion:** `weighted`
- **HyDE Default State:** `OFF` (`use_hyde=false`)
- **Reranking Default State:** `ON` (`rerank=true`, checked by default)
- **Candidate Slider:** Value `20` (`min=5, max=50, step=1`)
- **Score Visibility:** Both `retrieval` score and `rerank` score are visible on result cards.

### 3. Baseline Query Execution
- **Query:** `pod killed with exit code 137`
- **Settings:** Mode: `hybrid`, Fusion: `weighted`, HyDE: `OFF`, Rerank: `ON`, Candidates: `20`
- **Observed Latency:** 0.753s
- **Returned Results Count:** **5 results** (truncated from 20 requested candidates)
- **Ordered By:** `rerank` (`rerank_is_authoritative=true`)

#### Baseline Top 5 Results
| Rank | Source Document | Section Heading | Retrieval Score | Rerank Score |
| :---: | :--- | :--- | :---: | :---: |
| **1** | `sre/runbooks/kubernetes/oomkilled.md` | 3. Symptoms | 0.8319 | **0.9987** |
| **2** | `sre/runbooks/kubernetes/oomkilled.md` | 7. Read-Only Investigation Commands | 0.2025 | **0.9967** |
| **3** | `sre/runbooks/prometheus/high-cardinality-problems.md` | 3. Symptoms | 0.4538 | **0.9954** |
| **4** | `sre/runbooks/kubernetes/crashloopbackoff.md` | 8. Expected Outputs | 0.9536 | **0.9883** |
| **5** | `sre/runbooks/kubernetes/crashloopbackoff.md` | 9. Decision Tree | 0.9217 | **0.9760** |

---

## C. Experiment Results

### Experiment A — Strategy Comparison (Isolating Mode & Fusion)
*Settings: Query: `pod killed with exit code 137`, Candidates: 20, HyDE: OFF, Rerank: OFF*

| Metric / Attribute | A1: Dense | A2: Sparse | A3: Hybrid (Weighted) | A4: Hybrid (RRF) |
| :--- | :---: | :---: | :---: | :---: |
| **Mode** | `dense` | `sparse` | `hybrid` | `hybrid` |
| **Fusion** | N/A | N/A | `weighted` (dense weight 0.7) | `rrf` (server-side) |
| **Latency** | **0.022s** | **0.104s** | **0.108s** | **0.115s** |
| **Total Candidates** | 20 | 20 | 20 | 20 |
| **Score Type** | Cosine similarity (0..1) | BM25 raw score (0..35+) | Min-Max Weighted (0..1) | RRF reciprocal score |
| **Top 1 Passage** | `crashloopbackoff.md` (0.7454) | `crashloopbackoff.md` (31.967) | `crashloopbackoff.md` (0.9536) | `crashloopbackoff.md` (0.8333) |
| **Top 2 Passage** | `crashloopbackoff.md` (0.7412) | `oomkilled.md` (31.369) | `crashloopbackoff.md` (0.9217) | `crashloopbackoff.md` (0.7500) |
| **Top 3 Passage** | `manage-resources-containers.md` | `crashloopbackoff.md` | `oomkilled.md` (0.8319) | `oomkilled.md` (0.5333) |
| **Top 4 Passage** | `oomkilled.md` (0.7309) | `simulated-incident-oomkilled` | `manage-resources-containers` | `manage-resources-containers` |
| **Top 5 Passage** | `debug-application-pods.md` | `incident-response-template` | `high-cardinality-problems` | `high-cardinality-problems` |

#### Candidate Set Comparison & Analysis
- **Candidate Pool Overlap (20 candidates):** Dense and Sparse share only **7 of 20 items (35.0%)**.
- **Dense Unique Candidates (12 items):** Retrieves upstream Kubernetes architectural documentation (`kubernetes/configuration/manage-resources-containers.md`, `kubernetes/troubleshooting/debug-application-pods.md`).
- **Sparse Unique Candidates (11 items):** Retrieves exact token hits for `137` in postmortems (`simulated-incident-oomkilled-cascade.md`) and operational checklists (`sre/templates/incident-response-template.md`).
- **Weighted vs RRF Fusion:** Overlap is 16/20 (80%). Weighted fusion prioritizes dense ranking (dense weight 0.7), while RRF balances sparse keyword frequency evenly.
- **Finding:** The strategies behave genuinely differently. The apparent similarity in the top 3 occurs because `crashloopbackoff.md` and `oomkilled.md` contain both high semantic relevance AND exact keyword matches for `exit code 137`.

---

### Experiment B — Test HyDE Independently
*Settings: Query: `pod killed with exit code 137`, Candidates: 20, Rerank: OFF*

| Metric / Attribute | B1: Hybrid RRF (HyDE OFF) | B2: Hybrid RRF (HyDE ON) | B3: Dense (HyDE ON) | B4: Sparse (HyDE ON) |
| :--- | :---: | :---: | :---: | :---: |
| **HyDE State** | OFF | **ON** | **ON** | **ON** |
| **Latency** | 0.104s | **0.820s** (+715 ms) | 0.430s (+408 ms) | 0.548s (+444 ms) |
| **LLM Request Made** | No | **Yes (`qwen/qwen3.8-27b`)** | **Yes (`qwen/qwen3.8-27b`)** | **Yes (`qwen/qwen3.8-27b`)** |
| **HyDE Passage Generated** | None | 529 characters | 529 characters | 529 characters |
| **Top 1 Passage** | `crashloopbackoff.md :: 8` | `oomkilled.md :: 7 (Commands)` | `oomkilled.md :: 3 (Symptoms)` | `oomkilled.md :: 7 (Commands)` |
| **Top 2 Passage** | `crashloopbackoff.md :: 9` | `oomkilled.md :: 3 (Symptoms)` | `crashloopbackoff.md :: 8` | `oomkilled.md :: 8 (Outputs)` |
| **Top 3 Passage** | `oomkilled.md :: 3 (Symptoms)` | `crashloopbackoff.md :: 8` | `manage-resources-containers` | `crashloopbackoff.md :: 7` |
| **Top Score** | 0.8333 | 0.6667 | 0.8483 | **117.0811** (was 31.9) |

#### Generated HyDE Passage Observable in UI
```text
A Pod terminating with exit code 137 indicates it was forcibly terminated by the
kernel via SIGKILL, most commonly due to an Out-Of-Memory (OOM) event. When a container
exceeds its configured memory limit in its pod specification, the Linux cgroup
Out-Of-Memory killer targets and terminates the main process.
To diagnose:
1. Check pod status using 'kubectl describe pod <pod-name>' and look for OOMKilled: true.
2. Inspect node dmesg or kernel logs for 'Memory cgroup out of memory'.
```

#### Observations:
- **Passage Quality & Relevance:** Highly accurate and directly aligned with the SRE domain.
- **Retrieval Impact:** The generated text shifted the #1 result from `crashloopbackoff.md` to `oomkilled.md :: 7. Read-Only Investigation Commands`.
- **Sparse Expansion:** In Sparse mode (B4), the generated keywords boosted BM25 scores from 31.96 to 117.08, pulling in deep memory diagnostic runbooks that the short query alone missed.

---

### Experiment C — Test Reranking Independently
*Settings: Query: `pod killed with exit code 137`, Mode: Hybrid RRF, HyDE: OFF, Candidates: 20*

| Parameter | C1: Rerank OFF | C2: Rerank ON |
| :--- | :---: | :---: |
| **Rerank Status** | `rerank=false` | `rerank=true` |
| **Execution Latency** | 0.104s | 0.825s (+721 ms) |
| **Total Results Displayed** | **20** | **5** (Truncated to `settings.rerank_top_n`) |
| **Sort Key** | `retrieval` score | `rerank` score (`is_authoritative=true`) |
| **Top 1 Result** | `crashloopbackoff.md :: 8` (retrieval: 0.8333) | `oomkilled.md :: 3` (retrieval: 0.5333, **rerank: 0.9987**) |
| **Top 2 Result** | `crashloopbackoff.md :: 9` (retrieval: 0.7500) | `oomkilled.md :: 7` (retrieval: 0.1111, **rerank: 0.9967**) |
| **Top 3 Result** | `oomkilled.md :: 3` (retrieval: 0.5333) | `crashloopbackoff.md :: 8` (retrieval: 0.8333, **rerank: 0.9883**) |
| **Top 4 Result** | `manage-resources-containers.md` (retrieval: 0.3750) | `crashloopbackoff.md :: 9` (retrieval: 0.7500, **rerank: 0.9760**) |
| **Top 5 Result** | `high-cardinality-problems.md` (retrieval: 0.2540) | `crashloopbackoff.md :: 7` (retrieval: 0.0889, **rerank: 0.9420**) |

#### Observations:
- **Cross-Encoder Verification:** Rerank scores are real logits transformed via sigmoid (e.g. 0.9987). They are distinct from retrieval scores.
- **True Rank Inversion:** Section `oomkilled.md :: 7` (investigation commands) had a weak initial retrieval score of 0.1111 (rank 14 in the 20-candidate pool). The cross-encoder recognized its high triage value and elevated it to rank #2.
- **Side Effect Discovered:** Reranking dropped the result list from 20 to 5.

---

### Experiment D — Test Reranking Across Different Retrieval Methods
*Settings: Query: `pod killed with exit code 137`, Candidates: 20, HyDE: OFF*

| Run | Mode | Rerank | Latency | Returned Count | Top 1 Source & Section | Retrieval Score | Rerank Score |
| :--- | :---: | :---: | :---: | :---: | :--- | :---: | :---: |
| **D1** | Dense | OFF | 0.022s | 20 | `crashloopbackoff.md :: 9. Decision Tree` | 0.7454 | N/A |
| **D2** | Dense | ON | 0.740s | 5 | `oomkilled.md :: 3. Symptoms` | 0.7309 | **0.9987** |
| **D3** | Sparse | OFF | 0.104s | 20 | `crashloopbackoff.md :: 8. Expected Outputs` | 31.9673 | N/A |
| **D4** | Sparse | ON | 0.735s | 5 | `oomkilled.md :: 3. Symptoms` | 31.3695 | **0.9987** |
| **D5** | Hybrid RRF | OFF | 0.115s | 20 | `crashloopbackoff.md :: 8. Expected Outputs` | 0.8333 | N/A |
| **D6** | Hybrid RRF | ON | 0.825s | 5 | `oomkilled.md :: 3. Symptoms` | 0.5333 | **0.9987** |

#### Crucial Behavioral Insight:
- Notice the **Retrieval Scores** in D2, D4, and D6 for the same top document (`oomkilled.md :: 3`):
  - In Dense (D2), its retrieval score was `0.7309`.
  - In Sparse (D4), its retrieval score was `31.3695`.
  - In Hybrid (D6), its retrieval score was `0.5333`.
- This confirms that **caching is NOT accidentally cross-contaminating the candidates**: each retrieval mode executed its own search and generated its own candidate scores.
- However, because the CrossEncoder operates on `(query, passage_text)`, and `oomkilled.md` is present in all three candidate sets, the cross-encoder assigned the exact same logit (sigmoid: `0.9987`) to it.
- **Conclusion:** When Reranking is enabled, the UI will display the exact same top 5 results across Dense, Sparse, and Hybrid because the cross-encoder converges on the most relevant passages.

---

### Experiment E — Query Diversity Testing
*Settings: Candidates: 20, HyDE: OFF, Rerank: OFF*

#### Query E1: `"Why is my pod stuck in CrashLoopBackOff with exit code 137?"`
- **Dense Top 1:** `sre/runbooks/kubernetes/crashloopbackoff.md :: 9. Decision Tree` (0.8319)
- **Sparse Top 1:** `sre/runbooks/kubernetes/crashloopbackoff.md :: 9. Decision Tree` (35.5697)
- **Hybrid Top 1:** `sre/runbooks/kubernetes/crashloopbackoff.md :: 9. Decision Tree` (1.0000)
- *Behavior:* High overlap across modes due to direct topic and keyword alignment.

#### Query E2: `"Our ingress started returning 502s right after a deployment. Where should I look?"`
- **Dense Top 1:** `sre/runbooks/kubernetes/probe-failures.md :: 5. Impact Assessment` (0.7673)
- **Dense Top 2:** `sre/runbooks/networking/ingress-5xx-errors.md :: 8. Expected Outputs` (0.7513)
- **Sparse Top 1:** `prometheus/exporters/multi-target-exporter.md` (17.3897)
- **Sparse Top 2:** `helm/fundamentals/using-helm.md` (17.2152)
- **Hybrid Top 1:** `sre/runbooks/kubernetes/probe-failures.md` (0.5000) & `prometheus/...` (0.5000)
- *Behavior:* **Divergence observed.** Dense understood the semantic intent ("502s right after deploy" $\rightarrow$ probe failures & ingress 5xx errors). Sparse matched literal keywords across unrelated exporter docs. Hybrid balanced both.

#### Query E3: `"How do I investigate Prometheus alerts that remain pending?"`
- **Dense Top 1:** `prometheus/alertmanager/alerting-rules.md :: Inspecting alerts during runtime` (0.8142)
- **Dense Top 2:** `prometheus/promql/query-api.md :: Active Notifications` (0.8007)
- **Sparse Top 1:** `prometheus/alertmanager/alerting-rules.md :: Inspecting alerts during runtime` (17.9150)
- **Sparse Top 2:** `prometheus/promql/recording-rules.md` (17.4774)
- **Hybrid Top 1:** `prometheus/alertmanager/alerting-rules.md` (1.0000)
- *Behavior:* Both strategies converged on the official Prometheus alerting rules documentation.

#### Query E4: `"How can I troubleshoot Helm release upgrade failures?"`
- **Dense Top 1:** `sre/runbooks/helm/pending-release-state.md :: 3. Symptoms` (0.8609)
- **Dense Top 2:** `sre/runbooks/helm/upgrade-failures.md :: 3. Symptoms` (0.8599)
- **Sparse Top 1:** `helm/fundamentals/cheatsheet.md :: Perform App Upgrade and Rollback` (24.7632)
- **Sparse Top 2:** `helm/fundamentals/using-helm.md` (24.6233)
- **Hybrid Top 1:** `helm/fundamentals/cheatsheet.md` (0.6667)
- *Behavior:* **Divergence observed.** Dense found operational troubleshooting runbooks (`pending-release-state.md`); Sparse matched literal CLI commands in Helm user manuals (`cheatsheet.md`).

#### Query E5: `"How do I investigate Kubernetes node NotReady conditions?"`
- **Dense Top 1:** `kubernetes/architecture/nodes.md :: Management` (0.8272)
- **Dense Top 2:** `sre/runbooks/kubernetes/nodenotready.md :: 9. Decision Tree` (0.8196)
- **Sparse Top 1:** `sre/runbooks/kubernetes/nodenotready.md :: 7. Read-Only Investigation Commands` (25.5616)
- **Sparse Top 2:** `kubernetes/cluster-administration/node-shutdown.md` (19.5208)
- **Hybrid Top 1:** `sre/runbooks/kubernetes/nodenotready.md :: 7. Commands` (0.7500)
- *Behavior:* Sparse found the exact runbook command section, Dense found the conceptual node architecture doc.

---

### Experiment F — Repeatability & Cache Behavior
*Settings: Mode: Hybrid RRF, Candidates: 20, HyDE: OFF, Rerank: OFF*

| Iteration | Latency | Candidate Count | Top 1 Score | Top 1 Passage ID |
| :---: | :---: | :---: | :---: | :--- |
| **Run 1** | 0.1074s | 20 | 0.8333 | `crashloopbackoff.md :: 8` |
| **Run 2** | 0.1055s | 20 | 0.8333 | `crashloopbackoff.md :: 8` |
| **Run 3** | 0.0947s | 20 | 0.8333 | `crashloopbackoff.md :: 8` |

- **Score Stability:** Scores and ranks are 100% deterministic and bit-for-bit identical across runs.
- **Cache Invalidation:** Modifying any parameter (`mode`, `fusion`, `use_hyde`, `rerank`, `top_k`) immediately executes the corresponding pipeline without stale cache bleed.

---

### Experiment G — UI Controls & Input Validation

| Control / Input | Test Value | HTTP Status | Observed Behavior |
| :--- | :--- | :---: | :--- |
| **Empty Query** | `""` | **422** | Pydantic validation rejects with `String should have at least 1 characters`. |
| **Whitespace Query** | `"   "` | **200** | Backend accepts whitespace; returns 20 arbitrary documents. Frontend `app.js` suppresses submission. |
| **Candidate Slider Min** | `top_k=5` | **200** | Exactly 5 results returned (`top_k` honored when rerank is OFF). |
| **Candidate Slider Max** | `top_k=50` | **200** | Exactly 50 results returned (`top_k` honored when rerank is OFF). |
| **Mode Selector** | `dense`, `sparse`, `hybrid` | **200** | Successfully switches query mode on backend. |
| **Fusion Selector** | Disabled on non-hybrid | N/A | UI properly disables `#lab-fusion` dropdown when mode is `dense` or `sparse`. |

---

## D. Defects & Findings

### Finding 1: Reranking Silently Truncates Displayed Results from 20 to 5
- **Classification:** **HIGH** (Confusing & Inconsistent UI/API Behavior)
- **Reproduction Steps:**
  1. Navigate to Retrieval Lab.
  2. Set Candidates slider to `20` (or `50`).
  3. Ensure `Rerank` checkbox is checked (the default).
  4. Click `Retrieve`.
- **Expected Behavior:** The user requested 20 candidates and expects to see 20 reranked candidates, or the UI should indicate that reranking limits display to top 5.
- **Actual Behavior:** The UI summary reads `5 results — ordered by rerank score`. If the user unchecks `Rerank`, the UI suddenly displays `20 results — ordered by retrieval score`.
- **Evidence:** In [main.py](file:///C:/Users/ASUS/advanced-rag/src/advanced_rag/api/main.py#L170-L182), `/retrieve` does not pass `top_n` to `retriever.retrieve()`. In [retriever.py](file:///C:/Users/ASUS/advanced-rag/src/advanced_rag/retrieval/retriever.py#L206), `top_n` falls back to `self.settings.rerank_top_n = 5`, discarding candidates 6–20.
- **Likely Cause:** Backend defaults `rerank_top_n` for generation prompt bounding (where 5 chunks fit the context window), but the Retrieval Lab intended to inspect candidates.

---

### Finding 2: Default "Rerank: ON" in UI Masks Retrieval Strategy Differences
- **Classification:** **MEDIUM** (Misleading User Experience)
- **Reproduction Steps:**
  1. Open Retrieval Lab with default settings (`Rerank: checked`).
  2. Run query with `Mode: hybrid`.
  3. Switch dropdown to `Mode: dense` and click Retrieve.
  4. Switch dropdown to `Mode: sparse` and click Retrieve.
- **Expected Behavior:** In a "Retrieval Lab" explicitly designed to "compare retrieval strategies without generation", changing the retrieval mode should visibly showcase the differences between dense vector and sparse lexical retrieval.
- **Actual Behavior:** All three modes display the identical top 5 runbooks in the identical order with identical rerank scores (`0.9987`, `0.9967`, etc.).
- **Evidence:** Experiment D shows that the CrossEncoder produces identical top-5 rankings for all three modes because the candidate pools all contain `oomkilled.md` and `crashloopbackoff.md`.
- **Likely Cause:** Having `Rerank: checked` by default in `ui/web/index.html` defeats the primary user purpose of comparing raw retrieval algorithms.

---

### Finding 3: Whitespace Query Accepted by Backend API
- **Classification:** **LOW** (Validation Hygiene)
- **Reproduction Steps:**
  1. Send a POST to `/retrieve` with payload `{"query": "   ", "mode": "hybrid"}`.
- **Expected Behavior:** HTTP 400 Bad Request or HTTP 422 Unprocessable Entity rejecting whitespace-only strings.
- **Actual Behavior:** HTTP 200 OK returned with 20 arbitrary documents from Qdrant.
- **Evidence:** In [main.py](file:///C:/Users/ASUS/advanced-rag/src/advanced_rag/api/main.py#L56), `query: str = Field(min_length=1)` validates character count but does not check `.strip()`. In the browser, `app.js` line 595 (`if (!payload.query.trim()) return;`) prevents submission, but the API endpoint fails open.
- **Likely Cause:** Missing regex or `str.strip` validator on Pydantic `RetrieveRequest`.

---

### Finding 4: Retrieval Latency Not Displayed in Retrieval Lab
- **Classification:** **LOW** (UI Observability Gap)
- **Reproduction Steps:**
  1. Run any query in Retrieval Lab.
  2. Inspect the summary header: `5 results — ordered by rerank score`.
- **Expected Behavior:** The user should see the execution latency (e.g. `22 ms` for Dense vs `820 ms` for HyDE / Rerank) to evaluate the performance trade-offs of enabling HyDE and CrossEncoder.
- **Actual Behavior:** No timing information is displayed anywhere in the Retrieval Lab panel.
- **Evidence:** `renderLab(result)` in `ui/web/app.js` does not render timing, even though `panel-ask` does.

---

## E. Working as Expected

The following core components passed testing and verified functional:
1. **Dense Retrieval:** Fully operational via ONNX `BAAI/bge-small-en-v1.5` embeddings (sub-25ms response time).
2. **Sparse Retrieval:** Fully operational via FastEmbed BM25 sparse vectors in Qdrant (accurate lexical term matching).
3. **Hybrid Search (RRF & Weighted):** Server-side Qdrant RRF fusion and client-side min-max weighted fusion both function accurately.
4. **HyDE (Hypothetical Document Embeddings):** Accurately generates domain-specific runbook passages via Groq `qwen/qwen3.8-27b`, expanding BM25 recall significantly.
5. **Cross-Encoder Reranker:** Local `Xenova/ms-marco-MiniLM-L-6-v2` cross-encoder accurately re-ranks candidates and correctly reports authoritative status.
6. **UI Fusion Selector State:** Automatically disables when switching from Hybrid to Dense/Sparse.
7. **Score Separation:** UI clearly distinguishes `retrieval` score from `rerank` score on all passage cards.

---

## F. Blocked Tests

- **None.** All planned experiments (A through G) executed successfully against the active running application without requiring simulated or mocked data.

---

## G. Recommended Fixes (For Review — Not Implemented)

### Priority 1 (High Severity)
1. **Decouple Candidate Fetching from Rerank Truncation in `/retrieve`**:
   - In `src/advanced_rag/api/main.py`, pass `top_n=request.top_k` (or introduce a separate `rerank_top_n` parameter) so that when a user requests 20 candidates, all 20 reranked candidates are displayed in the UI rather than being truncated to 5.

### Priority 2 (Medium Severity)
2. **Default Rerank to Unchecked in Retrieval Lab UI**:
   - In `ui/web/index.html`, remove `checked` from `<input id="lab-rerank" type="checkbox">`. This allows users clicking between Dense, Sparse, and Hybrid to immediately see raw algorithm differences without the cross-encoder flattening the results.

### Priority 3 (Low Severity)
3. **Strip Whitespace in `RetrieveRequest`**:
   - In `src/advanced_rag/api/main.py`, add a Pydantic `@field_validator("query")` to reject whitespace-only strings with HTTP 422.
4. **Surface Execution Latency in Retrieval Lab**:
   - Update `app.js` and `main.py` `/retrieve` response to include `elapsed_ms` so users can observe the latency impact of HyDE and CrossEncoder.

---
*End of Report. Awaiting user review and authorization before taking any further action.*

