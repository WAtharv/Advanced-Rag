"""Test isolation.

Settings normally read the project's `.env`, which would make the offline suite
depend on whatever the developer has configured locally. Detaching the env file
at import time (before any test module builds a Settings object) keeps the
defaults in `config.py` as the single source of truth for tests.
"""

from __future__ import annotations

import os

import pytest

from advanced_rag.config import Settings, get_settings

Settings.model_config["env_file"] = None

# Guardrail and graph tests must never reach the network. Placeholder keys let
# the clients construct without complaining; fakes/mocks intercept the calls,
# and anything that slips past fails loudly instead of billing.
os.environ.setdefault("GROQ_API_KEY", "gsk-test-placeholder")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-placeholder")


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """`get_settings` is lru_cached; drop it between tests that patch settings."""
    from advanced_rag.llm.client import reset_llm

    get_settings.cache_clear()
    # The client and its availability probe are both latched module state. A test
    # that sets LLM_PROVIDER=offline would otherwise leak that verdict into every
    # test that ran after it.
    reset_llm()
    yield
    get_settings.cache_clear()
    reset_llm()
