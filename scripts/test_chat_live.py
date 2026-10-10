"""Live dossier battery for a built ScholarScout deployment.

Examples:
  python scripts/test_chat_live.py --base http://127.0.0.1:8080
  python scripts/test_chat_live.py --base http://127.0.0.1:8080 --cloud-max 12

Synthetic documents are uploaded through the real API. Each isolated anonymous
session is explicitly erased. Tokens, keys, and answer text are never printed.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import re
import secrets
import sys
import time

import pymupdf as fitz
import httpx


QUESTIONS = [
    "What is this document about?", "What is its main claim?",
    "What methodology or approach did it use?", "Which datasets or sources were used?",
    "What were the main results or findings?", "What limitations are explicitly stated?",
    "How was the approach evaluated?", "Who wrote this document?",
    "When was this document published?", "What evidence supports its central claim?",
    "What practical implications follow from the evidence?", "Explain it simply.",
]


def pdf(pages: list[str]) -> bytes:
    doc = fitz.open()
    for body in pages:
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(55, 55, 550, 770), body, fontsize=10)
    result = doc.tobytes()
    doc.close()
    return result


def corpus() -> list[tuple[str, bytes | None]]:
    policy = [
        "PUBLIC RESEARCH ACCESS\nIntroduction\nThis policy essay argues that publicly "
        "funded research should be available to all readers. It draws on library budgets.",
        "Evidence and Argument\nPublic consultation responses from twelve campuses "
        "show that access barriers delay collaboration. Stakeholder consultation "
        "informed the proposal; the response rate was 62 percent.",
        "Conclusion\nThe essay recommends open repositories and annual access audits. "
        "It reports no controlled experiment or causal effect size.",
    ]
    strategy = [
        "RESEARCH STRATEGY 2035\nExecutive Summary\nThis ten-year plan proposes "
        "climate observations, open data, and university partnerships.",
        "Approach\nStakeholder workshops, mission reviews, and risk assessment "
        "determine priorities. Each priority has a responsible team and milestone.",
        "Evaluation\nProgress will be measured by data release time, mission coverage, "
        "and partner participation. No achieved outcome is claimed in this plan.",
        "Risks\nBudget changes and launch delays may prevent milestones. "
        "The report recommends annual review.",
    ] + [f"RESEARCH STRATEGY 2035\nImplementation Plan\nYear {i}: teams review "
         "coverage, release open data, and publish a progress report. "
         "This is a planned activity, not a measured result." for i in range(5, 31)]
    journal = [
        "RIVER MONITORING STUDY\nAbstract\nWe compare two sensor layouts at 40 "
        "river sites. The proposed layout increases valid readings by 18 percent.",
        "Introduction\nWe ask whether a denser upstream layout improves valid readings.",
        "Methods\nPaired sensors at 40 sites were calibrated weekly over six months.",
        "Figure 1. Sensor placement across study sites.\nResults\nThe proposed "
        "layout increased valid readings by 18 percent compared with baseline.",
        "Table 1. Weekly valid readings.\nDiscussion\nFlood periods reduced both "
        "layouts' reliability, so this result may not generalize to severe storms.",
    ] + ["Figure 2. Sensor diagnostics.\nAppendix\nCalibration records."] * 3
    report = ["NATIONAL INFRASTRUCTURE REVIEW\nContents\nExecutive Summary ... 2\n"
              "Findings ... 30\nRecommendations ... 180"]
    report += ["NATIONAL INFRASTRUCTURE REVIEW\nExecutive Summary\nThis review "
               "surveys maintenance across regions and identifies a backlog of repairs."]
    report += [f"NATIONAL INFRASTRUCTURE REVIEW\nFindings\nInspection page {i}: "
               "records describe bridge maintenance, roads, and water systems. "
               "This is descriptive evidence, not an experiment." for i in range(3, 181)]
    report += ["NATIONAL INFRASTRUCTURE REVIEW\nRecommendations\nPrioritize "
               "high-risk repairs and publish a public progress dashboard. "
               "Funding gaps remain a limitation."] * 20
    return [("ml-demo", None), ("policy-essay", pdf(policy)),
            ("strategy-30-page", pdf(strategy)),
            ("journal-figures", pdf(journal)),
            ("report-200-page", pdf(report))]


def client_for(base: str) -> httpx.Client:
    return httpx.Client(base_url=base.rstrip("/"), timeout=180,
                        headers={"X-Owner-Token": "ss_" + secrets.token_urlsafe(24)})


def ask(client: httpx.Client, paper_id: str, question: str) -> dict:
    events = []
    started = time.monotonic()
    first_token = None
    with client.stream("POST", "/api/chat/stream",
                       json={"paper_id": paper_id, "message": question}) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith("data:"):
                event = json.loads(line[5:].strip())
                if event.get("type") == "token" and first_token is None:
                    first_token = time.monotonic()
                events.append(event)
    assert events and events[-1].get("type") == "done", "stream did not finish"
    visible = ""
    for event in events:
        if event.get("type") == "fallback":
            visible = ""
        elif event.get("type") == "token":
            visible += event.get("text", "")
        elif event.get("type") == "error":
            visible = event.get("message", "")
    done = events[-1]
    assert visible.strip() and done.get("mode") != "error", "blank/error answer"
    if str(done.get("mode", "")).startswith("synthesized"):
        cards = next((e.get("evidence") or [] for e in events
                      if e.get("type") == "evidence"), [])
        refs = [int(n) for n in re.findall(r"\[(\d+)\]", visible)]
        assert refs and all(1 <= n <= len(cards) for n in refs), "invalid citation"
    done["first_token_s"] = round((first_token or time.monotonic()) - started, 3)
    done["full_answer_s"] = round(time.monotonic() - started, 3)
    return done


def wait_ready(client: httpx.Client, paper_id: str, timeout: int = 240) -> dict:
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        response = client.get(f"/api/papers/{paper_id}")
        response.raise_for_status()
        state = response.json()
        if state["status"] == "ready":
            return state
        if state["status"] == "failed":
            raise AssertionError("analysis failed: " + str(state.get("error")))
        time.sleep(0.5)
    raise TimeoutError("analysis did not become ready")


def run_document(base: str, name: str, blob: bytes | None, cloud_max: int) -> dict:
    client = client_for(base)
    paper_id = None
    start = time.monotonic()
    try:
        if blob is None:
            response = client.post("/api/papers/demo")
        else:
            response = client.post("/api/papers/upload",
                                   files={"files": (name + ".pdf", blob, "application/pdf")})
        response.raise_for_status()
        payload = response.json()
        paper_id = payload["id"] if blob is None else payload["created"][0]["id"]
        state = wait_ready(client, paper_id)
        ready_s = round(time.monotonic() - start, 2)
        local_answers = [ask(client, paper_id, question) for question in QUESTIONS]
        before = client.get("/api/me/usage").json()["provider_calls"]
        cloud_answers = []
        if cloud_max:
            client.post("/api/me/cloud-ai", json={"enabled": True}).raise_for_status()
            for question in QUESTIONS[:cloud_max]:
                cloud_answers.append(ask(client, paper_id, question))
        after = client.get("/api/me/usage").json()["provider_calls"]
        first_tokens = sorted(a["first_token_s"] for a in local_answers)
        full_answers = sorted(a["full_answer_s"] for a in local_answers)
        report = {"genre": name, "pages": state["page_count"], "ready_s": ready_s,
                "local_questions": len(QUESTIONS), "cloud_questions": cloud_max,
                "provider_calls": after - before,
                "local_first_token_p95_s": first_tokens[11],
                "local_full_answer_p95_s": full_answers[11]}
        if cloud_answers:
            report["cloud_first_token_max_s"] = max(
                a["first_token_s"] for a in cloud_answers)
            report["cloud_full_answer_max_s"] = max(
                a["full_answer_s"] for a in cloud_answers)
        return report
    finally:
        if paper_id:
            erased = client.delete("/api/me/purge")
            if erased.status_code != 200:
                print(f"WARNING: session purge failed for {name}", file=sys.stderr)
        client.close()


def run_concurrency(base: str) -> None:
    client = client_for(base)
    paper_id = None
    try:
        response = client.post("/api/papers/demo")
        response.raise_for_status()
        paper_id = response.json()["id"]
        wait_ready(client, paper_id)

        def upload():
            return client.post("/api/papers/upload", files={"files": (
                "parallel.pdf", pdf(["Parallel upload\nIntroduction\nThis document tests "
                                     "concurrent ingestion and search with valid text."]),
                "application/pdf")})

        with ThreadPoolExecutor(max_workers=6) as pool:
            chats = [pool.submit(ask, client, paper_id, QUESTIONS[i]) for i in range(5)]
            upload_future = pool.submit(upload)
            for future in chats:
                future.result()
            uploaded = upload_future.result()
        uploaded.raise_for_status()
        wait_ready(client, uploaded.json()["created"][0]["id"])
    finally:
        if paper_id:
            client.delete("/api/me/purge")
        client.close()


def run_empty_pdf(base: str) -> None:
    """A valid but textless PDF must fail with a useful explanation."""
    client = client_for(base)
    paper_id = None
    try:
        response = client.post("/api/papers/upload", files={"files": (
            "empty.pdf", pdf([""]), "application/pdf")})
        response.raise_for_status()
        paper_id = response.json()["created"][0]["id"]
        until = time.monotonic() + 60
        while time.monotonic() < until:
            state = client.get(f"/api/papers/{paper_id}").json()
            if state["status"] in {"ready", "failed"}:
                break
            time.sleep(0.5)
        assert state["status"] == "failed", "empty PDF was accepted as analyzable"
        error = state.get("error") or ""
        assert error.strip() and "Traceback" not in error and "scanned" in error.lower()
    finally:
        if paper_id:
            client.delete("/api/me/purge")
        client.close()


def run_search_benchmark(base: str) -> dict:
    """Measure owner-scoped search across five analyzed documents."""
    client = client_for(base)
    uploaded_ids = []
    try:
        demo = client.post("/api/papers/demo")
        demo.raise_for_status()
        uploaded_ids.append(demo.json()["id"])
        others = corpus()[1:]
        response = client.post("/api/papers/upload", files=[
            ("files", (name + ".pdf", blob, "application/pdf"))
            for name, blob in others])
        response.raise_for_status()
        uploaded_ids += [p["id"] for p in response.json()["created"]]
        for paper_id in uploaded_ids:
            wait_ready(client, paper_id)
        queries = ["attention architecture", "public research access",
                   "climate observation strategy", "river sensor results",
                   "infrastructure maintenance"] * 4
        durations = []
        for query in queries:
            started = time.monotonic()
            result = client.post("/api/search", json={"query": query})
            result.raise_for_status()
            assert isinstance(result.json()["results"], list)
            durations.append(time.monotonic() - started)
        durations.sort()
        return {"documents": len(uploaded_ids), "queries": len(queries),
                "search_p95_s": round(durations[18], 3)}
    finally:
        if uploaded_ids:
            client.delete("/api/me/purge")
        client.close()


def run_soak(base: str, pace: float = 2.1) -> dict:
    """Fifty identical cloud questions should reuse one answer, without drift."""
    client = client_for(base)
    paper_id = None
    try:
        response = client.post("/api/papers/demo")
        response.raise_for_status()
        paper_id = response.json()["id"]
        wait_ready(client, paper_id)
        client.post("/api/me/cloud-ai", json={"enabled": True}).raise_for_status()
        cached, durations = 0, []
        question = "Why does attention help model relationships between distant words?"
        for index in range(50):
            started = time.monotonic()
            done = ask(client, paper_id, question)
            durations.append(time.monotonic() - started)
            cached += bool(done.get("cached"))
            if index < 49:
                time.sleep(pace)
        calls = client.get("/api/me/usage").json()["provider_calls"]
        hit_ratio = cached / 50
        assert calls > 0, (
            "cache soak unverified: no valid cloud answer was produced; "
            "check provider quota or availability and retry")
        assert hit_ratio >= 0.4, f"cache hit ratio below 40%: {hit_ratio:.0%}"
        assert calls <= 2, f"identical question sent {calls} provider calls"
        first = sorted(durations[:25])[12]
        last = sorted(durations[25:])[12]
        assert last < max(2.0, first * 3), "answer latency degraded during soak"
        return {"questions": 50, "cache_hit_ratio": round(hit_ratio, 2),
                "provider_calls": calls, "median_first_s": round(first, 3),
                "median_last_s": round(last, 3)}
    finally:
        if paper_id:
            client.delete("/api/me/purge")
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    parser.add_argument("--only-demo", action="store_true")
    parser.add_argument("--cloud-max", type=int, default=0,
                        help="cloud questions per document, from 0 to 12")
    parser.add_argument("--concurrency", action="store_true")
    parser.add_argument("--search-benchmark", action="store_true")
    parser.add_argument("--soak", action="store_true",
                        help="run 50 paced cloud questions in one disposable session")
    args = parser.parse_args()
    if not 0 <= args.cloud_max <= len(QUESTIONS):
        parser.error("--cloud-max must be 0 to 12")
    documents = [("ml-demo", None)] if args.only_demo else corpus()
    reports = []
    for name, blob in documents:
        report = run_document(args.base, name, blob, args.cloud_max)
        reports.append(report)
        print(json.dumps(report, sort_keys=True))
    if args.concurrency:
        run_concurrency(args.base)
        print("concurrency: five chats plus upload passed")
    if args.search_benchmark:
        print("search:", json.dumps(run_search_benchmark(args.base), sort_keys=True))
    run_empty_pdf(args.base)
    print("empty PDF: helpful failure passed")
    if args.soak:
        print("soak:", json.dumps(run_soak(args.base), sort_keys=True))
    print("PASS", len(reports), "genres", sum(r["local_questions"] for r in reports),
          "local questions", sum(r["cloud_questions"] for r in reports), "cloud questions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
