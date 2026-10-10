"""Tests for the LLM provider chain (no network calls — offline logic only)."""
from __future__ import annotations

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def isolated_provider_state(monkeypatch):
    import app.rag.llm_providers as llm
    for field in ("_cooldowns", "_hourly_calls", "_owner_hourly_calls", "_flights"):
        monkeypatch.setattr(llm, field, {})


def _configure_llm(monkeypatch, **env):
    """Patch the shared settings object without invalidating other modules."""
    import app.rag.llm_providers as llm
    values = {"GEMINI_API_KEY": "", "GROQ_API_KEY": "", "OPENAI_API_KEY": "",
              "LLM_PROVIDER_CHAIN": "gemini,groq,openai", **env}
    for key, value in values.items():
        monkeypatch.setattr(llm.settings, key.lower(), value)
    monkeypatch.setattr(llm.settings, "cloud_ai_enabled", True)
    monkeypatch.setattr(llm.settings, "production", False)
    return llm


def test_chain_empty_without_keys(monkeypatch):
    llm = _configure_llm(monkeypatch, GEMINI_API_KEY="", GROQ_API_KEY="", OPENAI_API_KEY="")
    assert llm.build_chain() == []
    assert llm.llm_configured() is False


def test_chain_gemini_and_groq_configured(monkeypatch):
    llm = _configure_llm(monkeypatch, GEMINI_API_KEY="g", GROQ_API_KEY="q", OPENAI_API_KEY="")
    assert llm.build_chain() == ["gemini", "groq"]


def test_chain_respects_custom_order(monkeypatch):
    llm = _configure_llm(monkeypatch, GEMINI_API_KEY="g", GROQ_API_KEY="q",
                      LLM_PROVIDER_CHAIN="groq,gemini")
    assert llm.build_chain() == ["groq", "gemini"]


def test_chain_dedupes_and_ignores_unknown(monkeypatch):
    llm = _configure_llm(monkeypatch, GEMINI_API_KEY="g", LLM_PROVIDER_CHAIN="gemini,anthropic,gemini")
    assert llm.build_chain() == ["gemini"]


def test_synthesize_returns_none_without_providers(monkeypatch):
    from app.rag.retriever import Retrieved
    llm = _configure_llm(monkeypatch, GEMINI_API_KEY="", GROQ_API_KEY="", OPENAI_API_KEY="")
    passage = Retrieved(0, "p1", "Title", "abstract", 1, "some text", 0.9)
    assert llm.synthesize("q?", [passage], "Title") is None


def test_synthesize_returns_none_without_passages(monkeypatch):
    llm = _configure_llm(monkeypatch, GEMINI_API_KEY="g")
    assert llm.synthesize("q?", [], "Title") is None


def test_invalid_citations_fall_back_to_next_provider(monkeypatch):
    from app.rag.retriever import Retrieved
    from app.services.scholarly import common
    from app.security import session

    llm = _configure_llm(monkeypatch, GEMINI_API_KEY="g", GROQ_API_KEY="q",
                      OPENAI_API_KEY="", LLM_PROVIDER_CHAIN="gemini,groq")
    monkeypatch.setattr(session, "cloud_ai_allowed", lambda owner: True)
    passage = Retrieved(7, "p1", "Title", "methods", 2, "The method uses attention.", 0.9)
    monkeypatch.setattr(common, "cache_get", lambda key: None)
    saved = []
    monkeypatch.setattr(common, "cache_set", lambda key, value, hours: saved.append(key))
    monkeypatch.setattr(llm, "_gemini", lambda q, c: "Unsupported claim [9].")
    monkeypatch.setattr(llm, "_openai_compatible",
                        lambda *args, **kwargs: "The method uses attention [1].")

    answer = llm.synthesize("What method?", [passage], "Title", owner="owner-1")
    assert answer == ("The method uses attention [1].", "groq")
    assert saved and saved[0].startswith("synth::owner-1::")


def test_generated_answer_requires_real_evidence_card_ids():
    from app.rag.llm_providers import valid_evidence_citations

    assert valid_evidence_citations("It uses attention [1].", 2)
    assert not valid_evidence_citations("It uses attention [3].", 2)
    assert not valid_evidence_citations("It uses attention.", 2)
