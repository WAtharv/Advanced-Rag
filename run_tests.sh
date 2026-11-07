#!/bin/bash
# Quick test script for Advanced RAG system
# Usage: ./run_tests.sh [step_number]
# If no step specified, runs all tests in sequence

set -e

STEP=${1:-all}

echo "=================================================="
echo "Advanced RAG System - Test Runner"
echo "=================================================="
echo ""

run_step_1() {
    echo "=== Step 1: Pre-flight Checks ==="
    python3 --version
    echo ""

    if [ -f .env ]; then
        echo "✓ .env file exists"
        if grep -q "GEMINI_API_KEY=.*[A-Za-z0-9]" .env; then
            echo "✓ GEMINI_API_KEY is set"
        else
            echo "⚠ GEMINI_API_KEY appears empty"
        fi
    else
        echo "✗ .env file not found"
        exit 1
    fi

    echo ""
    echo "Corpus files:"
    ls -1 data/corpus/ | wc -l
    echo ""
}

run_step_2() {
    echo "=== Step 2: Test Gemini API Connection ==="
    python3 test_gemini.py
    echo ""
}

run_step_3() {
    echo "=== Step 3: Test LLM Client Integration ==="
    . venv/bin/activate
    python3 -c "
from advanced_rag.llm.client import get_llm
print('Testing LLM client...')
llm = get_llm()
result = llm.complete('Say hello in 3 words')
print(f'✓ LLM Client works: {result.text}')
print(f'  Model: {result.model}')
print(f'  Tokens: in={result.input_tokens} out={result.output_tokens}')
"
    echo ""
}

run_step_4() {
    echo "=== Step 4: Run Offline Test Suite ==="
    . venv/bin/activate

    echo "Running pytest..."
    pytest -v --tb=short --durations=10

    echo ""
    echo "✓ Test suite completed"
    echo ""
}

run_step_5() {
    echo "=== Step 5: Ingest Corpus and Seed Database ==="
    . venv/bin/activate

    # Dry run first
    echo "Running dry-run (chunking only)..."
    python3 -m advanced_rag.ingestion.cli --dry-run

    echo ""
    echo "Running full ingestion..."
    python3 -m advanced_rag.ingestion.cli --seed-sql --recreate

    echo ""
    echo "Verifying ingestion..."
    if [ -d data/qdrant ]; then
        echo "✓ Qdrant data created"
        ls -lh data/qdrant/ | head -5
    else
        echo "⚠ Qdrant data directory not found"
    fi

    if [ -f data/ops.db ]; then
        echo "✓ SQLite database created"
        sqlite3 data/ops.db "SELECT COUNT(*) as incident_count FROM incidents;" 2>/dev/null || echo "  (table check skipped)"
    else
        echo "⚠ SQLite database not found"
    fi
    echo ""
}

run_step_6() {
    echo "=== Step 6: Run Retrieval Evaluation ==="
    . venv/bin/activate

    python3 -m advanced_rag.evaluation.runner --retrieval

    echo ""
    echo "Results saved to: eval_results/"
    ls -lt eval_results/ | head -3
    echo ""
}

run_step_7() {
    echo "=== Step 7: Start API Service ==="
    echo ""
    echo "Starting API server in background..."
    echo "(Press Ctrl+C to stop after testing)"
    echo ""

    . venv/bin/activate
    python3 -m advanced_rag.api.main &
    API_PID=$!

    echo "API PID: $API_PID"
    echo "Waiting for API to start..."
    sleep 5

    echo ""
    echo "You can now run Step 8 in another terminal:"
    echo "  ./run_tests.sh 8"
    echo ""
    echo "Or test manually:"
    echo "  curl -s localhost:8000/health | jq"
    echo ""

    # Keep running
    wait $API_PID
}

run_step_8() {
    echo "=== Step 8: Test API Endpoints ==="

    echo "1. Health check..."
    curl -s localhost:8000/health | jq -C '.' || echo "⚠ API not responding"

    echo ""
    echo "2. Test retrieval only..."
    curl -s localhost:8000/retrieve \
      -H 'content-type: application/json' \
      -d '{"query":"exit code 137","mode":"hybrid","fusion":"weighted","rerank":true}' \
      | jq -C '.results[0] | {source, retrieval_score, rerank_score, text: .text[:100]}' 2>/dev/null || echo "⚠ Retrieval failed"

    echo ""
    echo "3. Test documentation question..."
    curl -s localhost:8000/ask \
      -H 'content-type: application/json' \
      -d '{"question":"Why did my pod exit with code 137?"}' \
      | jq -C '{answer: .answer[:200], citations: .citations | length, cached: .cached}' 2>/dev/null || echo "⚠ Ask failed"

    echo ""
    echo "4. Test cache hit (repeat question)..."
    curl -s localhost:8000/ask \
      -H 'content-type: application/json' \
      -d '{"question":"Why did my pod exit with code 137?"}' \
      | jq -C '{cached: .cached, answer: .answer[:100]}' 2>/dev/null || echo "⚠ Cache test failed"

    echo ""
    echo "5. Test SQL question (should await approval)..."
    curl -s localhost:8000/ask \
      -H 'content-type: application/json' \
      -d '{"question":"How many sev1 incidents since June 2026?"}' \
      | jq -C '{awaiting_approval, sql: .sql.sql, thread_id}' 2>/dev/null || echo "⚠ SQL test failed"

    echo ""
    echo "✓ API endpoint tests completed"
    echo ""
}

# Main execution
case $STEP in
    1)
        run_step_1
        ;;
    2)
        run_step_2
        ;;
    3)
        run_step_3
        ;;
    4)
        run_step_4
        ;;
    5)
        run_step_5
        ;;
    6)
        run_step_6
        ;;
    7)
        run_step_7
        ;;
    8)
        run_step_8
        ;;
    all)
        run_step_1
        run_step_2
        run_step_3
        run_step_4
        run_step_5
        run_step_6
        echo ""
        echo "=================================================="
        echo "Automated tests completed!"
        echo ""
        echo "To test the API service, run in separate terminals:"
        echo "  Terminal 1: ./run_tests.sh 7  (starts API)"
        echo "  Terminal 2: ./run_tests.sh 8  (tests endpoints)"
        echo "=================================================="
        ;;
    *)
        echo "Usage: $0 [step_number]"
        echo ""
        echo "Available steps:"
        echo "  1  - Pre-flight checks"
        echo "  2  - Test Gemini API"
        echo "  3  - Test LLM client"
        echo "  4  - Run test suite"
        echo "  5  - Ingest corpus"
        echo "  6  - Retrieval evaluation"
        echo "  7  - Start API server"
        echo "  8  - Test API endpoints"
        echo "  all - Run steps 1-6 (default)"
        echo ""
        exit 1
        ;;
esac

echo "Done!"
