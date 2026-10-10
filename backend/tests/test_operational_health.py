"""Uptime checks expose readiness without revealing secrets or private data."""
from __future__ import annotations

from app.api.routes import system_health


def test_health_exposes_database_index_and_key_presence():
    status = system_health()
    assert status["status"] in {"ok", "degraded"}
    assert isinstance(status["database_reachable"], bool)
    assert isinstance(status["index_consistent"], bool)
    assert isinstance(status["index_chunk_count"], int)
    assert set(status["provider_keys_present"]) == {"gemini", "groq", "openai"}
    assert all(isinstance(v, bool) for v in status["provider_keys_present"].values())
