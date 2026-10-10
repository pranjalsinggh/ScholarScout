# Release verification

Updated **10 October 2026** for the public GitHub release and Render's free
compute profile. The local checks below passed. The actual Linux image and live
Render deployment have separate required checks described below.

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
profile. It has no persistent research disk or database. Cloud answers default
to disabled. Enabling a provider requires
operator approval of its data policy and a visitor's separate opt-in. Session
erasure cannot recall copies already sent to an external provider.

Logical user-data isolation and fair admission are tested. Shared hardware,
network availability and provider quotas still have finite capacity; absolute
zero impact between visitors and immunity from every threat are not guarantees.

## Checks that require publication/deployment

No Docker engine was available locally. GitHub CI is configured to build and
scan the actual image, run the production smoke gate inside a restricted
container and start a real server on its configured port. Require these checks
to pass before launch. Then verify Render's live HTTPS/proxy behavior and the
two-browser isolation/deletion flow described in [the deployment guide](DEPLOYMENT.md).
