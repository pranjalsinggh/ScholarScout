"""Fail CI if the anonymous Render release drifts into unsafe deployment settings."""
from __future__ import annotations

from pathlib import Path
import argparse

import yaml


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Export non-secret Blueprint values for container verification")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    blueprint = yaml.safe_load((root / "render.yaml").read_text())
    assert set(blueprint) == {"services"}, "No persistent database or shared environment group"
    assert len(blueprint["services"]) == 1, "Anonymous state requires one service"
    service = blueprint["services"][0]
    assert service["runtime"] == "docker" and service["type"] == "web"
    assert service["numInstances"] == 1 and "scaling" not in service
    assert "disk" not in service, "Anonymous research must not use a persistent disk"
    assert service["healthCheckPath"] == "/api/health"
    assert service["autoDeployTrigger"] == "checksPass"
    assert service["plan"] == "free", "This release must not create a paid Render service"
    values = {item["key"]: item for item in service["envVars"]}
    assert values["SCHOLARSCOUT_SECRET_KEY"].get("generateValue") is True
    for key, value in {"APP_ENV": "production", "SERVE_FRONTEND": "1",
                       "CLOUD_AI_ENABLED": "0", "CLOUD_PROVIDER_DATA_APPROVED": "0",
                       "TRUST_RENDER_EDGE": "1",
                       "WEB_CONCURRENCY": "1", "SCHOLARSCOUT_DATA_DIR": "/tmp/scholarscout"}.items():
        assert values[key].get("value") == value, key
    for key in ("ANALYSIS_WORKERS", "MAX_CONCURRENT_INGESTIONS", "INDEX_BUILD_WORKERS"):
        assert values[key].get("value") == "1", f"{key}: serialize expensive work on free compute"
    limits = {
        "MAX_UPLOAD_MB": 10, "MAX_REQUEST_MB": 12, "MAX_PDF_PAGES": 80,
        "MAX_PDF_TEXT_CHARS": 400000, "MAX_PDF_CHUNKS": 800,
        "MAX_SESSION_CHUNKS": 1600, "MAX_SESSION_PAPERS": 3,
        "MAX_SESSION_STORAGE_MB": 30, "MAX_TOTAL_STORAGE_MB": 100,
        "MAX_TOTAL_EXTRACTED_STORAGE_MB": 20, "PDF_WORKER_MEMORY_MB": 192,
        "MAX_PENDING_ANALYSES": 4, "MAX_PENDING_ANALYSES_PER_OWNER": 1,
        "MAX_CONCURRENT_REQUESTS": 8, "MAX_CONCURRENT_REQUESTS_PER_OWNER": 2,
        "MAX_ACTIVE_SESSIONS": 50, "INDEX_CACHE_MB": 32, "INDEX_CACHE_OWNERS": 4,
        "TFIDF_WORD_MAX_FEATURES": 20000, "TFIDF_CHAR_MAX_FEATURES": 25000,
        "OPENALEX_MAX_CITING": 40,
    }
    for key, maximum in limits.items():
        assert 0 < int(values[key]["value"]) <= maximum, f"{key}: exceeds free-profile budget"
    assert 25 <= int(values["PDF_PARSE_TIMEOUT_SECONDS"]["value"]) <= 120
    assert not any(item["key"].endswith("API_KEY") and "value" in item for item in service["envVars"])
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    assert "volumes" not in compose
    for service in compose["services"].values():
        assert service["read_only"] is True and service["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in service["security_opt"]
    if args.env_file:
        pairs = []
        for key, item in values.items():
            if "value" not in item:
                continue
            value = str(item["value"])
            assert "\n" not in value and "\r" not in value
            pairs.append(f"{key}={value}")
        args.env_file.write_text("\n".join(pairs) + "\n", encoding="utf-8")
    print("Deployment invariants passed: ephemeral data, secret injection, bounded single service")


if __name__ == "__main__":
    main()
