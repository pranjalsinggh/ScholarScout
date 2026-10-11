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


@pytest.mark.parametrize("provider", ["gemini", "groq", "openai"])
def test_stream_payload_matches_classic_bounded_history_and_evidence(monkeypatch, provider):
    """Exercise real adapters with fake HTTP; history must reach each payload."""
    import json

    from app.rag.retriever import Retrieved
    from app.security import session
    from app.services import usage
    from app.services.scholarly import common

    llm = _configure_llm(monkeypatch, **{
        provider.upper() + "_API_KEY": "offline-test-key", "LLM_PROVIDER_CHAIN": provider})
    monkeypatch.setattr(session, "cloud_ai_allowed", lambda owner: True)
    monkeypatch.setattr(common, "cache_get", lambda key: None)
    monkeypatch.setattr(common, "cache_set", lambda *a, **kw: None)
    monkeypatch.setattr(usage, "record_usage", lambda *a, **kw: None)
    captured = []
    answer = "Calibration uses a quantum reference [1]."

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            if provider == "gemini":
                return {"candidates": [{"content": {"parts": [{"text": answer}]}}]}
            return {"choices": [{"message": {"content": answer}}]}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_lines(self):
            data = (self.json() if provider == "gemini" else
                    {"choices": [{"delta": {"content": answer}}]})
            yield "data: " + json.dumps(data)
            if provider != "gemini":
                yield "data: [DONE]"

    def fake_request(*args, **kwargs):
        captured.append(kwargs["json"])
        return Response()

    monkeypatch.setattr(llm.httpx, "post", fake_request)
    monkeypatch.setattr(llm.httpx, "stream", fake_request)
    passages = [Retrieved(7, "paper", "Quantum calibration", "Methods", 2,
                          "Calibration uses a stable quantum reference.", 0.9)]
    history = ["User: old turn must be excluded", "User: compare calibration",
               "Assistant: the reference controls the comparison"]
    arguments = {"abstract": "a" * 1300, "history": history, "owner": "a" * 32,
                 "style_hint": "Use simple language."}
    assert llm.synthesize("What changes?", passages, "Quantum calibration", **arguments) == (answer, provider)
    stream = llm.synthesize_stream("What changes?", passages, **arguments)
    assert stream and stream[0] == provider and "".join(stream[1]) == answer
    assert len(captured) == 2
    if provider == "gemini":
        prompts = [payload["contents"][0]["parts"][0]["text"] for payload in captured]
        systems = [payload["system_instruction"]["parts"][0]["text"] for payload in captured]
    else:
        prompts = [payload["messages"][1]["content"] for payload in captured]
        systems = [payload["messages"][0]["content"] for payload in captured]
    assert prompts[0] == prompts[1]
    assert "Recent conversation so far:" in prompts[1]
    assert all(turn in prompts[1] for turn in history[-2:])
    assert history[0] not in prompts[1]
    assert "a" * 1200 in prompts[1] and "a" * 1201 not in prompts[1]
    assert "[1] (paper:" in prompts[1] and passages[0].text in prompts[1]
    assert "Question: What changes?" in prompts[1] and "Use simple language." in prompts[1]
    assert systems == [llm.SYSTEM_PROMPT] * 2
    assert "untrusted source material" in systems[1]
