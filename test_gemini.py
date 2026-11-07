#!/usr/bin/env python3
"""Quick test script to verify Gemini API integration."""

import os
import sys

# Check if google-genai is installed
try:
    from google import genai
    print("✓ google-genai package is installed")
except ImportError:
    print("✗ google-genai package is NOT installed")
    print("  Install it with: pip install google-genai")
    sys.exit(1)

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Check API key
gemini_api_key = os.getenv("GEMINI_API_KEY")
if not gemini_api_key:
    print("✗ GEMINI_API_KEY not found in environment")
    sys.exit(1)

print(f"✓ GEMINI_API_KEY found (length: {len(gemini_api_key)} chars)")

# Test Gemini API
try:
    client = genai.Client(api_key=gemini_api_key)

    print("\n🔄 Testing Gemini API with a simple prompt...")
    response = client.models.generate_content(
        model='gemini-3.6-flash',
        contents="Say 'Hello from Gemini!' in exactly those words."
    )

    print(f"\n✓ Gemini API call successful!")
    print(f"Response: {response.text}")

    # Show token usage
    if hasattr(response, 'usage_metadata'):
        print(f"\nToken usage:")
        print(f"  Input tokens: {response.usage_metadata.prompt_token_count}")
        print(f"  Output tokens: {response.usage_metadata.candidates_token_count}")

except Exception as e:
    print(f"\n✗ Gemini API call failed: {e}")
    sys.exit(1)

print("\n✅ All checks passed! Gemini integration is working.")
print("\nNow test the RAG system with:")
print("  python -c 'from advanced_rag.llm.client import get_llm; llm = get_llm(); print(llm.complete(\"Hello!\"))'")
