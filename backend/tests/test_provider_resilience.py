"""Fault cases that used to waste provider calls or reject usable answers."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

from app.rag import llm_providers as llm
from app.rag.retriever import Retrieved


PASSAGE = Retrieved(17, "paper", "Title", "Methods", 2,
                    "The team used a strategy with three validation stages.", 0.9)


def test_identical_concurrent_questions_share_one_provider_call(monkeypatch):
    from app.security import session
    from app.services.scholarly import common

    monkeypatch.setattr(session, "cloud_ai_allowed", lambda owner: True)
    monkeypatch.setattr(llm.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(llm.settings, "groq_api_key", "")
    monkeypatch.setattr(llm.settings, "openai_api_key", "")
    monkeypatch.setattr(llm.settings, "llm_provider_chain", "gemini")
    cache = {}
    monkeypatch.setattr(common, "cache_get", lambda key: cache.get(key))
    monkeypatch.setattr(common, "cache_set", lambda key, value, hours=6: cache.__setitem__(key, value))
    calls = 0
    gate = threading.Barrier(2)

    def provider(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        time.sleep(0.15)
        return "The strategy has three validation stages [1]."

    monkeypatch.setattr(llm, "_gemini", provider)

    def ask():
        gate.wait()
        return llm.synthesize("What strategy?", [PASSAGE], "Title", owner="a" * 32)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: ask(), range(2)))
    assert calls == 1
    assert results[0][0] == results[1][0]
    assert {r[1] for r in results} == {"gemini", "cache"}


def test_cache_key_is_independent_of_unrelated_history():
    a = llm._cache_key("What strategy?", [PASSAGE], owner="a" * 32)
    b = llm._cache_key("  WHAT   STRATEGY? ", [PASSAGE], owner="a" * 32)
    assert a == b


def test_citation_format_repair_preserves_supported_answer():
    repaired = llm.repair_evidence_citations(
        "It uses three stages 【1】 and further tests [Abstract] [1, 1].", 1)
    assert repaired == "It uses three stages [1] and further tests [1]."
    assert llm.valid_evidence_citations(repaired, 1)


def test_invalid_card_number_cannot_be_reassigned_to_another_claim():
    repaired = llm.repair_evidence_citations("Supported [1]. Unsupported [9].", 1)
    assert not llm.valid_evidence_citations(repaired, 1)


def test_each_generated_claim_line_needs_its_own_evidence_card():
    assert not llm.valid_evidence_citations(
        "The first result is supported [1].\nThe second result has no source.", 1)
    assert llm.valid_evidence_citations(
        "The first result is supported [1].\n- The second result is supported [1].", 1)
    assert llm.valid_evidence_citations(
        "I couldn't find sufficient evidence to answer this confidently.", 1)
    assert not llm.valid_evidence_citations(
        "I couldn't find sufficient evidence to answer this confidently. Also, it doubled.", 1)


def test_synthesis_prompt_covers_nonacademic_documents_and_cites_each_line():
    assert "research document" in llm.SYSTEM_PROMPT
    assert "every factual sentence and bullet" in llm.SYSTEM_PROMPT
    assert "academic paper" not in llm.SYSTEM_PROMPT


def test_retry_after_429_moves_primary_out_of_chain(monkeypatch):
    import httpx
    from app.security import session
    from app.services.scholarly import common

    monkeypatch.setattr(session, "cloud_ai_allowed", lambda owner: True)
    monkeypatch.setattr(llm.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(llm.settings, "groq_api_key", "test-key")
    monkeypatch.setattr(llm.settings, "openai_api_key", "")
    monkeypatch.setattr(llm.settings, "llm_provider_chain", "gemini,groq")
    monkeypatch.setattr(common, "cache_get", lambda key: None)
    monkeypatch.setattr(common, "cache_set", lambda *args, **kwargs: None)
    llm._cooldowns.clear()
    request = httpx.Request("POST", "https://example.invalid")
    response = httpx.Response(429, headers={"Retry-After": "60"}, request=request)
    def throttled(*_args, **_kwargs):
        raise httpx.HTTPStatusError("rate limited", request=request, response=response)
    monkeypatch.setattr(llm, "_gemini", throttled)
    monkeypatch.setattr(llm, "_openai_compatible",
                        lambda *args, **kwargs: "The strategy has three validation stages [1].")
    try:
        assert llm.synthesize("What strategy?", [PASSAGE], "Title", owner="b" * 32)
        assert llm.build_chain("b" * 32) == ["groq"]
    finally:
        llm._cooldowns.clear()


def test_repeatedly_failing_provider_is_skipped_during_cooldown(monkeypatch):
    import time
    from app.security import session

    monkeypatch.setattr(session, "cloud_ai_allowed", lambda owner: True)
    monkeypatch.setattr(llm.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(llm.settings, "groq_api_key", "test-key")
    monkeypatch.setattr(llm.settings, "openai_api_key", "")
    monkeypatch.setattr(llm.settings, "llm_provider_chain", "gemini,groq")
    llm._cooldowns.clear()
    llm._cooldowns["gemini"] = {"fails": [time.time()] * 3}
    try:
        assert llm.build_chain("b" * 32) == ["groq"]
    finally:
        llm._cooldowns.clear()


def test_gemini_sse_yields_successful_text_delta(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            pass

        def iter_lines(self):
            yield 'data: {"candidates":[{"content":{"parts":[{"text":"Grounded 【1】."}]}}]}'

    monkeypatch.setattr(llm.httpx, "stream", lambda *_args, **_kwargs: Response())
    assert list(llm._gemini_stream("Question", "Evidence")) == ["Grounded [1]."]


def test_stream_repeat_uses_persisted_cache(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.database import Base
    from app.models.entities import ApiCache, ChatMessage, OwnerSession, Paper
    from app.rag import answerer
    from app.services import usage
    from app.services.scholarly import common

    engine = create_engine(f"sqlite:///{(tmp_path / 'stream-cache.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, autoflush=False)
    with factory() as db:
        db.add(OwnerSession(owner="c" * 32, cloud_ai_opt_in=True))
        db.add(Paper(id="paper", owner="c" * 32, title="Title", status="ready"))
        db.commit()
    for module in (answerer, usage, common):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(answerer, "llm_configured", lambda owner: True)
    monkeypatch.setattr(answerer, "_resolve_meta", lambda *args, **kwargs:
                        (None, SimpleNamespace(id="paper", abstract=""), None))
    monkeypatch.setattr(answerer, "retrieve", lambda *args, **kwargs: [PASSAGE])
    monkeypatch.setattr(answerer, "_recent_history", lambda *args, **kwargs: [])
    monkeypatch.setattr(answerer, "build_suggestions", lambda *args, **kwargs: [])
    calls = 0

    def cloud(*args, **kwargs):
        nonlocal calls
        calls += 1
        return "gemini", iter(["The strategy has three validation stages [1]."])

    monkeypatch.setattr(llm, "synthesize_stream", cloud)

    def ask():
        frames = list(answerer.stream_answer_events("What strategy?", ["paper"],
                                                    owner="c" * 32, token="ss_test"))
        return [json.loads(frame.removeprefix("data: ")) for frame in frames]

    first, second = ask(), ask()
    assert calls == 1
    assert first[-1]["mode"] == "synthesized:gemini"
    assert second[-1]["mode"] == "synthesized:cache"
    assert second[-1]["cached"] is True
    with factory() as db:
        assert db.query(ApiCache).count() == 1
        assert db.query(ChatMessage).count() == 4
    engine.dispose()
