"""Offline production smoke gate, using synthetic PDFs and disposable storage.

Run from the repository root: backend/.venv/Scripts/python.exe scripts/release_gate.py
This checks the application configuration, not Render's live TLS/network edge.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="scholarscout-release-") as directory:
        os.environ.update({
            "APP_ENV": "production", "ALLOWED_HOSTS": "testserver",
            "SCHOLARSCOUT_SECRET_KEY": secrets.token_urlsafe(48),
            "SCHOLARSCOUT_DATA_DIR": directory,
            "DATABASE_URL": f"sqlite:///{Path(directory).as_posix()}/release.db",
            "CLOUD_AI_ENABLED": "0", "CLOUD_PROVIDER_DATA_APPROVED": "0",
            "SERVE_FRONTEND": "1", "DEMO_AUTOLOAD": "0",
            "FRONTEND_DIST_DIR": str(root / "frontend" / "dist"),
            "GEMINI_API_KEY": "", "GROQ_API_KEY": "", "OPENAI_API_KEY": "",
            "LLM_PROVIDER_CHAIN": "gemini,groq",
            "OPENALEX_API_KEY": "", "NEWS_API_KEY": "",
        })
        sys.path.insert(0, str(root / "backend"))
        import fitz
        from fastapi.testclient import TestClient
        from app.main import app
        from app.security.owner import new_token
        from app.database.database import SessionLocal, engine
        from app.models.entities import ChatMessage, OwnerSession, Paper
        from app.security.session import cloud_ai_allowed

        def pdf(text, pages=1):
            doc = fitz.open()
            for number in range(pages):
                page = doc.new_page()
                page.insert_textbox(fitz.Rect(50, 50, 290, 780), text, fontsize=11)
                if pages > 1:
                    page.insert_textbox(fitz.Rect(320, 50, 550, 780),
                        f"Evaluation passage {number + 1}\n" +
                        ("Controlled observations compare attention mechanisms with baseline models. "
                         "Validation measures accuracy and records limitations across independent trials.\n" * 10),
                        fontsize=11)
            content = doc.tobytes()
            doc.close()
            return content

        checks = 0
        with TestClient(app, base_url="https://testserver") as client:
            with engine.connect() as connection:
                assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
                assert connection.exec_driver_sql("PRAGMA secure_delete").scalar() == 1
                checks += 2
            response = client.get("/api/health")
            assert response.status_code == 200 and set(response.json()) == {"status"}
            assert response.headers["Strict-Transport-Security"].startswith("max-age=")
            checks += 2
            assert client.get("/api/papers").status_code == 401
            assert client.get("/api/health", headers={"Host": "attacker.invalid"}).status_code == 400
            assert client.get("/api/papers", headers={"X-Owner-Token": "ss_short"}).status_code == 401
            # TestClient exposes cleartext through an explicit URL.
            assert client.get("http://testserver/api/me/privacy", headers={"X-Owner-Token": new_token()}).status_code == 426
            checks += 4
            alice, bob = new_token(), new_token()
            ah, bh = {"X-Owner-Token": alice}, {"X-Owner-Token": bob}
            # Offline CI intentionally has no provider credentials, regardless of
            # the live Blueprint. Fresh owners must remain local and independent.
            for headers in (ah, bh):
                assert client.get("/api/me/cloud-ai", headers=headers).json() == {"enabled": False, "available": False}
                assert client.get("/api/me/research-lookups", headers=headers).json() == {"enabled": False}
                checks += 2
            assert client.post("/api/me/cloud-ai", headers=ah, json={"enabled": True}).status_code == 409
            assert client.post("/api/me/cloud-ai", headers=ah, json={"enabled": False}).json() == {"enabled": False}
            with SessionLocal() as db:
                sessions = db.query(OwnerSession).all()
                assert len(sessions) == 2 and all(not row.cloud_ai_opt_in for row in sessions)
                assert all(not cloud_ai_allowed(row.owner) for row in sessions)
            checks += 4
            papers = []
            for headers, text in [(ah, "Private Alice Research\nAbstract\nAttention mechanisms select relevant observations.\nMethods\nWe compare observations using controlled validation.\nResults\nValidation supported the attention method."),
                                  (bh, "Private Bob Manuscript\nAbstract\nQuantum oscillations reveal secret measurement patterns.\nMethods\nWe study oscillations using private chamber records.\nResults\nThe chamber observations supported the quantum argument.")]:
                response = client.post("/api/papers/upload", headers=headers,
                                       files={"files": ("synthetic.pdf", pdf(text, pages=15 if headers is ah else 1), "application/pdf")})
                assert response.status_code == 200, response.text
                paper_id = response.json()["created"][0]["id"]
                papers.append(paper_id)
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    state = client.get(f"/api/papers/{paper_id}", headers=headers).json()
                    if state["status"] in {"ready", "failed"}:
                        break
                    time.sleep(0.2)
                assert state["status"] == "ready", state.get("error")
                assert state["page_count"] == (15 if headers is ah else 1)
                checks += 2
            aid, bid = papers
            for path in [f"/api/papers/{bid}", f"/api/papers/{bid}/file", f"/api/papers/{bid}/export", f"/api/chat/{bid}/history"]:
                assert client.get(path, headers=ah).status_code == 404
                checks += 1
            assert client.post("/api/chat", headers=ah, json={"paper_id": bid, "message": "What does it say?"}).status_code == 404
            response = client.post("/api/search", headers=ah, json={"query": "quantum chamber", "paper_ids": [bid]})
            assert response.status_code == 200 and response.json()["results"] == []
            checks += 2
            own_search = client.post("/api/search", headers=ah,
                json={"query": "attention observations validation", "paper_ids": [aid]})
            assert own_search.status_code == 200 and own_search.json()["results"]
            assert all(hit["paper_id"] == aid for hit in own_search.json()["results"])
            checks += 2
            response = client.post("/api/chat", headers=ah, json={"paper_id": aid, "message": "What is this document about?"})
            assert response.status_code == 200 and response.json()["answer"]
            assert response.json()["mode"] == "extractive"
            assert "quantum" not in response.json()["answer"].lower()
            checks += 3
            local = client.post("/api/chat", headers=ah,
                json={"paper_id": aid, "message": "What method does the document use?", "answer_mode": "local"})
            assert local.status_code == 200 and local.json()["mode"] == "extractive"
            assert client.post("/api/chat", headers=ah,
                json={"paper_id": aid, "message": "What method does the document use?", "answer_mode": "cloud"}).status_code == 409
            checks += 2
            assert client.delete("/api/me/purge", headers=ah).status_code == 200
            assert client.get("/api/papers", headers=ah).status_code == 410
            assert client.get(f"/api/papers/{bid}", headers=bh).status_code == 200
            checks += 3
            with SessionLocal() as db:
                assert db.get(Paper, aid) is None
                assert db.query(ChatMessage).filter(ChatMessage.paper_id == aid).count() == 0
            assert client.delete("/api/me/purge", headers=bh).status_code == 200
            assert not list(Path(directory).rglob("*.plain"))
            checks += 4
            if (root / "frontend" / "dist" / "index.html").exists():
                assert "<html" in client.get("/library").text.lower()
                checks += 1
        engine.dispose()
        print(json.dumps({"production_smoke": "passed", "checks": checks,
                          "live_cloud_calls": 0, "storage": "disposable"}))


if __name__ == "__main__":
    main()
