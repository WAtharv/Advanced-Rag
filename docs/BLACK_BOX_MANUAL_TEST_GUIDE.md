# Black-Box Manual Browser Acceptance Testing Guide
## Enterprise Advanced RAG / Kubernetes SRE Copilot

**Audience**: Human QA / Operator / End-User
**Environment**: Local Windows Workstation (`127.0.0.1:8000`)
**Scope**: End-to-End manual testing via Google Chrome, Microsoft Edge, or Mozilla Firefox.
**Deliverable**: `docs/BLACK_BOX_MANUAL_TEST_GUIDE.md`

---

## 1. Prerequisites & Application Startup

Before beginning manual browser testing, launch the FastAPI server in a dedicated PowerShell terminal:

```powershell
# In Terminal 1: Activate environment and start server
cd C:\Users\ASUS\advanced-rag
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\python.exe -m uvicorn advanced_rag.api.main:app --host 127.0.0.1 --port 8000
```

### Verification of Successful Startup
Look for the following log output in Terminal 1:
```text
INFO:     Started server process [...]
INFO:     Waiting for application startup.
INFO:     advanced_rag.certs | TLS verification is using the system trust store
INFO:     advanced_rag.api.main | Starting API (model=qwen/qwen3.8-27b)
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

### Troubleshooting Occupied Port 8000
If port 8000 is occupied by a stale process, identify and stop it:
```powershell
# Check which process is listening on port 8000
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object LocalAddress, LocalPort, OwningProcess

# Stop only that specific process
Stop-Process -Id <OwningProcessId> -Force
```

---

## 2. Step-by-Step Manual Browser Checklist

Open your web browser and navigate to:
👉 **`http://127.0.0.1:8000`**

Open your browser's Developer Tools by pressing **`F12`** (or right-click -> **Inspect**) and navigate to the **Console** and **Network** tabs.

---

### Step 1: Opening the Application & Initial Page Load
- **Action**: Load `http://127.0.0.1:8000` in the browser.
- **Expected Result**:
  - The SRE Copilot dark-themed single-page application renders immediately.
  - Page title displays: `Kubernetes SRE Copilot`.
  - Header displays system status badges: `System: Online`, `Vector Index: 5533 chunks`, `LLM Mode: Live` (or `Offline`), `Dialect: SQLite`.
- **Failure Indicator**: White blank page, 404/500 banner, or "Failed to load system health".
- **Evidence to Capture**: Screenshot of the initial dashboard header and status badges.

---

### Step 2: Dashboard Navigation & Tabs
- **Action**: Click through the navigation tabs: **Copilot (Chat)**, **Knowledge Base**, **Audit Trail**, and **Settings/Docs**.
- **Expected Result**:
  - Switching between views occurs instantly with smooth transitions.
  - **Knowledge Base** tab displays document stats (584 documents indexed across categories like `alertmanager`, `fundamentals`, `architecture`).
  - **Audit Trail** tab displays recent approval/execution history in a tabular layout.
- **Failure Indicator**: Clicking tabs does nothing, console displays `Uncaught TypeError`, or content flickers/disappears.
- **Evidence to Capture**: Browser console showing zero red JavaScript errors.

---

### Step 3: Asking a Kubernetes Diagnostic Question
- **Action**: In the **Copilot** chat input box, type:
  `How to troubleshoot CrashLoopBackOff in Kubernetes?`
  Ensure the **Without LLM** toggle is **OFF** (using LLM mode), and press **Enter** (or click Send).
- **Expected Result**:
  - A subtle loading indicator appears while the query runs.
  - The response streams or displays a structured answer explaining diagnostic steps (`kubectl describe pod`, `kubectl logs --previous`, exit code analysis).
  - The route badge displays `VECTOR` or `HYBRID`.
- **Failure Indicator**: Spinner hangs permanently, an unhandled error alert displays, or empty text is returned.
- **Evidence to Capture**: Screenshot of the completed assistant answer card.

---

### Step 4: Viewing Retrieved Citations & Sources
- **Action**: Inspect the bottom of the response card from Step 3. Click the **Citations** accordion/drawer (e.g. `5 Citations`).
- **Expected Result**:
  - Citations expand to reveal runbook sources, similarity scores (e.g. `score: 0.83+`), and snippet excerpts.
  - Clicking a source badge reveals the document title or metadata origin.
- **Failure Indicator**: Drawer does not expand, citation count is zero, or raw JSON `[object Object]` is rendered.
- **Evidence to Capture**: Screenshot of the expanded citations drawer with source runbook names.

---

### Step 5: Testing Extractive RAG Mode ("Without LLM")
- **Action**:
  1. Toggle the **Without LLM** switch to **ON**.
  2. Ask: `What are the diagnostic steps for pod eviction?`
  3. Click Send.
- **Expected Result**:
  - Query executes deterministically in < 1 second.
  - Response banner shows badge: `EXTRACTIVE (No LLM)`.
  - Content directly quotes exact passages from the indexed Kubernetes eviction runbook.
  - Inline source citations are present.
- **Failure Indicator**: Generated conversational prose appears instead of quoted passages, or an LLM call is triggered.
- **Evidence to Capture**: Screenshot highlighting the `EXTRACTIVE` badge and verbatim quoted passages.

---

### Step 6: Asking an Operational Database Question (Text2SQL Proposal)
- **Action**:
  1. Toggle **Without LLM** back to **OFF**.
  2. Ask: `List all Kubernetes clusters`
  3. Click Send.
- **Expected Result**:
  - The pipeline routes the query to `SQL`.
  - Execution **pauses automatically** at the human approval gate.
  - The chat surfaces an **Approval Required** interactive proposal card.
- **Failure Indicator**: The query executes directly without asking for human confirmation, or crashes with a 500 error.
- **Evidence to Capture**: Screenshot of the interactive SQL Approval card.

---

### Step 7: Reviewing the Generated SQL Proposal Card
- **Action**: Carefully inspect the details inside the SQL proposal card:
- **Expected Result**:
  - Target SQL: `SELECT id, name, environment, node_count FROM clusters...` (or similar valid SELECT).
  - Dialect: `SQLite` (or `PostgreSQL`).
  - Target Tables: `clusters`.
  - Proposal ID: A distinct UUID (e.g. `76c29b32...`).
  - Status Badge: `PENDING APPROVAL` (yellow).
  - Action Buttons: Green **Approve** and Red **Reject** buttons are active.
- **Failure Indicator**: Missing SQL statement, missing proposal ID, or DDL/mutation query proposed.
- **Evidence to Capture**: Screenshot of the proposal card metadata.

---

### Step 8: Approving a Safe SELECT Proposal
- **Action**: Click the green **Approve** button on the proposal card.
- **Expected Result**:
  - Button enters brief loading state, then transitions status to `COMPLETED` (green).
  - A structured results table renders below the query showing the 5 clusters (`prod-us-east`, `prod-eu-west`, `staging-us`, etc.).
  - Column headers show inferred types: `id (INTEGER)`, `name (TEXT)`, `environment (TEXT)`.
  - SRE summary sentence renders: `Found 5 clusters.`
- **Failure Indicator**: Query fails, table is blank, or proposal remains in PENDING state.
- **Evidence to Capture**: Screenshot of the rendered data table, column types, and SRE summary.

---

### Step 9: Rejecting an Unwanted Proposal
- **Action**:
  1. Ask: `Show me all deployments in production`
  2. When the proposal card appears, click the red **Reject** button.
  3. If prompted for a reason, type `Testing operator abort` and confirm.
- **Expected Result**:
  - Proposal transitions immediately to `REJECTED` (red badge).
  - No database query is executed.
  - The assistant replies: `Proposal was rejected by operator: Testing operator abort`.
- **Failure Indicator**: Database table renders despite clicking reject, or UI throws an error.
- **Evidence to Capture**: Screenshot of the rejected proposal state.

---

### Step 10: Checking Audit History in the Web UI
- **Action**: Click on the **Audit Trail** tab in the top navigation.
- **Expected Result**:
  - The audit table lists both proposals from Steps 8 and 9.
  - Columns show: `Timestamp`, `Proposal ID`, `SQL Query`, `Status` (`completed`, `rejected`), and `Actor` (`web_ui` or operator identity).
- **Failure Indicator**: Empty audit table, or rejected proposal listed as completed.
- **Evidence to Capture**: Screenshot of the Audit Trail table showing both lifecycle entries.

---

### Step 11: Testing Result Pagination
- **Action**: In the results table from Step 8, locate the pagination controls at the bottom (`Page 1 of ...`, `Next >`, `< Prev`). Click **Next >**.
- **Expected Result**:
  - Table requests the next slice via `POST /sql/paginate` and updates rows smoothly.
  - Page indicator updates to `Page 2`.
  - Clicking `< Prev` returns cleanly to Page 1.
- **Failure Indicator**: Page counter becomes negative, page freezes, or entire dataset reloads.
- **Evidence to Capture**: Screenshot of Page 2 with updated row subset.

---

### Step 12: Empty, Malformed & Boundary Inputs
- **Action**:
  1. Submit an empty prompt (just spaces).
  2. Submit special characters: `;;; && || <script>alert(1)</script>`.
  3. Double-click the Send button rapidly.
- **Expected Result**:
  - Empty prompt: Client-side validation prevents submission or highlights the input border in red.
  - Script injection: Safely escaped and sanitized; no modal dialogs appear.
  - Rapid clicks: Debounced; only a single request is submitted to the API.
- **Failure Indicator**: Page crashes, browser executes injected JavaScript, or duplicate simultaneous requests hang the pipeline.
- **Evidence to Capture**: Console log showing input sanitization and zero script execution.

---

### Step 13: Page Refresh & Session State
- **Action**: Press **`F5`** or `Ctrl+R` to reload the page.
- **Expected Result**:
  - Application reloads in under 500ms.
  - System status indicators re-verify immediately (`Online`, `5533 chunks`).
  - Historical audit records remain accessible in the **Audit Trail** tab.
- **Failure Indicator**: Server throws 404 on page reload, or vector store reports disconnected.
- **Evidence to Capture**: Screenshot of freshly reloaded application dashboard.

---

### Step 14: Browser Console & Network Inspection
- **Action**: Switch to the **Console** tab in Developer Tools. Inspect for red errors. Switch to the **Network** tab and filter by **Fetch/XHR**.
- **Expected Result**:
  - Zero unhandled JavaScript errors in the Console.
  - All legitimate API calls (`/health`, `/ask`, `/approve`, `/api/knowledge/*`) return HTTP status `200`.
  - Intentional test errors (e.g. 404 on invalid doc) return clean HTTP status codes and are handled by the UI without unhandled promise rejections.
- **Failure Indicator**: Red `Uncaught (in promise)` stack traces, CORS errors, or stuck `(pending)` network requests.
- **Evidence to Capture**: Network tab screenshot showing clean 200 OK responses.

---

### Step 15: Graceful Error Handling & Spinner Dismissal
- **Action**: In Developer Tools Network tab, toggle **Throttling** to **Offline** (or simulate an error), submit a question, then turn offline off.
- **Expected Result**:
  - The loading spinner ceases within the timeout period.
  - A user-friendly error banner appears: `Unable to connect to server. Please check your network connection.`
  - The Send button re-enables; the UI does **NOT** remain permanently frozen in a loading state.
- **Failure Indicator**: Infinite loading spinner spinning indefinitely; Send button disabled forever.
- **Evidence to Capture**: Screenshot showing the dismissed spinner and error notification banner.
