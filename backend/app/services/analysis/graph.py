"""Interactive research graph builder - papers, people, institutions, methods,
datasets, topics, events with typed relationships."""
from __future__ import annotations

from collections import Counter

from sqlalchemy.orm import Session

from app.models.entities import Author, Citation, Paper, ResearchEvent
from app.schemas.api import CitationEdge, CitationNode, GraphResponse


def build_graph(db: Session, paper_ids: list[str] | None = None,
                owner: str = "") -> GraphResponse:
    q = db.query(Paper).filter(Paper.status == "ready")
    if owner:
        q = q.filter(Paper.owner == owner)
    papers = q.all() if paper_ids is None else q.filter(Paper.id.in_(paper_ids)).all()
    if not papers:
        return GraphResponse(nodes=[], edges=[], note="No processed papers yet.")

    nodes: dict[str, CitationNode] = {}
    edges: list[CitationEdge] = []
    focus = papers[0]

    def add(node: CitationNode) -> str:
        nodes[node.id] = node
        return node.id

    def edge(s: str, t: str, kind: str, weight: float = 1.0) -> None:
        if s != t:
            edges.append(CitationEdge(source=s, target=t, kind=kind, weight=weight))

    # ---- paper nodes ----
    for p in papers:
        pid = f"paper:{p.id}"
        add(CitationNode(id=pid, label=(p.title or "Untitled")[:90], kind="paper",
                         year=p.year, meta={"main": p.id == focus.id, "citations": (p.meta or {}).get("openalex_cited_by")}))
        for a in (p.authors or [])[:8]:
            aid = f"author:{a['name'].lower()}"
            add(CitationNode(id=aid, label=a["name"], kind="author"))
            edge(pid, aid, "authored_by")
            if a.get("affiliation"):
                iid = f"inst:{a['affiliation'].lower()}"
                add(CitationNode(id=iid, label=a["affiliation"], kind="institution"))
                edge(aid, iid, "affiliated_with")
        for m in ((p.meta or {}).get("methods") or [])[:5]:
            mid = f"method:{m.lower()}"
            add(CitationNode(id=mid, label=m.title(), kind="method"))
            edge(pid, mid, "uses_method")
        for d in ((p.meta or {}).get("datasets") or [])[:5]:
            did = f"dataset:{d.lower()}"
            add(CitationNode(id=did, label=d, kind="dataset"))
            edge(pid, did, "uses_dataset")

    # ---- citation ecosystem (citing works as paper nodes) ----
    top_citing = db.query(Citation).filter(
        Citation.paper_id == focus.id, Citation.direction == "citing"
    ).order_by(Citation.cited_by_count.desc()).limit(18).all()
    for c in top_citing:
        cid = f"cite:{c.id}"
        add(CitationNode(id=cid, label=(c.title or "?")[:90], kind="paper", year=c.year,
                         meta={"external": True, "challenges": bool(c.is_contradictory),
                               "replication": bool(c.is_replication), "extends": bool(c.is_extension),
                               "cited_by": c.cited_by_count}))
        edge(cid, f"paper:{focus.id}", "cites")
        for name in (c.authors or [])[:2]:
            aid = f"author:{name.lower()}"
            add(CitationNode(id=aid, label=name, kind="author"))
            edge(cid, aid, "authored_by")
            for inst in (c.institution_hints or [])[:1]:
                iid = f"inst:{inst.lower()}"
                add(CitationNode(id=iid, label=inst, kind="institution"))
                edge(aid, iid, "affiliated_with")

    # Essays and reports may have topic literature without a verified citation
    # record. Show that context with its actual relationship, never as cites.
    related = db.query(Citation).filter(
        Citation.paper_id == focus.id, Citation.direction == "related"
    ).order_by(Citation.cited_by_count.desc()).limit(10).all()
    for c in related:
        if not c.title:
            continue
        cid = f"cite:{c.id}"
        add(CitationNode(id=cid, label=c.title[:90], kind="paper", year=c.year,
                         meta={"external": True, "topic_match":
                               (focus.meta or {}).get("related_source") == "topic_search",
                               "connection": c.snippet or "Related research record",
                               "cited_by": c.cited_by_count}))
        edge(f"paper:{focus.id}", cid, "related_to")

    # ---- roles: extenders / challengers ----
    authors = db.query(Author).filter(Author.paper_id == focus.id,
                                      Author.role.in_(["extender", "challenger"])).all()
    for a in authors:
        aid = f"author:{a.name.lower()}"
        if aid in nodes:
            edge(aid, f"paper:{focus.id}", "extends" if a.role == "extender" else "challenges")

    # ---- timeline events ----
    events = db.query(ResearchEvent).filter(ResearchEvent.paper_id == focus.id) \
        .order_by(ResearchEvent.date_year).limit(8).all()
    for e in events:
        if e.kind in ("publication",):
            continue
        eid = f"event:{e.id}"
        add(CitationNode(id=eid, label=e.title[:80], kind="event", year=e.date_year,
                         meta={"kind": e.kind}))
        edge(eid, f"paper:{focus.id}", e.kind if e.kind in ("challenges", "extends") else "related_to")

    # trim to a readable size
    if len(nodes) > 80:
        keep = set(list(nodes)[:80])
        nodes = {k: v for k, v in nodes.items() if k in keep}
        edges = [e for e in edges if e.source in keep and e.target in keep]

    return GraphResponse(
        nodes=list(nodes.values()), edges=edges,
        note="Click nodes to inspect; filter by type; edges are typed relationships from indexed data.",
    )
