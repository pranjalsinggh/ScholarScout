"""Token estimates are owner-scoped and erased with an anonymous session."""
from __future__ import annotations

import uuid

from app.database.database import Base, SessionLocal, engine
from app.security.session import purge_owner, touch_session


def test_usage_report_is_private_and_purgeable():
    from app.services.usage import record_usage, usage_report

    Base.metadata.create_all(bind=engine)
    owner = uuid.uuid4().hex
    other = uuid.uuid4().hex
    touch_session(owner)
    record_usage(owner, "gemini", "gemini-test", "a" * 400, "b" * 80)
    mine = usage_report(owner)
    theirs = usage_report(other)
    assert mine["provider_calls"] == 1
    assert mine["input_tokens_estimate"] >= 100
    assert mine["output_tokens_estimate"] >= 20
    assert theirs["provider_calls"] == 0

    with SessionLocal() as db:
        purge_owner(db, owner, rebuild=False)
    assert usage_report(owner)["provider_calls"] == 0
