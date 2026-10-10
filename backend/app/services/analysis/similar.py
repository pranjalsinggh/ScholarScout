"""Similar paper engine - embedding centroid similarity + explained overlap."""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models.entities import Citation, Paper
from app.services.vector_store import store

REASON_CHECKS: list[tuple[str, str]] = [
    (r"\btransformer|attention|bert|gpt|llm\b", "similar method family (transformers)"),
    (r"\bimage|visual|convolution|cnn|vision\b", "similar domain (vision)"),
    (r"\bgraph|network(?:s)?\b", "similar domain (graphs/networks)"),
    (r"\breinforcement|reward|policy|agent\b", "similar domain (RL)"),
    (r"\bprotein|genome|biomedical|clinical|medical|patient\b", "similar domain (biomedical)"),
    (r"\bquantum|qubit\b", "similar domain (quantum)"),
    (r"\blanguage|text|nlp|semantic|translation\b", "similar domain (language)"),
    (r"\bdataset|benchmark|corpus\b", "evaluation-focused work"),
    (r"\btheory|theoretical|proof|analysis\b", "theory-oriented"),
    (r"\befficien\w*|compress|distill|prun\w*|quantiz\w*\b", "efficiency-focused"),
]


def similar_papers(db: Session, paper: Paper, owner: str = "") -> list[dict]:
    """Across the library (embeddings) + within the citation ecosystem (OpenAlex related)."""
    out: list[dict] = []

    # Restrict the candidate set before ranking. A global top-k can be filled
    # entirely by other sessions and hide this owner's actual neighbours.
    library = db.query(Paper).filter(Paper.owner == (owner or paper.owner),
                                     Paper.status == "ready").all()
    allowed = {other.id: other for other in library if other.id != paper.id}
    from app.services.scoped_index import paper_matrix
    snapshot = paper_matrix(owner or paper.owner)
    ranked = []
    if snapshot is not None:
        ids, vectors = snapshot
        if paper.id in ids:
            base = vectors[ids.index(paper.id)]
            ranked = sorted(((pid, float(vectors[index] @ base))
                             for index, pid in enumerate(ids) if pid in allowed),
                            key=lambda hit: (-hit[1], hit[0]))[:6]
    for pid, score in ranked:
        other = allowed[pid]
        if other:
            out.append({
                "paper_id": other.id, "title": other.title, "year": other.year,
                "authors": [a.get("name") for a in (other.authors or [])[:4]],
                "similarity": round(score * 100, 1),
                "reasons": _reasons(paper, other),
                "url": f"https://doi.org/{other.doi}" if other.doi else None,
                "source": "library",
            })

    # 2. ecosystem related (OpenAlex `related_to`) - saved as Citation rows already
    related = db.query(Citation).filter(
        Citation.paper_id == paper.id, Citation.direction == "related"
    ).order_by(Citation.cited_by_count.desc()).limit(8).all()
    for c in related:
        if not c.title:
            continue
        out.append({
            "paper_id": None, "title": c.title, "year": c.year,
            "authors": (c.authors or [])[:4],
            "similarity": None,  # never fabricate a % - OpenAlex gives no score
            "reasons": [c.snippet or "Related via shared references (OpenAlex)"],
            "url": (f"https://doi.org/{c.doi}" if c.doi else
                    f"https://openalex.org/{c.openalex_id}" if c.openalex_id else None),
            "source": "openalex",
        })
    return out


def _reasons(a: Paper, b: Paper) -> list[str]:
    reasons: list[str] = []
    ta = ((a.title or "") + " " + (a.abstract or "")[:1500]).lower()
    tb = ((b.title or "") + " " + (b.abstract or "")[:1500]).lower()
    for rx, why in REASON_CHECKS:
        if re.search(rx, ta) and re.search(rx, tb):
            reasons.append(why)
    ma = {m.lower() for m in (a.meta or {}).get("methods", [])}
    mb = {m.lower() for m in (b.meta or {}).get("methods", [])}
    shared_methods = ma & mb
    if shared_methods:
        reasons.append("shared methods: " + ", ".join(sorted(shared_methods)[:3]))
    da = {d.lower() for d in (a.meta or {}).get("datasets", [])}
    db_ = {d.lower() for d in (b.meta or {}).get("datasets", [])}
    shared_ds = da & db_
    if shared_ds:
        reasons.append("same datasets: " + ", ".join(sorted(shared_ds)[:3]))
    return reasons[:4] or ["semantically similar content"]
