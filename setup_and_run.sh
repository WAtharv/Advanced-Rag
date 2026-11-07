#!/bin/bash
# Setup and run script for the Advanced RAG system with Gemini

set -e

echo "=================================================="
echo "Advanced RAG Setup with Gemini API"
echo "=================================================="
echo ""

# Check Python version
echo "Checking Python version..."
python3 --version

# Install google-generativeai
echo ""
echo "Installing google-generativeai package..."
if python3 -m pip install --user google-generativeai; then
    echo "✓ google-generativeai installed successfully"
else
    echo "⚠ Installation had issues, but continuing..."
fi

# Install project dependencies
echo ""
echo "Installing project dependencies..."
if python3 -m pip install --user -e ".[dev]"; then
    echo "✓ Project dependencies installed"
else
    echo "⚠ Some dependencies may have issues"
fi

# Test Gemini connection
echo ""
echo "Testing Gemini API connection..."
python3 test_gemini.py

# Ingest data
echo ""
echo "=================================================="
echo "Ingesting data into the RAG system..."
echo "=================================================="
python3 -m advanced_rag.ingestion.cli --seed-sql

# Start the API server
echo ""
echo "=================================================="
echo "Starting the RAG API server..."
echo "=================================================="
echo "The API will be available at: http://127.0.0.1:8000"
echo "API docs available at: http://127.0.0.1:8000/docs"
echo ""
echo "In another terminal, you can:"
echo "  - Run the Streamlit UI: streamlit run ui/streamlit_app.py"
echo "  - Test the API: curl -s localhost:8000/ask -H 'content-type: application/json' -d '{\"question\":\"Why did my pod exit with code 137?\"}'"
echo ""
python3 -m advanced_rag.api.main
