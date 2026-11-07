<#
.SYNOPSIS
    One-command runner for Advanced RAG. Idempotent: safe to run every time.

.DESCRIPTION
    Does the whole "get it running" dance so you don't have to:
      1. Creates .venv (uv) and installs the package the first time only.
      2. Creates .env from .env.example if it is missing (never edits an existing one).
      3. Indexes the corpus + seeds the SQLite ops DB only if that hasn't been done.
      4. Picks a free port (starts at 8000, bumps if another session holds it).
      5. Serves the API + web UI.

    Corporate defaults are applied at RUNTIME via environment variables (which
    override .env), so nothing on disk is clobbered:
      - RETRIEVAL_BACKEND=gemini   (huggingface.co is blocked here; the Gemini
        embeddings API is reachable -> real dense + sparse hybrid retrieval)
      - -Offline forces RETRIEVAL_BACKEND=keyword (pure-Python BM25, no network)

.EXAMPLE
    .\run.ps1                 # setup if needed, then serve on the first free port
    .\run.ps1 -Offline        # no API key needed: cited, quoted passages
    .\run.ps1 -Port 8010      # force a port
    .\run.ps1 -Reingest       # rebuild the index + ops DB from data/corpus
    .\run.ps1 -Install        # force a dependency reinstall
    .\run.ps1 -Backend auto   # let fastembed try to download models
    .\run.ps1 -Backend keyword # pure-Python BM25, no network at all
#>
[CmdletBinding()]
param(
    [switch]$Offline,
    [int]$Port = 8000,
    [switch]$Reingest,
    [switch]$Install,
    [ValidateSet('gemini', 'keyword', 'auto', 'fastembed')]
    [string]$Backend = 'gemini'
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

function Say([string]$msg) { Write-Host "  $msg" -ForegroundColor Cyan }

# ---------------------------------------------------------------- 1. venv + deps
if ($Install -or -not (Test-Path $venvPython)) {
    Say 'Creating virtualenv (.venv) and installing the package...'
    uv venv --python 3.13
    uv pip install -e '.[eval,dev]'
} else {
    Say 'Virtualenv ready.'
}

# ---------------------------------------------------------------- 2. .env
if (-not (Test-Path '.env')) {
    Copy-Item '.env.example' '.env'
    Say 'Created .env from .env.example (add ANTHROPIC_API_KEY if you have one).'
}

# ---------------------------------------------------------------- 3. runtime env
# HF is blocked on this network. Default to the gemini backend (dense embeddings
# via the reachable Gemini API + local BM25 sparse = real hybrid retrieval).
$env:RETRIEVAL_BACKEND = $Backend
if ($Offline) {
    # Offline means no network at all, so the gemini dense arm can't apply:
    # fall back to pure-Python BM25 and skip model calls entirely.
    $env:LLM_PROVIDER = 'offline'
    $env:RETRIEVAL_BACKEND = 'keyword'
    Say 'Offline mode: keyword retrieval + cited, quoted passages (no model calls).'
}

# ---------------------------------------------------------------- 4. ingest
$qdrantReady = (Test-Path 'data\qdrant') -and
               (Get-ChildItem 'data\qdrant' -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1)
$dbReady = Test-Path 'data\ops.db'

if ($Reingest) {
    Say 'Reingest requested: clearing data\qdrant and data\ops.db...'
    Remove-Item 'data\qdrant' -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item 'data\ops.db' -Force -ErrorAction SilentlyContinue
    $qdrantReady = $false
    $dbReady = $false
}

if (-not ($qdrantReady -and $dbReady)) {
    Say 'Indexing corpus + seeding the ops database (one-time)...'
    & $venvPython -m advanced_rag.ingestion.cli --seed-sql
    if ($LASTEXITCODE -ne 0) { throw "Ingestion failed (exit $LASTEXITCODE)." }
} else {
    Say 'Index + ops DB already present (use -Reingest to rebuild).'
}

# ---------------------------------------------------------------- 5. free port
function Test-PortBusy([int]$p) {
    [bool](Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue)
}
$chosen = $Port
while ((Test-PortBusy $chosen) -and ($chosen -lt $Port + 20)) {
    Say "Port $chosen is in use (another session?), trying $($chosen + 1)..."
    $chosen++
}
$env:API_PORT = "$chosen"

# ---------------------------------------------------------------- 6. serve
$host_ = if ($env:API_HOST) { $env:API_HOST } else { '127.0.0.1' }
Write-Host ''
Say "Serving at http://$($host_):$chosen   (OpenAPI at /docs)  --  Ctrl+C to stop"
Write-Host ''
& $venvPython -m advanced_rag.api.main
