# GitHub and Render deployment

Updated 10 October 2026 for the requested free Render deployment. These files configure the service; the repository publication and live deployment must be verified separately.

## Release design

- `render.yaml` builds `Dockerfile.backend`: the existing frontend and API share one HTTPS origin.
- One web service, one instance and one backend worker. Do not enable autoscaling or additional Uvicorn workers: anonymous lifecycle, queues and rate limits are coordinated inside this process.
- The Blueprint explicitly selects `plan: free` (0.1 CPU, 512 MB RAM), with a reduced resource profile described below. This is a hobby/demo deployment with limited capacity. Render advises against using free instances for production applications. [Blueprint compute plans](https://render.com/docs/blueprint-spec#plan) and [free instance limitations](https://render.com/docs/free) describe the current constraints.
- Anonymous research lives under `/tmp/scholarscout`, with no persistent disk, external database or user-data backup. Restarting or deploying ends active libraries. [Render's filesystem documentation](https://render.com/docs/disks) describes the default ephemeral filesystem.
- Render supplies the service hostname and terminates HTTPS at its edge. The container binds `0.0.0.0` and the dynamic `PORT`; its backend port is not separately exposed to the public internet. [Render web services](https://render.com/docs/web-services) documents port binding and TLS.

## Free instance behavior and resource profile

- An idle free service spins down after 15 minutes. The next request wakes it, which can take about one minute. Spin-down also removes the temporary research library.
- A workspace shares 750 free instance hours per month. Bandwidth, build minutes and high outbound traffic also have limits. For a zero-spend account, do not enable paid compute or add a payment method for usage overages; review the Billing page before creating the service.
- Free services cannot attach persistent disks and may restart at any time. An interrupted research session starts empty after restart. Do not use external uptime pings to prevent the normal idle shutdown.

These behaviors are documented in [Render's free-service documentation](https://render.com/docs/free). They affect availability and session continuity even when application checks pass.

The free profile changes capacity limits while preserving owner checks, encryption, deletion and consent:

| Resource | Blueprint limit |
|---|---|
| Backend process / analysis workers / active PDF parsers | 1 / 1 / 1 |
| PDF upload / HTTP request | 10 MB / 12 MB |
| PDF pages / extracted characters / chunks | 80 / 400,000 / 800 |
| Session papers / chunks / upload storage | 3 / 1,600 / 30 MB |
| Global uploads / extracted storage | 100 MB / 20 MB |
| Parser memory / wall timeout | 192 MB / 120 seconds |
| Admitted analysis jobs / jobs per owner | 4 / 1 |
| Concurrent ingestions / requests / requests per owner | 1 / 8 / 2 |
| Active sessions | 50 |
| Retained index cache / owners / concurrent index builders | 32 MB / 4 / 1 |
| TF-IDF word / character vocabulary features | 20,000 / 25,000 |
| Fetched OpenAlex citing records | 40 |

The parser timeout allows for the free instance's small CPU allocation. Oversized, scanned or unusually expensive PDFs can still be rejected. Busy requests should receive bounded responses that preserve existing research. The upload page reads the deployed limits from `/api/me/privacy`.

Cache eviction causes an owner's index to rebuild on its next search; it does not expose or delete another owner's documents. Smaller vocabulary and external lookup limits can reduce retrieval or literature coverage for long documents. Local development retains the larger defaults.

Memory caps are limits, not reserved RAM or a proof that every accepted workload fits. A local Windows test parsed the bundled 15-page demo at a 192 MB parser limit, but Windows process-memory accounting differs from Linux's address-space limit. Require the Linux Docker resource-constrained release check and the first live upload before treating the free profile as verified. If the service is repeatedly memory-killed, lower the document/concurrency limits further or move to suitable paid compute.

## Publish the prepared repository

1. Run `scripts/check_secrets.py` from the project root. Review the Git changes. `.env`, private keys, databases, uploads, local environments and document diagnostics are ignored. Do not add files with `git add -f` to bypass these rules.
2. Create a GitHub repository, preferably private while reviewing the release. Connect this project's local repository to that remote, commit the reviewed project files and push the `main` branch. Keep provider credentials in environment settings, never GitHub source files or frontend variables.
3. Wait for all **Release checks** jobs: backend, frontend and image. They run advisory scans, owner/privacy tests, the production smoke gate, frontend checks and an actual Docker build/startup probe. Protect `main` with these required checks before accepting further changes.

The repository has minimal read permissions in CI, pinned Actions commits and a weekly scheduled scan. Dependabot prepares dependency update pull requests; review and run the release gates before merging them.

## Create the Render service

1. Connect GitHub in Render and create a **Blueprint** from this repository. Select the intended branch and review the `scholarscout` service, Singapore region and **Free / $0 compute** before creation. Stop if Render proposes paid compute, a database, a persistent disk or any other paid resource.
2. Keep the Blueprint's single instance, generated `SCHOLARSCOUT_SECRET_KEY`, ephemeral data path and `/api/health` check. Do not attach a persistent disk or database. Render automatically sets `RENDER_EXTERNAL_HOSTNAME`; production rejects unapproved hostnames.
3. Leave `CLOUD_AI_ENABLED=0` and `CLOUD_PROVIDER_DATA_APPROVED=0` for the first deployment. Local extraction and grounded answers work without provider keys. Add optional research provider keys only through Render's environment settings.
4. If using a custom domain, set `ALLOWED_HOSTS` to the exact domain names as a comma-separated list, confirm Render's TLS certificate and repeat the checks below. A custom domain can become the Host header used by health checks.
5. The Blueprint uses `autoDeployTrigger: checksPass`: subsequent automatic deployments wait for GitHub checks. [Blueprint fields](https://render.com/docs/blueprint-spec) and [health checks](https://render.com/docs/health-checks) describe these settings.

`FORWARDED_ALLOW_IPS=*` is specific to this isolated Render service behind its edge proxy. For another host, configure trusted proxy addresses explicitly. Never expose this configuration through a separate directly reachable backend port. Local Compose trusts its dedicated nginx network and is bound to host loopback.

The Blueprint also enables `TRUST_RENDER_EDGE=1`. Only in production with Render's
service hostname does the application normalize rate-limit addresses from the
validated `CF-Connecting-IP` header. Missing/invalid edge addresses share a
conservative fallback bucket. [Render documents its Cloudflare ingress](https://render.com/articles/how-render-handles-ddos-attacks),
and [Cloudflare documents the client header and forwarded-IP chain](https://developers.cloudflare.com/fundamentals/reference/http-headers/).
Keep this setting off on other hosting providers. The live check should confirm
that forged `X-Forwarded-For`, `X-Real-IP` and `CF-Connecting-IP` values cannot
bypass the per-IP new-session limit through Render's public edge.

## Verify the first live deployment

- The HTTP URL redirects to HTTPS; HTTPS responses include HSTS, CSP, frame protections and `Cache-Control: no-store` for private API responses.
- `/api/health` returns only readiness status. Production documentation is unavailable.
- Use two fresh browser sessions. Upload different synthetic PDFs and verify each session's library, topics, evidence, source download and Ask results contain only its own documents. Requests using the other session's document ID must fail or yield empty scoped results.
- Confirm **End session and erase data**, the browser-exit notice and idle expiration. Ensure erasing one session leaves the other usable.
- Upload the bundled demo and use Evidence and Ask to build its private index. Inspect Render logs/metrics for memory kills, parser-budget failures and queue failures. Processing can be slower than local development.
- Try a PDF above 10 MB or 80 pages: rejection must preserve the existing library. Under excess load, bounded busy responses must recover without losing another visitor's data.
- Check restart behavior: active ephemeral sessions end cleanly and the site starts a fresh empty library.
- Let the free service idle and wake it again: a cold start should show a fresh library. Distinguish the hosting wake-up delay from an application failure.

## Optional cloud answers

Operator approval and visitor opt-in are both required. Review the chosen provider's retention, training and data-control settings before adding its server-side key. [Gemini unpaid terms](https://ai.google.dev/gemini-api/terms) permit product improvement and human review of submitted content; unpaid Gemini is unsuitable for confidential research. Review [Groq data controls](https://console.groq.com/docs/your-data), including applicable retention controls. Configure a suitable provider chain, then explicitly set both cloud flags to `1`. Deleting the ScholarScout session cannot recall material already sent to a provider.

## Maintaining the release

Update `backend/requirements.in`, install it and `requirements-dev.txt` in a fresh Python 3.11 environment, then run `scripts/lock_requirements.py` to regenerate the complete graph and artifact hashes in `requirements.lock.txt`. Audit the result and run all release checks before merging it. Keep optional Transformers/training packages outside the production runtime. Updating only a pin without its graph and hashes will fail installation. Refresh pinned base-image digests and Actions commits through reviewed updates.

No local Docker engine was available during preparation. The Docker/Trivy jobs are configured as required release gates; their execution and Render's real network/TLS behavior must be confirmed after publication. A passed advisory scan is a dated check, not a promise of immunity from future threats. Monitor availability, quota rejections and provider failures without logging session tokens, document passages or chat content.
