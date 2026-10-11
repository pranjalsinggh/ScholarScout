# Release verification

Updated **11 October 2026**. The [public repository](https://github.com/pranjalsinggh/ScholarScout)
is deployed on Render Free at **[scholarscout-dskr.onrender.com](https://scholarscout-dskr.onrender.com)**.
The cloud update below covers implementation commit `a3d86bd`; the initial release results cover `9014707`.

## Verified Local Evidence and Cloud AI update

The cloud implementation at `a3d86bd` passed all three jobs in
[Release checks](https://github.com/pranjalsinggh/ScholarScout/actions/runs/38132887545)
and deployed on Render Free before the focused live verification. Gemini and
Groq each passed a minimal credential/model readiness request without exposing
credentials. The server uses `gemini,groq`; their Blueprint entries contain
only key names with `sync: false`, and the values are private Render settings.

The [sanitized live report](verification/cloud-modes-2026-10-11.json) records
**176 passing checks** using the public Attention Is All You Need paper and an
independent synthetic peer document. Both uncached cloud questions returned
`synthesized:gemini`, with valid evidence-card citations: classic in **2.72 s**,
streamed in **2.14 s**. The public PDF parsed all 15 pages in **18.92 s**.
The report contains no tokens, paper IDs, questions, answers, passages or keys.

The live checks verified:

- Fresh visitors start with Local Evidence and cloud consent off.
- Cloud requests are rejected before consent and after consent withdrawal on both chat endpoints.
- Opting in one visitor leaves the other visitor's preference and answers local.
- Explicit Local bypasses a previously generated cloud answer and its cache, including streaming.
- Foreign paper IDs cannot reveal documents, chat history or retrieval passages.
- Erasing one session revokes its token while the peer's paper, preferences and answers remain usable; both disposable owners were erased.

CI remains offline and provider-free. The backend gate passed **247 tests**
with three optional real-PDF fixture skips and six existing API deprecation
warnings; the live public-demo checks exercised that PDF on Render.
The restricted release image passed **41 smoke checks** at **0.1 CPU / 512 MB**,
plus a real-server startup/readiness probe and the HIGH/CRITICAL advisory gate.
Frontend build, render/performance checks and dependency advisories passed.
The exported smoke environment contains no provider or application secrets.

The synthesis prompt now requires a source citation on the opening answer and
each supporting bullet. Existing strict validation rejects missing or invalid
citations and retains local fallback. A diagnostic before that refinement
confirmed the fallback rejected two candidates with uncited prose rather than
presenting them as grounded cloud answers. Streamed providers now receive the
same bounded recent conversation as classic providers; offline payload tests
cover all adapters. Initial chat preference, history and suggestion reads are
sequential to fit the per-session request budget.

The live browser walkthrough confirmed a Local answer, a cited Gemini answer,
switching the same question back to Local, and restoration of both modes'
conversation history through in-site navigation. The answer selector and
composer were also checked at a 390-pixel mobile viewport with no horizontal
page overflow. A separate fresh-owner API check confirmed empty history and
correctly shaped user/assistant history after a Local answer; its disposable
owner was erased. A transient initial history-read warning recovered on
navigation; the chat now offers a retry action for that warning.

These are dated checks, not guarantees of provider uptime, factual correctness
or future quota availability. Local answers remain available if cloud providers
fail or their output cannot pass the evidence checks. Free Gemini's provider
retention and training terms still apply to cloud submissions.

## Verified in GitHub CI and on Render

All three jobs in [Release checks](https://github.com/pranjalsinggh/ScholarScout/actions/runs/38128516294)
passed before deployment.

| Check | Result |
|---|---|
| Backend, frontend and image release jobs | Passed |
| Final application image advisory scan | Zero HIGH/CRITICAL findings with Trivy 0.74.0; no ignored findings |
| Restricted Linux release image | Upload, private retrieval and erasure passed with 0.1 CPU / 512 MB RAM and a 192 MB parser limit |
| Real server entrypoint and readiness | Passed with the same 0.1 CPU / 512 MB limits |
| Initial live HTTPS/API verification | 105 checks passed: redirects, security headers, frontend assets, CORS, hidden API documentation, private document/search/chat access and erasure |
| Two-owner live lifecycle | Bidirectional isolation passed; erasing one owner preserved the other, and both ended tokens were rejected |
| Public 15-page demo on Render | 30 focused checks passed: on-demand arXiv fetch, all 15 pages parsed, valid source PDF, ten Evidence passages with page references, sourced local main-contribution answer and confirmed erasure/revocation |

The final image uses a pinned nonroot Distroless Debian 13 base, Python 3.11.17
and 33 hash-locked application packages. Required native libraries retain their
Debian package records and copyright notices. The image excludes installation
tools and server-unused terminal/GUI bindings. These are dated checks of specific
workloads, not a proof that every PDF or traffic load fits the free service.

The demo reached ready in 19.39 seconds; its 30 checks completed in 22.92 seconds
with cloud AI and external lookups off. This is one measured run, not a latency
guarantee. `main` requires all three CI jobs, blocks force pushes/deletion and
allows the owner administrator to bypass. Secret scanning, push protection and
Dependabot alerts/security updates are enabled.

## Verified locally

| Check | Result |
|---|---|
| Backend regressions | 231 passed; six dependency API deprecation warnings |
| Isolated application smoke using the free-tier configuration | 32 checks passed, zero live cloud calls |
| Original five test PDFs with the isolated parser | All five passed page, text, time and memory budgets |
| TypeScript and frontend production build | Passed |
| Graph, scrolling, streamed chat and cancellation regression script | Passed |
| npm advisory audit after Tailwind 4 migration | Zero known vulnerabilities |
| Exact 33-package production Python advisory audit | Zero known vulnerabilities |
| Actual hash-verified core installation and dependency consistency | Passed; Transformers and Torch absent |
| Windows and Linux wheel/hash resolution | Passed |
| Python compilation and critical correctness lint | Passed |
| Git candidate credential/artifact scan | Passed; environment files, keys, databases and private uploads excluded |
| Render/Compose configuration invariants | Passed |

The frontend dependency migration preserves the existing palette, typography,
responsive breakpoints and glass treatments. Desktop and mobile landing layouts
and the upload screen were visually checked. The demo PDF is fetched on demand
from its public source in a published checkout; seven offline tests cover the
fallback, validation, cleanup and ownership behavior. Local prompt drafts,
environment secrets, private uploads and the local PDF binary are excluded from
publication.

Run the complete local gate using your environment's Python:

```powershell
backend/.venv/Scripts/python.exe scripts/verify.py
```

Install `backend/requirements.txt` and `backend/requirements-dev.txt`, and run
`npm ci --ignore-scripts` in `frontend`, before using this command in a new
checkout. Advisory results are dated checks; CI scans again on changes and weekly.

## Isolation and deletion changes

- Anonymous sessions use a cryptographically random tab token, keyed owner
  hashes and ownership checks on document, chat, feedback, watch, export and
  source-file access. A bearer token is a credential: possession grants access
  to that session.
- Retrieval vocabularies, BM25 statistics, topic calculations and answer caches
  belong to one owner. Concurrent rebuilds for the same owner share one build;
  foreign uploads cannot alter its search scores or appear in its results.
- Persistent content-free revocation records and serialized write transactions
  prevent delayed chats, caches, analyses and external enrichment from restoring
  erased research. External enrichment collects in private memory and publishes
  only after checking current ownership and consent.
- Fair bounded queues permit one active analysis per owner, with per-owner,
  global upload, request, parser, storage and provider budgets. Native parsers
  run separately with hard timeout, memory and extraction limits. Downloads
  connect to the exact validated public IP, retain hostname TLS checks, and
  validate redirects.
- Confirmed erasure removes session content and cancels owned jobs/parsers.
  Closing or leaving a tab sends a best-effort deletion request; a lost request
  is handled by the one-hour idle expiry. This cannot promise instantaneous
  deletion after every browser exit.
- Unexpected failures log fixed messages and error classes, avoiding document
  text and bearer tokens in tracebacks. Production hides detailed operational
  health and API documentation.

## Production configuration

The Blueprint uses one service, instance and worker, a generated secret,
ephemeral data and a free 512 MB compute plan with reduced resource limits.
Free services can sleep and lose temporary research; this is a demo workload
profile. It has no persistent research disk or database. The current Blueprint
enables cloud capability with operator approval and private provider-key injection.
Visitors still default to local answers; cloud answers require their separate opt-in.
Session
erasure cannot recall copies already sent to an external provider.

Logical user-data isolation and fair admission are tested. Shared hardware,
network availability and provider quotas still have finite capacity; absolute
zero impact between visitors and immunity from every threat are not guarantees.

## Verification for future changes

Keep all three CI jobs required on `main`, including the actual image scan,
constrained release gate and real server startup. `autoDeployTrigger: checksPass`
controls ordinary automatic code deployments. Blueprint configuration changes
can trigger a build immediately, before those checks finish; the cloud-mode
rollout used this path. Run the local release gates before pushing configuration
changes, and monitor every CI job and the Render build before considering the
release verified. Repeat live HTTPS/proxy, private-session and deletion checks
after relevant code, domain or environment changes. Check idle wake-up and restart
behavior when hosting changes; free-instance sleep and shared quotas still limit
availability.
Follow [the deployment guide](DEPLOYMENT.md) for these checks and provider policy.
