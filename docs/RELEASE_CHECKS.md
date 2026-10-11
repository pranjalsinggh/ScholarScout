# Release verification

Updated **11 October 2026**. The [public repository](https://github.com/pranjalsinggh/ScholarScout)
is deployed on Render Free at **[scholarscout-dskr.onrender.com](https://scholarscout-dskr.onrender.com)**.
The results below cover release commit `9014707` and the initial live verification.

## Cloud mode update awaiting deployment verification

The current Blueprint enables operator-approved cloud answers and the
`gemini,groq` chain. `GEMINI_API_KEY` and `GROQ_API_KEY` are declared with
`sync: false` for private Render injection; no credentials are in source.
Visitors retain local mode by default and must opt in before selecting a cloud
answer. The initial live results below were measured with cloud processing off.
They are not a verification of the new cloud configuration or provider keys.
CI remains offline and provider-free; live opt-in, provider attribution, local
selection and consent revocation must be checked after the new deployment.

The updated offline smoke gate passed **41 checks** locally with disposable
storage and zero live cloud calls. It verifies that fresh owners start with
cloud consent and connected lookups off, unavailable cloud requests are
rejected, explicit local selection returns extractive answers, and the original
private-session retrieval and erasure checks still pass. The configuration gate
also verified the approved cloud flags, provider chain, private key descriptors
and unchanged free-instance resource limits. Its exported container environment
contains no provider or application-secret values.

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
