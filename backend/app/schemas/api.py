"""Pydantic schemas for API requests/responses."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CloudAIRequest(BaseModel):
    enabled: bool


class ResearchLookupRequest(BaseModel):
    enabled: bool


class Stage(BaseModel):
    key: str
    label: str
    state: str = "pending"       # pending|active|done|failed|skipped
    detail: str | None = None


class PaperSummary(BaseModel):
    id: str
    title: str
    filename: str | None = None
    authors: list[dict] = []
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    status: str
    stages: list[Stage] = []
    page_count: int = 0
    citation_count: int = 0
    created_at: str | None = None
    ecosystem_ok: bool = True
    ecosystem_note: str | None = None
    error: str | None = None


class PaperOverview(PaperSummary):
    abstract: str = ""
    keywords: list[str] = []
    methods: list[str] = []
    datasets: list[str] = []
    main_findings: list[str] = []
    limitations: list[str] = []
    funding: list[dict] = []
    conflicts: list[str] = []
    openalex_id: str | None = None
    match_confidence: str | None = None
    match_status: str | None = None
    match_source: str | None = None
    embedding_backend: str = "tfidf"


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    paper_ids: list[str] | None = None


class SearchHit(BaseModel):
    paper_id: str
    paper_title: str
    section: str | None
    page: int | None
    snippet: str
    score: float
    explanation: str


class SearchResponse(BaseModel):
    query: str
    backend: str
    results: list[SearchHit]


class EvidenceCard(BaseModel):
    paper_id: str | None = None
    paper_title: str | None = None
    section: str | None = None
    page: int | None = None
    snippet: str
    score: float = 0.0
    source_type: str = "paper"


class ChatRequest(BaseModel):
    paper_id: str
    message: str = Field(min_length=2, max_length=1000)
    answer_mode: Literal["local", "cloud"] | None = None


class ChatResponse(BaseModel):
    message_id: int | None = None
    answer: str
    evidence: list[EvidenceCard] = []
    mode: str = "extractive"
    cached: bool = False
    insufficient: bool = False
    suggestions: list[str] = []


class FeedbackRequest(BaseModel):
    message_id: int
    rating: str = Field(pattern="^(up|down)$")


class FeedbackResponse(BaseModel):
    ok: bool = True
    profile: dict


class XRayField(BaseModel):
    text: str
    evidence_section: str | None = None
    evidence_page: int | None = None
    evidence_snippet: str | None = None


class XRayResponse(BaseModel):
    research_question: XRayField | None = None
    main_claim: XRayField | None = None
    methodology: XRayField | None = None
    dataset: XRayField | None = None
    evaluation: XRayField | None = None
    main_results: list[XRayField] = []
    limitations: list[XRayField] = []
    assumptions: list[XRayField] = []
    reproducibility: XRayField | None = None
    disclaimer: str = ""


class ComplexityScore(BaseModel):
    dimension: str
    score: int          # 1-5
    rationale: str


class ComplexityResponse(BaseModel):
    scores: list[ComplexityScore]
    overall: int
    prerequisites: list[dict]      # [{topic, needed: bool}]
    disclaimer: str


class CitationNode(BaseModel):
    id: str
    label: str
    kind: str                      # paper|author|institution|method|dataset|topic|event
    year: int | None = None
    meta: dict = {}


class CitationEdge(BaseModel):
    source: str
    target: str
    kind: str                      # cites|extends|challenges|authored_by|affiliated_with|...
    weight: float = 1.0


class GraphResponse(BaseModel):
    nodes: list[CitationNode]
    edges: list[CitationEdge]
    note: str | None = None


class TimelineEntry(BaseModel):
    kind: str
    year: int | None = None
    date_text: str | None = None
    title: str
    description: str | None = None
    source_type: str | None = None
    source_name: str | None = None
    source_url: str | None = None
    confidence: str = "high"


class TimelineResponse(BaseModel):
    entries: list[TimelineEntry]
    note: str | None = None
    status: str = "ok"               # ok|partial|empty|unavailable|unmatched|disabled


class DisagreementItem(BaseModel):
    original_claim: str
    alternative_finding: str
    why_they_differ: str | None = None
    paper_title: str
    year: int | None = None
    source_url: str | None = None
    confidence: str = "heuristic"


class TensionItem(BaseModel):
    text: str
    section: str | None = None
    page: int | None = None


class DebateResponse(BaseModel):
    disagreements: list[DisagreementItem]
    internal_tensions: list[TensionItem] = []
    note: str | None = None


class ReplicationItem(BaseModel):
    category: str                  # supporting|partial|contradictory|failed|insufficient
    paper_title: str
    year: int | None = None
    snippet: str | None = None
    source_url: str | None = None


class ReplicationResponse(BaseModel):
    status: str                    # supporting|partial|contradictory|failed|insufficient|none
    items: list[ReplicationItem]
    note: str | None = None


class PersonProfile(BaseModel):
    name: str
    role: str
    affiliation: str | None = None
    research_areas: list[str] = []
    h_index: int | None = None
    works_count: int | None = None
    notable_works: list[dict] = []
    openalex_id: str | None = None
    profile_url: str | None = None


class PeopleResponse(BaseModel):
    original_authors: list[PersonProfile]
    extenders: list[PersonProfile]
    challengers: list[PersonProfile]
    major_researchers: list[PersonProfile]
    note: str | None = None


class InstitutionItem(BaseModel):
    name: str
    country: str | None = None
    roles: list[str]
    related_papers: int = 0
    openalex_id: str | None = None


class NewsItem(BaseModel):
    headline: str
    date: str | None = None
    publisher: str | None = None
    summary: str | None = None
    connection: str | None = None
    url: str | None = None
    category: str                  # academic|news|commentary


class ResearchSourceStatus(BaseModel):
    provider: str
    status: str                     # ok|no_results|unavailable|rate_limited|disabled
    note: str | None = None
    query: str | None = None


class NewsResponse(BaseModel):
    academic: list[NewsItem]
    news: list[NewsItem]
    commentary: list[NewsItem]
    note: str | None = None
    status: str = "ok"               # ok|partial|empty|unavailable|insufficient_topic|disabled
    sources: list[ResearchSourceStatus] = []


class HealthCheck(BaseModel):
    label: str
    state: str                      # ok|partial|warning|none
    detail: str


class HealthResponse(BaseModel):
    checks: list[HealthCheck]
    disclaimer: str


class GapItem(BaseModel):
    title: str
    why: str
    supporting: list[dict] = []
    direction: str | None = None


class GapsResponse(BaseModel):
    gaps: list[GapItem]
    disclaimer: str


class ReproductionChecklist(BaseModel):
    item: str
    found: bool
    detail: str | None = None


class ReproductionResponse(BaseModel):
    checklist: list[ReproductionChecklist]
    summary: str
    disclaimer: str


class TopicCluster(BaseModel):
    label: str
    terms: list[str]
    share: float
    paper_ids: list[str]


class TopicsResponse(BaseModel):
    topics: list[TopicCluster]
    backend: str
    note: str | None = None


class TrendPoint(BaseModel):
    year: int
    count: int


class TrendSeries(BaseModel):
    topic: str
    status: str                     # established|growing|emerging|declining
    points: list[TrendPoint]


class TrendsResponse(BaseModel):
    series: list[TrendSeries]
    note: str | None = None


class CompareRequest(BaseModel):
    paper_ids: list[str] = Field(min_length=2, max_length=4)


class WatchResponse(BaseModel):
    id: int
    paper_id: str
    label: str
    updates: list[dict] = []
    last_count: int = 0
    new_count: int = 0
