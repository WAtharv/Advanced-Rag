#!/usr/bin/env python3
"""List available Gemini models."""

import os
from dotenv import load_dotenv
load_dotenv()

try:
    from google import genai

    gemini_api_key = os.getenv("GEMINI_API_KEY")
    if not gemini_api_key:
        print("✗ GEMINI_API_KEY not found")
        exit(1)

    client = genai.Client(api_key=gemini_api_key)

    print("Available Gemini models:")
    print("-" * 60)

    models = client.models.list()
    for model in models:
        print(f"  • {model.name}")
        if hasattr(model, 'supported_generation_methods'):
            print(f"    Methods: {', '.join(model.supported_generation_methods)}")

except Exception as e:
    print(f"Error: {e}")
