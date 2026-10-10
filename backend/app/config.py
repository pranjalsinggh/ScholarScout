"""Central configuration for ScholarScout.

All secrets come from environment variables (never committed). External
providers are optional - the app degrades gracefully without them.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")

DATA_DIR = Path(os.getenv("SCHOLARSCOUT_DATA_DIR", str(BACKEND_DIR / "data"))).expanduser().resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
INDEX_DIR = DATA_DIR / "index"
DB_PATH = DATA_DIR / "scholarscout.db"

for _d in (DATA_DIR, UPLOAD_DIR, INDEX_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH.as_posix()}")


class Settings:
    """Runtime settings with safe defaults."""

    app_name: str = "ScholarScout"
    api_prefix: str = "/api"

    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "60"))
    allowed_extensions: tuple[str, ...] = (".pdf",)
    max_request_mb: int = max(1, int(os.getenv("MAX_REQUEST_MB", "65")))
    max_pdf_pages: int = max(1, int(os.getenv("MAX_PDF_PAGES", "300")))
    max_pdf_text_chars: int = max(1000, int(os.getenv("MAX_PDF_TEXT_CHARS", "1500000")))
    max_pdf_chunks: int = max(10, int(os.getenv("MAX_PDF_CHUNKS", "3000")))
    max_session_chunks: int = max(10, int(os.getenv("MAX_SESSION_CHUNKS", "10000")))
    pdf_parse_timeout_seconds: int = max(1, int(os.getenv("PDF_PARSE_TIMEOUT_SECONDS", "25")))
    pdf_worker_memory_mb: int = max(128, int(os.getenv("PDF_WORKER_MEMORY_MB", "512")))
    analysis_workers: int = max(1, min(4, int(os.getenv("ANALYSIS_WORKERS", "2"))))
    max_pending_analyses: int = max(2, int(os.getenv("MAX_PENDING_ANALYSES", "12")))
    max_pending_analyses_per_owner: int = max(1, int(os.getenv("MAX_PENDING_ANALYSES_PER_OWNER", "2")))
    max_session_papers: int = max(1, int(os.getenv("MAX_SESSION_PAPERS", "10")))
    max_session_storage_mb: int = max(1, int(os.getenv("MAX_SESSION_STORAGE_MB", "150")))
    max_total_storage_mb: int = max(1, int(os.getenv("MAX_TOTAL_STORAGE_MB", "1000")))
    max_total_extracted_storage_mb: int = max(1, int(os.getenv("MAX_TOTAL_EXTRACTED_STORAGE_MB", "150")))
    max_concurrent_ingestions: int = max(1, int(os.getenv("MAX_CONCURRENT_INGESTIONS", "2")))
    max_active_sessions: int = max(2, int(os.getenv("MAX_ACTIVE_SESSIONS", "1000")))
    max_new_sessions_per_ip_hour: int = max(1, int(os.getenv("MAX_NEW_SESSIONS_PER_IP_HOUR", "30")))
    max_concurrent_requests: int = max(4, int(os.getenv("MAX_CONCURRENT_REQUESTS", "24")))
    max_concurrent_requests_per_owner: int = max(1, int(os.getenv("MAX_CONCURRENT_REQUESTS_PER_OWNER", "4")))
    production: bool = os.getenv("APP_ENV", "development").lower() == "production"
    allowed_hosts: str = os.getenv("ALLOWED_HOSTS", "")
    trust_render_edge: bool = os.getenv("TRUST_RENDER_EDGE", "0") == "1"
    # Production cloud use requires both operator approval of provider data terms
    # and the visitor's separate opt-in. Local extraction remains available.
    cloud_ai_enabled: bool = os.getenv("CLOUD_AI_ENABLED", "1") == "1"
    cloud_provider_data_approved: bool = os.getenv("CLOUD_PROVIDER_DATA_APPROVED", "0") == "1"

    # Polite contact for scholarly APIs (Crossref/arXiv etiquette).
    contact_email: str | None = os.getenv("CONTACT_EMAIL")
    openalex_api_key: str | None = os.getenv("OPENALEX_API_KEY")

    # Optional LLM providers for evidence-locked answer synthesis.
    # Chain order: configured providers are tried in LLM_PROVIDER_CHAIN order
    # (default: gemini first, then groq, then openai). Free tiers welcome -
    # the app falls back to deterministic extractive answers without any key.
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
    groq_api_key: str | None = os.getenv("GROQ_API_KEY")
    groq_model: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_base_url: str | None = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    llm_provider_chain: str = os.getenv("LLM_PROVIDER_CHAIN", "gemini,groq,openai")
    llm_calls_per_provider_hour: int = max(1, int(os.getenv("LLM_CALLS_PER_PROVIDER_HOUR", "120")))
    llm_calls_per_owner_hour: int = max(1, int(os.getenv("LLM_CALLS_PER_OWNER_HOUR", "12")))
    newsapi_key: str | None = os.getenv("NEWS_API_KEY")

    # Pipeline tuning
    chunk_size: int = int(os.getenv("CHUNK_SIZE", "900"))          # characters
    chunk_overlap: int = int(os.getenv("CHUNK_OVERLAP", "150"))
    retrieval_top_k: int = int(os.getenv("RETRIEVAL_TOP_K", "6"))
    openalex_max_citing: int = int(os.getenv("OPENALEX_MAX_CITING", "120"))
    index_cache_mb: int = max(8, int(os.getenv("INDEX_CACHE_MB", "128")))
    index_cache_owners: int = max(1, int(os.getenv("INDEX_CACHE_OWNERS", "16")))
    index_build_workers: int = max(1, min(2, int(os.getenv("INDEX_BUILD_WORKERS", "2"))))
    tfidf_word_max_features: int = max(1000, int(os.getenv("TFIDF_WORD_MAX_FEATURES", "60000")))
    tfidf_char_max_features: int = max(1000, int(os.getenv("TFIDF_CHAR_MAX_FEATURES", "80000")))

    # Rate limiting for chat/search (simple in-memory token bucket per owner)
    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))

    # CORS origins for deployment (comma-separated). Defaults cover local dev.
    cors_origins: str = os.getenv("CORS_ORIGINS",
                                  "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173")

    # Serve the built frontend (frontend/dist) from this app - single-origin deploys
    serve_frontend: bool = os.getenv("SERVE_FRONTEND", "0") == "1"
    demo_autoload: bool = os.getenv("DEMO_AUTOLOAD", "0") == "1"

    # Hard fallback when the browser's best-effort exit request is lost.
    # Anonymous research is never kept indefinitely.
    retention_hours: int = max(1, int(os.getenv("RETENTION_HOURS", "1")))

    @property
    def embedding_backend(self) -> str:
        from app.services.embeddings import embedding_backend_name
        return embedding_backend_name()


settings = Settings()
