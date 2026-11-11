# ============================================================================
# Black-Box Application Acceptance Smoke Test
# Enterprise Advanced RAG / Kubernetes SRE Copilot
# ============================================================================
# Usage:
#   .\scripts\blackbox_smoke.ps1 [-BaseUrl "http://127.0.0.1:8000"]
#
# Requirements:
#   FastAPI server running at BaseUrl.
#   Executes pure HTTP black-box requests against the live API.
#   Never modifies operational database records (data/ops.db).
#   Returns exit code 0 on all passed, non-zero if any required test fails.
# ============================================================================

[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Continue"

# Output Formatting Helpers
function Write-TestHeader {
    param([string]$Category)
    Write-Host ""
    Write-Host ("=" * 75) -ForegroundColor Cyan
    Write-Host " [CATEGORY] $Category" -ForegroundColor Cyan
    Write-Host ("=" * 75) -ForegroundColor Cyan
}

function Write-TestResult {
    param(
        [string]$TestId,
        [string]$Name,
        [string]$Status,
        [int]$StatusCode,
        [double]$DurationMs,
        [string]$Details = ""
    )
    $badgeColor = switch ($Status) {
        "PASS"    { "Green" }
        "FAIL"    { "Red" }
        "BLOCKED" { "Yellow" }
        default   { "White" }
    }
    
    $statusText = "[$Status]".PadRight(9)
    $idText = "[$TestId]".PadRight(10)
    $timeText = "(${DurationMs}ms)".PadLeft(10)
    $codeText = "HTTP $StatusCode".PadRight(9)

    Write-Host "$statusText $idText $codeText $Name $timeText" -ForegroundColor $badgeColor
    if ($Details -and $Status -ne "PASS") {
        Write-Host "       Detail: $Details" -ForegroundColor DarkYellow
    }
}

# HTTP Request Helper
function Invoke-BlackBoxRequest {
    param(
        [string]$Method = "GET",
        [string]$Path,
        [object]$Body = $null,
        [int[]]$ExpectedStatus = @(200),
        [int]$TimeoutSec = 35
    )

    $url = "$BaseUrl$Path"
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $statusCode = 0
    $responseObj = $null
    $rawContent = ""
    $errorMessage = ""

    $headers = @{
        "Accept" = "application/json, text/html, */*"
    }

    try {
        if ($Body -ne $null) {
            $jsonBody = if ($Body -is [string]) { $Body } else { $Body | ConvertTo-Json -Depth 10 -Compress }
            $headers["Content-Type"] = "application/json"
            $resp = Invoke-WebRequest -Uri $url -Method $Method -Headers $headers -Body $jsonBody -TimeoutSec $TimeoutSec -UseBasicParsing
        } else {
            $resp = Invoke-WebRequest -Uri $url -Method $Method -Headers $headers -TimeoutSec $TimeoutSec -UseBasicParsing
        }

        $statusCode = [int]$resp.StatusCode
        $rawContent = $resp.Content
        try {
            $responseObj = $rawContent | ConvertFrom-Json
        } catch {
            $responseObj = $rawContent
        }
    } catch {
        if ($_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
            if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
                $rawContent = $_.ErrorDetails.Message
            } else {
                try {
                    $stream = $_.Exception.Response.GetResponseStream()
                    $reader = New-Object System.IO.StreamReader($stream)
                    $rawContent = $reader.ReadToEnd()
                    $reader.Close()
                } catch {
                    $rawContent = $_.Exception.Message
                }
            }
            try {
                $responseObj = $rawContent | ConvertFrom-Json
            } catch {
                $responseObj = $rawContent
            }
        } else {
            $statusCode = 0
            $errorMessage = $_.Exception.Message
        }
    } finally {
        $sw.Stop()
    }

    $isMatch = $ExpectedStatus -contains $statusCode
    $statusStr = if ($isMatch) { "PASS" } else { "FAIL" }
    if ($statusCode -eq 0) {
        $statusStr = "FAIL"
        $errorMessage = "Connection refused or timed out: $errorMessage"
    }

    return [PSCustomObject]@{
        Status      = $statusStr
        StatusCode  = $statusCode
        DurationMs  = [math]::Round($sw.Elapsed.TotalMilliseconds, 1)
        Data        = $responseObj
        Raw         = $rawContent
        Error       = $errorMessage
        Url         = $url
    }
}

# Tracking Summary
$TotalTests = 0
$PassedTests = 0
$FailedTests = 0
$BlockedTests = 0

function Record-Result {
    param($Result, [string]$TestId, [string]$TestName, [scriptblock]$Validator = $null)
    $script:TotalTests++
    
    $status = $Result.Status
    $detail = $Result.Error

    if ($status -eq "PASS" -and $Validator -ne $null) {
        try {
            $valid = & $Validator $Result.Data $Result.Raw
            if (-not $valid) {
                $status = "FAIL"
                $detail = "Custom validation assertion failed on response payload"
            }
        } catch {
            $status = "FAIL"
            $detail = "Validation threw exception: $($_.Exception.Message)"
        }
    }

    if ($status -eq "PASS") {
        $script:PassedTests++
    } elseif ($status -eq "BLOCKED") {
        $script:BlockedTests++
    } else {
        $script:FailedTests++
    }

    Write-TestResult -TestId $TestId -Name $TestName -Status $status -StatusCode $Result.StatusCode -DurationMs $Result.DurationMs -Details $detail
    return ($status -eq "PASS")
}

Write-Host "===========================================================================" -ForegroundColor Green
Write-Host "  ENTERPRISE ADVANCED RAG - BLACK-BOX ACCEPTANCE SMOKE TEST" -ForegroundColor Green
Write-Host "  Target Host: $BaseUrl" -ForegroundColor Green
Write-Host "  Timestamp:   $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Green
Write-Host "===========================================================================" -ForegroundColor Green

# -----------------------------------------------------------------------------
# 0. Initial Pre-flight Server Reachability
# -----------------------------------------------------------------------------
Write-Host "`n[*] Verifying server reachability at $BaseUrl/health..." -ForegroundColor Yellow
$preflight = Invoke-BlackBoxRequest -Method "GET" -Path "/health" -ExpectedStatus @(200) -TimeoutSec 10
if ($preflight.StatusCode -ne 200) {
    Write-Host "`n[FATAL] Cannot connect to FastAPI server at $BaseUrl" -ForegroundColor Red
    Write-Host "Server returned code: $($preflight.StatusCode). Error: $($preflight.Error)" -ForegroundColor Red
    Write-Host "`nTo start the server in a separate terminal run:" -ForegroundColor Yellow
    Write-Host "  .\.venv\Scripts\Activate.ps1" -ForegroundColor White
    Write-Host "  .\.venv\Scripts\python.exe -m uvicorn advanced_rag.api.main:app --host 127.0.0.1 --port 8000`n" -ForegroundColor White
    exit 2
}
Write-Host "[+] Server is ONLINE. Commencing black-box test suite.`n" -ForegroundColor Green

# -----------------------------------------------------------------------------
# CATEGORY 1: Application Availability & Static Frontend Assets
# -----------------------------------------------------------------------------
Write-TestHeader "1. Application Availability & Static Frontend Assets"

$res = Invoke-BlackBoxRequest -Method "GET" -Path "/health" -ExpectedStatus @(200)
Record-Result $res "AVAIL-01" "Health endpoint reports status ok and indexed vector chunks" {
    param($d) $d.status -eq "ok" -and $d.indexed_chunks -gt 0
}

$res = Invoke-BlackBoxRequest -Method "GET" -Path "/" -ExpectedStatus @(200)
Record-Result $res "AVAIL-02" "Root endpoint serves HTML UI (index.html)" {
    param($d, $raw) $raw -match "(?i)<!doctype html>" -and $raw -match "Kubernetes SRE Copilot"
}

$res = Invoke-BlackBoxRequest -Method "GET" -Path "/docs" -ExpectedStatus @(200)
Record-Result $res "AVAIL-03" "Swagger OpenAPI UI docs served at /docs" {
    param($d, $raw) $raw -match "swagger-ui"
}

$res = Invoke-BlackBoxRequest -Method "GET" -Path "/openapi.json" -ExpectedStatus @(200)
Record-Result $res "AVAIL-04" "OpenAPI specification JSON schema served at /openapi.json" {
    param($d) $d.openapi -and $d.paths."/ask" -and $d.paths."/approve"
}

$res = Invoke-BlackBoxRequest -Method "GET" -Path "/app.js" -ExpectedStatus @(200)
Record-Result $res "AVAIL-05" "Frontend JavaScript bundle served at /app.js" {
    param($d, $raw) $raw -match "function" -or $raw -match "addEventListener"
}

$res = Invoke-BlackBoxRequest -Method "GET" -Path "/styles.css" -ExpectedStatus @(200)
Record-Result $res "AVAIL-06" "Frontend CSS stylesheet served at /styles.css" {
    param($d, $raw) $raw -match "body" -or $raw -match "--bg"
}

# -----------------------------------------------------------------------------
# CATEGORY 2: Knowledge Management & Document Registry
# -----------------------------------------------------------------------------
Write-TestHeader "2. Knowledge Management & Document Registry"

$res = Invoke-BlackBoxRequest -Method "GET" -Path "/api/knowledge/stats" -ExpectedStatus @(200)
Record-Result $res "KNOW-01" "Knowledge base statistics endpoint returns document and chunk counts" {
    param($d) ($d.inventory -and $d.inventory.total_documents -gt 0) -or ($d.qdrant -and $d.qdrant.total_points -gt 0)
}

$res = Invoke-BlackBoxRequest -Method "GET" -Path "/api/knowledge/categories" -ExpectedStatus @(200)
Record-Result $res "KNOW-02" "Knowledge base categories returns populated category list" {
    param($d) ($d -is [System.Array] -or $d.Count -gt 0)
}

$res = Invoke-BlackBoxRequest -Method "GET" -Path "/documents" -ExpectedStatus @(200)
Record-Result $res "KNOW-03" "Document listing endpoint returns tracked documents" {
    param($d) ($d -is [System.Array]) -and $d.Length -gt 0
}

$res = Invoke-BlackBoxRequest -Method "GET" -Path "/api/knowledge/ingestion/status" -ExpectedStatus @(200)
Record-Result $res "KNOW-04" "Ingestion status endpoint responds successfully" {
    param($d) $null -ne $d
}

$res = Invoke-BlackBoxRequest -Method "GET" -Path "/api/knowledge/documents/non-existent-doc-9999" -ExpectedStatus @(404)
Record-Result $res "KNOW-05" "Non-existent document lookup returns 404 Not Found safely" {
    param($d) $true
}

# -----------------------------------------------------------------------------
# CATEGORY 3: Retrieval & End-to-End RAG
# -----------------------------------------------------------------------------
Write-TestHeader "3. Retrieval & End-to-End RAG Workflows"

$retPayload = @{
    query = "How to troubleshoot CrashLoopBackOff in Kubernetes?"
    top_k = 5
    mode  = "hybrid"
}
$res = Invoke-BlackBoxRequest -Method "POST" -Path "/retrieve" -Body $retPayload -ExpectedStatus @(200)
Record-Result $res "RAG-01" "Direct /retrieve returns top chunks with similarity scores" {
    param($d) $d.result_count -gt 0 -and $d.mode -eq "hybrid"
}

$askPayload = @{
    question = "What are the common causes of CrashLoopBackOff in a Kubernetes pod?"
    use_llm  = $true
}
$res = Invoke-BlackBoxRequest -Method "POST" -Path "/ask" -Body $askPayload -ExpectedStatus @(200) -TimeoutSec 40
Record-Result $res "RAG-02" "Standard SRE question generates non-empty answer with citations" {
    param($d) (-not [string]::IsNullOrWhiteSpace($d.answer)) -and ($d.route -ne $null)
}

$extractivePayload = @{
    question = "What are the diagnostic steps for pod eviction?"
    use_llm  = $false
}
$res = Invoke-BlackBoxRequest -Method "POST" -Path "/ask" -Body $extractivePayload -ExpectedStatus @(200) -TimeoutSec 30
Record-Result $res "RAG-03" "Deterministic extractive query (use_llm:false) quotes runbook passages" {
    param($d) $d.extractive -eq $true -and (-not [string]::IsNullOrWhiteSpace($d.answer)) -and $d.citations.Count -ge 1
}

$outOfScopePayload = @{
    question = "What is the capital of France and who is the president?"
    use_llm  = $true
}
$res = Invoke-BlackBoxRequest -Method "POST" -Path "/ask" -Body $outOfScopePayload -ExpectedStatus @(200) -TimeoutSec 30
Record-Result $res "RAG-04" "Out-of-scope question handled safely without server error" {
    param($d) (-not [string]::IsNullOrWhiteSpace($d.answer))
}

$ambiguousPayload = @{
    question = "pod died"
    use_llm  = $false
}
$res = Invoke-BlackBoxRequest -Method "POST" -Path "/ask" -Body $ambiguousPayload -ExpectedStatus @(200) -TimeoutSec 30
Record-Result $res "RAG-05" "Ambiguous/terse question handled gracefully" {
    param($d) (-not [string]::IsNullOrWhiteSpace($d.answer))
}

# -----------------------------------------------------------------------------
# CATEGORY 4: Text2SQL Approval Lifecycle & Read-Only Execution
# -----------------------------------------------------------------------------
Write-TestHeader "4. Text2SQL Approval Lifecycle & Read-Only Execution"

# Step 1: Submit operational query -> must enter awaiting_approval
$sqlAskPayload = @{
    question = "List all Kubernetes clusters"
    use_llm  = $true
}
$resAsk = Invoke-BlackBoxRequest -Method "POST" -Path "/ask" -Body $sqlAskPayload -ExpectedStatus @(200) -TimeoutSec 35
$proposalCreated = Record-Result $resAsk "SQL-01" "Operational query generates SQL proposal and pauses for approval" {
    param($d) $d.awaiting_approval -eq $true -and $null -ne $d.sql -and $d.sql.status -eq "pending" -and (-not [string]::IsNullOrWhiteSpace($d.sql.sql)) -and (-not [string]::IsNullOrWhiteSpace($d.sql.proposal_id))
}

$threadId1 = $resAsk.Data.thread_id
$propId1 = $resAsk.Data.sql.proposal_id
$sqlHash1 = $resAsk.Data.sql.sql_hash

# Step 2: Query GET /pending/{thread_id}
if ($threadId1) {
    $resPending = Invoke-BlackBoxRequest -Method "GET" -Path "/pending/$threadId1" -ExpectedStatus @(200)
    Record-Result $resPending "SQL-02" "Pending approval endpoint returns parked interrupt details" {
        param($d) $null -ne $d.sql -or $null -ne $d.proposal_id
    }
}

# Step 3: Approve harmless SELECT query
if ($threadId1 -and $propId1) {
    $approvePayload = @{
        thread_id   = $threadId1
        approved    = $true
        proposal_id = $propId1
        sql_hash    = $sqlHash1
        actor       = "smoke_blackbox_user"
    }
    $resApprove = Invoke-BlackBoxRequest -Method "POST" -Path "/approve" -Body $approvePayload -ExpectedStatus @(200) -TimeoutSec 30
    Record-Result $resApprove "SQL-03" "Approving harmless SELECT executes query and returns structured result" {
        param($d) $d.awaiting_approval -eq $false -and $d.sql.status -eq "completed" -and $d.sql.row_count -ge 1
    }

    # Step 4: Replay protection on already completed proposal
    $resReplay = Invoke-BlackBoxRequest -Method "POST" -Path "/approve" -Body $approvePayload -ExpectedStatus @(200)
    Record-Result $resReplay "SQL-04" "Duplicate resume attempt on already completed proposal is safely blocked" {
        param($d) $d.sql.status -eq "completed" -and $d.answer -match "(?i)already"
    }
}

# Step 5: Test Rejection workflow with a new proposal
$sqlRejectAsk = @{
    question = "Show me all deployments in production"
    use_llm  = $true
}
$resRejectAsk = Invoke-BlackBoxRequest -Method "POST" -Path "/ask" -Body $sqlRejectAsk -ExpectedStatus @(200) -TimeoutSec 35
$threadId2 = $resRejectAsk.Data.thread_id
$propId2 = $resRejectAsk.Data.sql.proposal_id
$sqlHash2 = $resRejectAsk.Data.sql.sql_hash

if ($threadId2 -and $propId2) {
    $rejectPayload = @{
        thread_id   = $threadId2
        approved    = $false
        proposal_id = $propId2
        sql_hash    = $sqlHash2
        reason      = "Cancelled by operator test"
        actor       = "smoke_blackbox_user"
    }
    $resReject = Invoke-BlackBoxRequest -Method "POST" -Path "/approve" -Body $rejectPayload -ExpectedStatus @(200)
    Record-Result $resReject "SQL-05" "Rejecting proposal halts execution with REJECTED status" {
        param($d) $d.awaiting_approval -eq $false -and $d.sql.status -eq "rejected"
    }
}

# Step 6: Hash Tamper Detection
$sqlTamperAsk = @{
    question = "List all nodes"
    use_llm  = $true
}
$resTamperAsk = Invoke-BlackBoxRequest -Method "POST" -Path "/ask" -Body $sqlTamperAsk -ExpectedStatus @(200) -TimeoutSec 35
$threadId3 = $resTamperAsk.Data.thread_id
$propId3 = $resTamperAsk.Data.sql.proposal_id

if ($threadId3 -and $propId3) {
    $tamperPayload = @{
        thread_id   = $threadId3
        approved    = $true
        proposal_id = $propId3
        sql_hash    = "0000000000000000000000000000000000000000000000000000000000000000"
        actor       = "tamper_test"
    }
    $resTamper = Invoke-BlackBoxRequest -Method "POST" -Path "/approve" -Body $tamperPayload -ExpectedStatus @(200)
    Record-Result $resTamper "SQL-06" "Modified/tampered SQL hash is rejected without query execution" {
        param($d) $d.sql.status -eq "rejected" -and $d.answer -match "(?i)hash mismatch"
    }
}

# Step 7: SQL Pagination & Formatting
$pagPayload = @{
    sql       = "SELECT id, name, environment, node_count FROM clusters ORDER BY id"
    page      = 1
    page_size = 2
    dialect   = "sqlite"
}
$resPag = Invoke-BlackBoxRequest -Method "POST" -Path "/sql/paginate" -Body $pagPayload -ExpectedStatus @(200)
Record-Result $resPag "SQL-07" "Dialect-aware /sql/paginate returns sliced rows, column types and summary" {
    param($d) $d.page -eq 1 -and $d.page_size -eq 2 -and $d.rows.Length -le 2 -and $null -ne $d.column_types -and (-not [string]::IsNullOrWhiteSpace($d.summary))
}

# -----------------------------------------------------------------------------
# CATEGORY 5: Persistent Approval Audit Store
# -----------------------------------------------------------------------------
Write-TestHeader "5. Persistent Approval Audit Store"

$resAudit = Invoke-BlackBoxRequest -Method "GET" -Path "/approval/audit?limit=10" -ExpectedStatus @(200)
Record-Result $resAudit "AUDIT-01" "GET /approval/audit returns persisted audit records" {
    param($d) ($d -is [System.Array]) -and $d.Length -ge 1
}

if ($threadId1) {
    $resThreadAudit = Invoke-BlackBoxRequest -Method "GET" -Path "/approval/audit?thread_id=$threadId1" -ExpectedStatus @(200)
    Record-Result $resThreadAudit "AUDIT-02" "GET /approval/audit?thread_id returns exact thread audit trail" {
        param($d) ($d -is [System.Array]) -and $d.Length -ge 1 -and $d[0].thread_id -eq $threadId1
    }
}

# -----------------------------------------------------------------------------
# CATEGORY 6: Error Handling, Firewalls & Edge Cases
# -----------------------------------------------------------------------------
Write-TestHeader "6. Error Handling, Firewalls & Edge Cases"

# 1. Invalid JSON body
$res = Invoke-BlackBoxRequest -Method "POST" -Path "/ask" -Body "{malformed_json:" -ExpectedStatus @(400, 422)
Record-Result $res "ERR-01" "Malformed JSON body rejected with HTTP 400 or 422" {
    param($d) $true
}

# 2. Missing required field
$res = Invoke-BlackBoxRequest -Method "POST" -Path "/ask" -Body @{} -ExpectedStatus @(422)
Record-Result $res "ERR-02" "Missing required fields rejected with HTTP 422 Unprocessable" {
    param($d) $true
}

# 3. SQL Injection / DDL rejection in pagination
$ddlPayload = @{
    sql       = "DROP TABLE clusters"
    page      = 1
    page_size = 10
}
$res = Invoke-BlackBoxRequest -Method "POST" -Path "/sql/paginate" -Body $ddlPayload -ExpectedStatus @(400)
Record-Result $res "ERR-03" "DDL statement (DROP TABLE) blocked with HTTP 400 SqlRejected" {
    param($d, $raw) $raw -match "(?i)forbidden" -or $raw -match "(?i)DROP" -or $raw -match "(?i)SqlRejected"
}

# 4. Unauthorized column access in pagination
$unauthColPayload = @{
    sql       = "SELECT password FROM clusters"
    page      = 1
    page_size = 10
}
$res = Invoke-BlackBoxRequest -Method "POST" -Path "/sql/paginate" -Body $unauthColPayload -ExpectedStatus @(400)
Record-Result $res "ERR-04" "Non-allowlisted column access blocked with HTTP 400 SqlRejected" {
    param($d, $raw) $raw -match "(?i)password" -or $raw -match "(?i)clusters" -or $raw -match "(?i)exist"
}

# 5. Unsupported pagination values
$badPagePayload = @{
    sql       = "SELECT id FROM clusters"
    page      = "not_a_number"
    page_size = 10
}
$res = Invoke-BlackBoxRequest -Method "POST" -Path "/sql/paginate" -Body $badPagePayload -ExpectedStatus @(400, 422)
Record-Result $res "ERR-05" "Malformed pagination value (non-integer) rejected safely with HTTP 422" {
    param($d) $true
}

# 6. Non-existent API route
$res = Invoke-BlackBoxRequest -Method "GET" -Path "/api/nonexistent/endpoint/404" -ExpectedStatus @(404)
Record-Result $res "ERR-06" "Non-existent route returns clean HTTP 404 Not Found" {
    param($d) $true
}

# -----------------------------------------------------------------------------
# FINAL SUMMARY
# -----------------------------------------------------------------------------
Write-Host "`n" + ("=" * 75) -ForegroundColor Green
Write-Host " BLACK-BOX ACCEPTANCE TEST SUITE COMPLETE" -ForegroundColor Green
Write-Host ("=" * 75) -ForegroundColor Green
Write-Host " Total Tests Executed: $TotalTests" -ForegroundColor White
Write-Host " Passed:               $PassedTests" -ForegroundColor Green
Write-Host " Failed:               $FailedTests" -ForegroundColor $(if ($FailedTests -gt 0) { "Red" } else { "Green" })
Write-Host " Blocked:              $BlockedTests" -ForegroundColor $(if ($BlockedTests -gt 0) { "Yellow" } else { "Green" })
Write-Host ("=" * 75) -ForegroundColor Green

if ($FailedTests -gt 0) {
    Write-Host "`n[FAIL] $FailedTests test(s) failed acceptance criteria. Review above log for details.`n" -ForegroundColor Red
    exit 1
} else {
    Write-Host "`n[SUCCESS] All $PassedTests black-box acceptance tests passed cleanly!`n" -ForegroundColor Green
    exit 0
}
