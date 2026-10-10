"""Injected failures in the answer path must terminate with usable output."""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from app.rag import answerer, llm_providers
from app.rag.retriever import Retrieved


PASSAGE = Retrieved(
    chunk_id=7, paper_id="paper-a", paper_title="Paper A", section="Methods",
    page=2, text="The paper uses attention to compare scientific findings across documents.",
    score=0.9,
)


class FakeQuery:
    def __init__(self, paper):
        self.paper = paper

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.paper


class FakeDB:
    def __init__(self, *, commit_error: bool = False):
        self.paper = SimpleNamespace(id="paper-a", title="Paper A", abstract="", meta={})
        self.added = []
        self.closed = False
        self.commit_error = commit_error

    def query(self, *args):
        return FakeQuery(self.paper)

    def add(self, row):
        self.added.append(row)

    def commit(self):
        if self.commit_error:
            raise RuntimeError("injected database commit failure")

    def refresh(self, row):
        row.id = 42

    def close(self):
        self.closed = True


def _events(raw):
    return [json.loads(item.removeprefix("data: ")) for item in raw]


def _visible_text(events):
    text = ""
    for event in events:
        if event["type"] == "fallback":
            text = ""
        elif event["type"] == "token":
            text += event["text"]
        elif event["type"] == "error":
            text = event["message"]
    return text


def _assert_terminal(events):
    assert events, "stream emitted no events"
    assert events[-1]["type"] == "done", "stream never finished"
    assert _visible_text(events).strip(), "user sees an empty answer"


@pytest.fixture
def stream_harness(monkeypatch):
    db = FakeDB()
    from app.security import session

    @contextmanager
    def fake_owner_write(connection, owner, **kwargs):
        # These tests isolate provider failures. Real lifecycle transactions
        # are exercised in test_session_isolation_regressions.py.
        yield
        connection.commit()

    monkeypatch.setattr(session, "owner_write", fake_owner_write)
    monkeypatch.setattr(answerer, "SessionLocal", lambda: db)
    monkeypatch.setattr(answerer, "_resolve_meta", lambda *a, **kw: (None, db.paper, None))
    monkeypatch.setattr(answerer, "_resolve_followup", lambda *a, **kw: (None, None))
    monkeypatch.setattr(answerer, "retrieve", lambda *a, **kw: [PASSAGE])
    monkeypatch.setattr(answerer, "_extractive", lambda *a, **kw: answerer.Answer(
        text="The paper uses attention to compare scientific findings.", evidence=[PASSAGE]))
    monkeypatch.setattr(answerer, "_recent_history", lambda *a, **kw: [])
    monkeypatch.setattr(answerer, "build_suggestions", lambda *a, **kw: [])
    monkeypatch.setattr(answerer, "llm_configured", lambda *a, **kw: True)
    from app.services.scholarly import common
    monkeypatch.setattr(common, "cache_get", lambda key: None)
    monkeypatch.setattr(common, "cache_set", lambda *a, **kw: None)
    flight = {"event": threading.Event()}
    monkeypatch.setattr(llm_providers, "claim_synthesis", lambda key: (True, flight))
    monkeypatch.setattr(llm_providers, "finish_synthesis", lambda *a, **kw: flight["event"].set())
    from app.services import usage
    monkeypatch.setattr(usage, "record_usage", lambda *a, **kw: None)
    return db


def _run_stream():
    return _events(answerer.stream_answer_events(
        "How does it compare findings?", paper_ids=["paper-a"],
        owner="owner-a", token="ss_test_token"))


@pytest.mark.parametrize("primary_failure", ["empty", "rate_limit"])
def test_empty_or_429_primary_stream_hands_off_to_backup(stream_harness, monkeypatch,
                                                          primary_failure):
    attempts = []

    def failing_primary():
        if primary_failure == "rate_limit":
            request = httpx.Request("POST", "https://example.invalid/model")
            response = httpx.Response(429, request=request, headers={"Retry-After": "2"})
            raise httpx.HTTPStatusError("rate limited", request=request, response=response)
        if False:
            yield "never"

    def backup():
        yield "The paper uses attention [1]."

    def choose(*args, exclude=None, **kwargs):
        attempts.append(set(exclude or []))
        if "gemini" not in (exclude or set()):
            return "gemini", failing_primary()
        if "groq" not in (exclude or set()):
            return "groq", backup()
        return None

    monkeypatch.setattr(llm_providers, "synthesize_stream", choose)
    events = _run_stream()
    _assert_terminal(events)
    assert "attention [1]" in _visible_text(events)
    assert events[-1]["mode"] == "synthesized:groq"
    assert len(attempts) == 2 and "gemini" in attempts[1]
    assert stream_harness.closed


def test_midstream_failure_preserves_cited_partial_answer(stream_harness, monkeypatch):
    def broken():
        yield "The paper uses attention [1]."
        raise RuntimeError("provider stream ended")

    monkeypatch.setattr(llm_providers, "synthesize_stream",
                        lambda *a, **kw: ("gemini", broken()))
    events = _run_stream()
    _assert_terminal(events)
    assert "attention [1]" in _visible_text(events)
    assert "stopped early" in _visible_text(events)
    assert any(event["type"] == "fallback" for event in events)


def test_all_providers_fail_returns_extractive_answer(stream_harness, monkeypatch):
    monkeypatch.setattr(llm_providers, "synthesize_stream", lambda *a, **kw: None)
    events = _run_stream()
    _assert_terminal(events)
    assert "paper uses attention" in _visible_text(events)
    assert events[-1]["mode"] == "extractive"


def test_cloud_failure_preserves_local_insufficient_evidence_flag(stream_harness, monkeypatch):
    monkeypatch.setattr(answerer, "_extractive", lambda *a, **kw: answerer.Answer(
        text="I couldn't find sufficient evidence for that question.",
        evidence=[PASSAGE], insufficient=True))
    monkeypatch.setattr(llm_providers, "synthesize_stream", lambda *a, **kw: None)
    events = _run_stream()
    _assert_terminal(events)
    assert events[-1]["insufficient"]


def test_retrieval_failure_emits_error_and_done(stream_harness, monkeypatch):
    monkeypatch.setattr(answerer, "retrieve", lambda *a, **kw: (_ for _ in ()).throw(
        RuntimeError("private database detail")))
    events = _run_stream()
    _assert_terminal(events)
    assert any(event["type"] == "error" for event in events)
    assert "private database detail" not in _visible_text(events)


def test_database_commit_failure_emits_error_and_done(stream_harness):
    stream_harness.commit_error = True
    events = _run_stream()
    _assert_terminal(events)
    assert any(event["type"] == "error" for event in events)


def test_database_open_failure_emits_error_and_done(stream_harness, monkeypatch):
    monkeypatch.setattr(answerer, "SessionLocal", lambda: (_ for _ in ()).throw(
        RuntimeError("database unavailable")))
    events = _run_stream()
    _assert_terminal(events)
    assert any(event["type"] == "error" for event in events)


def test_sync_cloud_answer_uses_provider_without_blank_response(stream_harness, monkeypatch):
    monkeypatch.setattr(llm_providers, "synthesize", lambda *a, **kw: (
        "The paper uses attention [1].", "gemini"))
    result = answerer.answer_question(
        "How does it compare findings?", paper_ids=["paper-a"],
        owner="owner-a", token="ss_test_token")
    assert result.text.strip() and "[1]" in result.text
    assert result.mode == "synthesized:gemini"


def test_no_evidence_stream_explains_gap_and_finishes(stream_harness, monkeypatch):
    monkeypatch.setattr(answerer, "retrieve", lambda *a, **kw: [])
    events = _run_stream()
    _assert_terminal(events)
    assert "sufficient evidence" in _visible_text(events)
    assert events[-1]["insufficient"]


def test_empty_deterministic_answer_cannot_finish_blank(stream_harness, monkeypatch):
    monkeypatch.setattr(answerer, "llm_configured", lambda *a, **kw: False)
    monkeypatch.setattr(answerer, "_extractive", lambda *a, **kw: answerer.Answer(
        text="  ", evidence=[PASSAGE]))
    events = _run_stream()
    _assert_terminal(events)
