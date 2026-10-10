# Document consistency checks

Checked on 30 September 2026 using the five supplied PDFs in a separate local data directory. Cloud answer synthesis was disabled during the corpus run. External metadata and news were checked after enabling connected research.

## Live corpus results

All five uploads reached `ready`. Eighteen dossier endpoints per document, three Ask questions per document, and document-scoped Evidence searches returned successfully. Enabling connected research after upload queued all five documents and completed their refresh attempts.

| Document | Overview source page | Evidence passages returned | Authors identified locally |
| --- | ---: | ---: | ---: |
| Gudimallam temple document | 1 | 9 | 0 |
| Space exploration and human development speech | 6 | 7 | 1 |
| Research and Discussion of Quantum Theory | 1 | 10 | 1 |
| The Vision for Space Exploration | 5 | 10 | 0 |
| Space and Time | 1 | 10 | 2 |

These counts describe this test run. External coverage changes with provider availability, query results and dates; a missing author or news item is not filled with a fabricated substitute.

## Regressions addressed

- Split cover titles, inline abstracts, wrapped words and offset two-column layouts.
- Publisher and preface text appearing in document overviews.
- Long sections being truncated and later passages receiving the section's first page number.
- Missing-method questions retrieving unrelated anecdotes.
- Historical experiments from other teams being described as the document's original results.
- Generic machine-learning prerequisites and experiment checklists being applied to speeches, history and theory.
- External outages being reported as empty coverage; unrelated software, retail and promotional news being admitted by broad topic words.
- Connected research staying stale when enabled after an upload.
- Evidence and similarity selecting from other users or unrelated documents before applying scope.
- Failed section requests, background refreshes and stale lazy bundles blocking recovery.

## Verification and limits

The full backend regression suite passed **182 tests**, with **4 optional tests skipped**. After the final evidence-content cache change, all **40 targeted answer/provider tests** passed. Coverage includes retrieval, provider failures, session isolation, consent, deletion, document structure, source pages and external matching. The frontend TypeScript/Vite production build passed. Browser checks covered desktop and 390-pixel phone layouts, Evidence, local author cards, disabled News, and Ask with numbered page evidence.

The corpus check validates local extraction and dashboard behavior. Live Gemini/Groq synthesis was not exercised; provider fallback and failure handling were checked with mocked responses. News results and citation matches remain candidates that a researcher should inspect through their source links.

All temporary anonymous app sessions, their uploads, passages, chat and research caches were erased. Original PDFs and the user's `.env` were unchanged. Browser exit requests are best effort; explicit session erasure and the server retention fallback remain available.

Deletion of separate diagnostic text/page images and the corpus report in the Codex workspace was rejected by automatic approval review with the reason "blocked by policy". Those local diagnostic files remain outside the app's data directory; they are not included in the project or production build.

After updating the app, restart it and re-upload PDFs in a fresh session to apply the new extraction rules.
