"""Content-free, owner-scoped estimates of provider token use.

Providers differ in tokenization and may bill hidden reasoning tokens. These
counts are explicit estimates, never presented as a provider invoice.
"""
from __future__ import annotations

import json
import logging
import math
import os

from sqlalchemy import func

from app.database.database import SessionLocal
from app.models.entities import ProviderUsage

log = logging.getLogger("scholarscout.usage")


def _tokens(text: str) -> int:
    return max(1, math.ceil(len(text or "") / 4))


def _price(provider: str, direction: str) -> float | None:
    raw = os.getenv(f"{provider.upper()}_{direction.upper()}_USD_PER_M", "")
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def record_usage(owner: str, provider: str, model: str,
                 input_text: str, output_text: str) -> None:
    """Record counts only; errors must not replace a valid answer."""
    if not owner:
        return
    inp, out = _tokens(input_text), _tokens(output_text)
    in_price, out_price = _price(provider, "input"), _price(provider, "output")
    cost = None if in_price is None or out_price is None else (
        inp * in_price + out * out_price) / 1_000_000
    try:
        from app.security.session import SessionEnded, owner_write
        with SessionLocal() as db:
            with owner_write(db, owner):
                db.add(ProviderUsage(owner=owner, provider=provider, model=model,
                                     input_tokens_estimate=inp,
                                     output_tokens_estimate=out,
                                     cost_usd_estimate=cost))
        log.info(json.dumps({"event": "provider_usage", "owner_prefix": owner[:8],
                             "provider": provider, "model": model,
                             "input_tokens_estimate": inp,
                             "output_tokens_estimate": out,
                             "cost_usd_estimate": cost}))
    except SessionEnded:
        return
    except Exception as exc:
        log.error("provider usage recording failed (%s)", type(exc).__name__)


def usage_report(owner: str) -> dict:
    with SessionLocal() as db:
        rows = db.query(
            ProviderUsage.provider,
            func.count(ProviderUsage.id),
            func.coalesce(func.sum(ProviderUsage.input_tokens_estimate), 0),
            func.coalesce(func.sum(ProviderUsage.output_tokens_estimate), 0),
            func.sum(ProviderUsage.cost_usd_estimate),
            func.count(ProviderUsage.cost_usd_estimate),
        ).filter(ProviderUsage.owner == owner).group_by(ProviderUsage.provider).all()
    by_provider = [{
        "provider": provider, "calls": int(count),
        "input_tokens_estimate": int(inp), "output_tokens_estimate": int(out),
        "cost_usd_estimate": round(float(cost), 6) if priced == count else None,
    } for provider, count, inp, out, cost, priced in rows]
    total_calls = sum(p["calls"] for p in by_provider)
    known_cost = all(p["cost_usd_estimate"] is not None for p in by_provider)
    return {
        "provider_calls": total_calls,
        "input_tokens_estimate": sum(p["input_tokens_estimate"] for p in by_provider),
        "output_tokens_estimate": sum(p["output_tokens_estimate"] for p in by_provider),
        "cost_usd_estimate": round(sum(p["cost_usd_estimate"] for p in by_provider), 6)
        if known_cost and total_calls else None,
        "by_provider": by_provider,
        "note": "Approximate tokens; hidden reasoning and provider billing can differ. "
                "Set each provider's INPUT_USD_PER_M and OUTPUT_USD_PER_M to estimate USD.",
    }
