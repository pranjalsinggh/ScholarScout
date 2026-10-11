# ScholarScout

**[Live demo](https://scholarscout-dskr.onrender.com)** · **[Public repository](https://github.com/pranjalsinggh/ScholarScout)** · **[Passed release checks](https://github.com/pranjalsinggh/ScholarScout/actions/runs/38128516294)**

The initial Render Free release was verified on **11 October 2026**: all three CI jobs passed, followed by 105 live deployment checks and 30 public-demo checks covering HTTPS, private session access, local search/chat and confirmed erasure. The current Blueprint enables optional Gemini answers with Groq fallback when private Render credentials are configured. Visitors start with local grounded answers and choose whether to opt into cloud processing. Cloud deployment verification is tracked separately in [release verification](docs/RELEASE_CHECKS.md). Free hosting sleeps after 15 idle minutes and temporary research disappears on sleep, restart or deployment.

> **Research is bigger than a paper.**
> Upload a research paper and explore the people, papers, debates, evidence and developments surrounding it.

ScholarScout is an AI-powered **research intelligence platform** — not a PDF chatbot. It analyzes an uploaded paper, then connects it to the wider research ecosystem: citations, replications, disagreements, researchers, institutions, news and research gaps — with a visible source trail for every external claim.

**Design:** a custom **Liquid Glass** system — deep-space canvas, drifting aurora fields, frosted glass panes with refraction highlights, gradient typography.

---

## Features

| Area | What you get |
|---|---|
| **Research X-Ray** | Claim, approach, evidence, results and stated limits, with source passages and honest empty states for fields a document does not contain |
| **Complexity profile** | Subject-specific background topics and transparent reading estimates; implementation, evaluation and data dimensions appear when supporting text is detected |
| **Evidence-backed chat** | Local extractive answers by default; optional cloud synthesis with evidence cards and an insufficient-evidence fallback |
| **Semantic search** | Evidence from the selected document by default, with an optional session-library scope and links to source pages |
| **Literature engine** | Similar papers (library embeddings + OpenAlex related works) with *why* they're similar; full citation ecosystem (cites / cited-by / related) |
| **Timeline** | "Since this paper was published" — follow-ups, replications, contradictions, corrections, milestones from live data |
| **Debate tracker** | "Who disagrees?" — challenges presented neutrally; replication tracker with careful evidence categories |
| **People & institutions** | Author profiles (h-index, areas) from OpenAlex, extenders, challengers, major researchers, institution map, funding disclosures |
| **Research graph** | Interactive force-directed graph: papers, authors, institutions, methods, datasets, events with typed edges (cites, extends, challenges…), zoom/pan/filter/click |
| **Gaps & ideas** | Sourced future work, plans and limitations, with study prompts clearly separated from verified novelty |
| **Topics & trends** | KMeans clustering over embeddings; topic activity over actual publication years |
| **Watchlist** | Saved papers with polling for newly indexed citations |
| **Health panel** | Publication / citations / follow-ups / replication / contradictions / corrections / retraction summary — evidence, not a quality score |

### Hallucination protection

- **LLM synthesis with a free-tier provider chain**: configure `GEMINI_API_KEY` and/or `GROQ_API_KEY` — they're tried in `LLM_PROVIDER_CHAIN` order (default gemini → groq → openai) and back each other up; the UI shows which provider answered
- Identical question/evidence pairs share an in-flight request and an encrypted owner-scoped persistent cache. A cache hit is labeled **Cached · 0 tokens**. Provider `Retry-After` values and an hourly call budget prevent rate-limit storms.
- The privacy panel shows approximate input/output token counts per anonymous session. Set provider input/output USD-per-million-token rates to show a cost estimate; without rates, the UI says cost is unavailable rather than assuming zero.
- Cloud synthesis is off for each new anonymous session until the visitor enables it in chat. The local extractive answer path remains available without sending question passages to those providers.
- Every provider is **evidence-locked**: the prompt may only answer from the numbered retrieved passages, cites them inline, and must say so when they're insufficient
- Deterministic **extractive** answering remains the always-available fallback (no key needed)
- Embedding backend **self-verifies** at startup with a semantic sanity probe and falls back to TF-IDF if weights are broken
- Overview questions use the uploaded document's own abstract or introduction. Retrieval expands terms with vocabulary extracted from that document and applies section priors and diversity.
- OpenAlex/Crossref matches are checked against the PDF's title, authors and DOI. A weak candidate is rejected before its author or citation graph is attached, and the dossier labels the external record unverified.
- Generated answers must cite existing evidence-card numbers. The extractive fallback remains available when synthesis fails validation; users should still verify research claims.

---

## Architecture

```text
┌──────────── frontend (React + TypeScript + Vite) ────────────┐
│  Liquid Glass design system · Framer Motion · Recharts       │
│  Landing / Upload / Library / Dashboard (11 section views)   │
└──────────────────────────┬───────────────────────────────────┘
                           │ /api (Vite proxy → :8000)
┌──────────────────────────▼───────────────────────────────────┐
│                 backend (FastAPI + SQLAlchemy)                │
│                                                               │
│  Ingestion:  PyMuPDF → sectionizer → metadata heuristics      │
│  Retrieval:  chunker → private sparse TF-IDF + BM25           │
│              → owner-scoped cosine/MMR → retriever            │
│  RAG:        intent routing → extractive composer             │
│              → opt-in Gemini/Groq synthesis with validation   │
│  Ecosystem:  OpenAlex (primary) · Crossref (fallback)         │
│              arXiv (recent preprints) — all cached, swappable │
│  Analysis:   x-ray · complexity · timeline · debate ·         │
│              replication · people · institutions · graph ·    │
│              topics (KMeans) · trends · gaps · watchlist      │
└──────────────────────────────────────────────────────────────┘
```

## Tech Stack

**Backend** — Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2 (SQLite → PostgreSQL-ready), PyMuPDF, scikit-learn, sentence-transformers *(optional)*, FAISS *(optional)*, httpx.
**Frontend** — React 18, TypeScript, Vite, Tailwind CSS, Framer Motion, Lucide, Recharts, React Router.

## ML Pipeline

1. **PDF extraction** — PyMuPDF with font-size title detection and arXiv-stamp filtering
2. **Section detection** — numbered/bold/caps heading matching onto canonical sections, page-based fallback for messy PDFs
3. **Metadata heuristics** — authors, DOI, arXiv id, year, keywords, datasets, methods, metrics, funding
4. **Chunking** — sentence-aware, overlapping
5. **Private embeddings** — each session fits its own sparse TF-IDF vocabulary (word 1–2-gram + char 3–5-gram union), L2-normalized. Another visitor's uploads cannot change its ranking statistics.
6. **Private index** — bounded in-memory sparse cosine and BM25 snapshots; paper-level centroids power library similarity and topic clustering. The production image excludes optional model/training packages and the global dense store.
7. **Retrieval** — similarity floor per backend, section priors (abstract/introduction), per-section diversity cap

## Data Sources

| Provider | Used for | Key required |
|---|---|---|
| OpenAlex | work matching, citation network, author profiles, h-index, related works | optional `OPENALEX_API_KEY` for a larger API budget |
| Crossref | metadata fallback when OpenAlex can't match | no |
| arXiv API | recent preprints for Latest Developments | no |
| NewsAPI | news tier of Latest Developments | optional `NEWS_API_KEY` |

External calls are bounded and failure-isolated. Connected research is off by default. Enabling it after upload queues context refreshes for existing documents; each dossier also has a refresh control. News distinguishes missing coverage, provider failure and disabled lookups, and related topic results do not imply a citation relationship.

Connected results use an encrypted, session-scoped cache with a short lifetime for failures. The cache, answer history, feedback and usage records are erased with the anonymous session. Unknown publication years are displayed as unknown.

### Consistency across document types

Historical documents, speeches, plans and theoretical essays do not always contain experimental methods or datasets. X-Ray and Ask explain missing fields instead of supplying unrelated passages. Nonexperimental documents receive a source and reasoning checklist; complexity suggestions follow the detected subject. Extracted passages retain their actual PDF page, including long sections and tables.

All dashboard sections expose loading, errors and retry controls. Retrying a failed section preserves the current anonymous session. Root recovery also offers session erasure when a view cannot load. To benefit from extraction changes after upgrading, end the old session and re-upload its PDFs.

## Screenshots

*Landing hero with animated network background — dark Liquid Glass with aurora fields. Research dashboard with sidebar sections (Overview, X-Ray, Literature, Network, People, Timeline, Debate, News, Evidence, Gaps, Ask ScholarScout). Interactive research graph with typed, color-coded nodes.*

## Installation

```bash
# 1. Backend
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt        # Windows, hashed runtime lock
.venv\Scripts\pip install -r requirements-dev.txt    # tests/audit tools (development only)
# source .venv/bin/activate && pip install -r requirements.txt && pip install -r requirements-dev.txt  # macOS/Linux

# Production uses local TF-IDF and the audited core environment.
# Optional model/training packages are excluded from the release image.

# 2. Frontend
cd ../frontend
npm ci --ignore-scripts
```

## Environment Variables

Copy `backend/.env.example` → `backend/.env` for local development. Provider keys are optional; local analysis and extractive chat work without them. Production requires a generated secret and approved hostnames, supplied by the Render Blueprint. Connected research lookups contact public APIs when a visitor opts in.

| Variable | Purpose |
|---|---|
| `CONTACT_EMAIL` | polite pool for OpenAlex/Crossref/arXiv (recommended) |
| `GEMINI_API_KEY` | free-tier Gemini synthesis (aistudio.google.com) |
| `GROQ_API_KEY` | free-tier Groq synthesis, fallback provider (console.groq.com) |
| `OPENAI_API_KEY` | optional third in the chain |
| `LLM_PROVIDER_CHAIN`, `GEMINI_MODEL`, `GROQ_MODEL` | chain order & model overrides |
| `CLOUD_AI_ENABLED`, `CLOUD_PROVIDER_DATA_APPROVED` | cloud capability and operator approval; both required in production, plus each visitor's opt-in |
| `LLM_CALLS_PER_PROVIDER_HOUR` | maximum provider requests per process per hour (default 120) |
| `GEMINI_INPUT_USD_PER_M`, `GEMINI_OUTPUT_USD_PER_M` | optional cost-estimate rates; equivalent `GROQ_*` and `OPENAI_*` variables are supported |
| `NEWS_API_KEY` | news headlines tier |
| `RETENTION_HOURS` | abandoned session expiry, default one idle hour; refresh/leave also triggers best-effort erase |
| `SERVE_FRONTEND`, `FRONTEND_DIST_DIR`, `DEMO_AUTOLOAD` | built frontend serving and optional per-session demo autoload |
| `SCHOLARSCOUT_DATA_DIR` | override the private data directory |
| `MAX_UPLOAD_MB`, `CHUNK_SIZE`, `RETRIEVAL_TOP_K`, … | pipeline tuning |
| `DATABASE_URL` | SQLite database URL; other databases need migration and lifecycle testing |

## Running Locally

```bash
# Terminal 1 — backend (port 8000)
cd backend
.venv\Scripts\python -m uvicorn app.main:app --port 8000

# Terminal 2 — frontend (port 5173, proxies /api → 8000)
cd frontend
npm run dev
```

Open http://localhost:5173 — upload a text-based PDF and watch the dossier build stage by stage.

## API Documentation

Development OpenAPI docs: **http://localhost:8000/api/docs** (30+ endpoints under `/api`). Documentation and detailed operational health are disabled in production.

Key endpoints: `POST /papers/upload` · `GET /papers/{id}` · `GET /papers/{id}/xray` · `POST /chat` · `POST /search` · `GET /papers/{id}/similar` · `GET /papers/{id}/citations` · `GET /papers/{id}/timeline` · `GET /papers/{id}/debate` · `GET /papers/{id}/replication` · `GET /papers/{id}/people` · `GET /graph` · `GET /topics` · `GET /trends` · `GET /papers/{id}/gaps` · `GET /papers/{id}/health` · `GET|POST /watchlist`.

## Project Structure

```text
ScholarScout/
├── backend/
│   ├── app/
│   │   ├── api/routes.py          # all endpoints
│   │   ├── models/entities.py     # relational schema
│   │   ├── schemas/api.py         # pydantic contracts
│   │   ├── ingestion/             # pdf_extractor, sectionizer, metadata
│   │   ├── services/
│   │   │   ├── scholarly/         # openalex, crossref, arxiv, aggregator
│   │   │   ├── analysis/          # xray, complexity, timeline, insights,
│   │   │   │                      # news, gaps, topics, similar, graph, watchlist
│   │   │   ├── embeddings.py      # sanity-probed MiniLM / TF-IDF fallback
│   │   │   ├── vector_store.py    # FAISS / numpy, centroids
│   │   │   ├── chunker.py, pipeline.py
│   │   ├── rag/                   # retriever, answerer (evidence-first)
│   │   └── database/database.py
│   ├── tests/test_backend.py
│   ├── requirements.txt · .env.example
│   └── data/                      # sqlite db, uploads, index (gitignored)
└── frontend/
    └── src/
        ├── pages/                 # Landing, Upload, Library, Dashboard
        ├── components/glass/      # Liquid Glass primitives + backgrounds
        ├── components/dashboard/  # 11 dossier section views
        ├── components/network/    # force-directed GraphView (canvas)
        ├── services/api.ts · types/api.ts · index.css (design system)
```

## Limitations

- Metadata extraction uses transparent heuristics. Exotic PDF layouts and scanned pages can still be misread; a weak external match is withheld rather than treated as a correction.
- Replication/disagreement classification is keyword-based and labeled `heuristic`; it can miss neutrally-worded disagreements and never infers replication success from similarity.
- News results depend on public indexes and may be empty for niche or unpublished work.
- Private TF-IDF retrieval matches wording and character patterns; it may miss paraphrases. Only the session's own index refits when its corpus changes. Evidence cards and the local insufficient-evidence fallback remain available.

## Anonymous sessions and deletion

ScholarScout uses a random tab-scoped bearer token instead of an account. A refresh or navigation away warns when papers exist; if the user continues, the tab sends a best-effort deletion request and the next visit explains that the session ended. The explicit **End session and erase data** action waits for server confirmation. The server also deletes abandoned sessions after `RETENTION_HOURS` of inactivity, including files, chats, feedback profiles, answer caches, usage records and embedding vectors. A browser exit cannot guarantee that a network request arrives, so expiry is the reliable fallback. A newly generated session starts a new library. Browser tabs can inherit session storage when duplicated; the browser session guard rotates inherited tokens. Session data also disappears after a server restart or deploy in the supplied ephemeral deployment.

The server decrypts uploaded PDFs and extracted text to process and search them. The database and search index encrypt paper text and most descriptive metadata at rest, while operational identifiers and some public metadata remain readable. Embeddings run locally. When a visitor enables cloud AI, Gemini and Groq may receive relevant passages and questions for answers. Deletion from ScholarScout cannot delete data already received by external providers. [Gemini's unpaid API terms](https://ai.google.dev/gemini-api/terms) allow submitted content to be used for product improvement and human review; do not enable it for confidential research. Review [Groq's data controls](https://console.groq.com/docs/your-data) before enabling it for sensitive work.

Connected research lookups are also off by default. Visitors can enable them before uploading; this sends title and topic terms to public research and news services to build the wider citation dossier. Entering an arXiv ID or DOI to fetch a PDF contacts that public source even in private analysis mode.

The anonymous token protects one session from another; it is not an account or identity check. Deploy behind HTTPS, protect the server and its data volume, and set rate limits suitable for expected traffic. Do not expose the backend port directly.

## Deployment

The project includes a single-service [Render Blueprint](render.yaml) and a [deployment guide](docs/DEPLOYMENT.md). The backend Docker image serves the frontend and API on one origin, binds Render's `PORT`, and runs one non-root server worker. The Blueprint selects Render's free 512 MB compute plan with reduced upload, document, queue and index limits, a generated production secret, ephemeral anonymous data, and no persistent database or disk. Free services sleep after 15 idle minutes and lose research on sleep/restart/redeploy; this profile is for demos and small workloads. The operator has enabled cloud synthesis for this deployment with the `gemini,groq` chain. The keys must be supplied privately in Render; the Blueprint stores only their names. Each visitor starts in local mode and must opt into external processing before choosing a cloud answer. Local answers remain available independently of provider keys and quotas.

GitHub Actions gates releases with secret checks, backend tests, an offline production smoke check, npm and Python advisories, frontend build/render checks, a Docker image build, a container vulnerability scan and a real server readiness probe. Base images and GitHub Actions are pinned to digests/commits; the complete production Python graph is pinned with artifact hashes. Dependabot prepares weekly dependency review updates. No GitHub repository or Render service is created by these files alone.

The local split-service preview remains available with `docker compose up --build` on `127.0.0.1:8080`; `docker compose --profile demo up --build` adds the demo on `127.0.0.1:8081`. Compose uses read-only container roots, temporary memory-backed storage, dropped capabilities and resource limits. It injects `backend/.env` at runtime when present. Docker Compose v2.24 or later is required for optional `env_file` support. This preview uses loopback HTTP; public deployment requires HTTPS and a trusted proxy.

Run release checks before publishing:

```bash
backend/.venv/Scripts/python.exe scripts/check_secrets.py
backend/.venv/Scripts/python.exe -m pytest backend/tests -q
cd frontend
npm ci --ignore-scripts
npm audit --audit-level=moderate
npm run build
npm run verify:render
cd ..
backend/.venv/Scripts/python.exe scripts/release_gate.py
```

The production smoke gate uses synthetic PDFs, independent anonymous tokens and disposable storage, with zero live cloud calls. The production health endpoint only returns readiness status; it does not disclose other visitors' counts or provider configuration. Test the deployed HTTPS endpoint, source downloads, owner isolation and erasure again after Render starts the service. The Docker build/container checks still require a Docker-capable machine or GitHub Actions.

## Secret Safety

- Provider keys stay in Render environment settings or local `backend/.env`; they are excluded from Git and Docker build inputs.
- Production requires `SCHOLARSCOUT_SECRET_KEY` from the environment. The Blueprint generates it. Development can use a local `secret.key`; no production key is written into the anonymous data directory.
- Frontend code receives no provider credentials. Anonymous browser tokens are bearer credentials; anyone with a stolen token could access that session. HTTPS, CSP, strict token validation and session isolation reduce this risk.
- Keep anonymous data ephemeral. Do not back up its database, uploads, vectors or session caches, and do not attach a Render persistent disk. Logical deletion cannot promise physical erasure from provider infrastructure, external services or operator-created backups.
- A browser's exit deletion request is best effort; confirmed erasure and idle expiration remain available. Shared server hardware still requires traffic controls and monitoring even with per-owner quotas.

If an actual key has ever been shared or committed, rotate it in the provider console.

## One-Command Health Check

```bash
backend\.venv\Scripts\python.exe scripts\verify.py
```

Runs the local release gates: secret/artifact checks, deployment configuration,
backend lint/tests, frontend advisories/build/render checks and the isolated
production smoke check. It uses the current interpreter and requires the
development tools plus Node.js. Container and live Render checks run separately
in CI and on first deployment.

## Tests

```bash
cd backend
.venv\Scripts\python -m pytest tests/ -v
```

From `frontend`, run `npm run verify:render` for synthetic graph scheduling,
touch gesture, streamed-chat and cancellation checks. Run `npm run build` for
the TypeScript and production bundle gate. See [interface smoothness checks](docs/SMOOTHNESS_CHECKS.md)
for the browser verification and measurement limits.

## License

ScholarScout's original application code is MIT licensed. Dependencies retain their own licenses, including PyMuPDF/MuPDF's AGPL or commercial licensing. See [third-party notices](THIRD_PARTY_NOTICES.md) and [demo document credits](backend/demo_assets/README.md). The public demo paper is obtained from arXiv on demand in published installations and is not covered by ScholarScout's MIT license.
