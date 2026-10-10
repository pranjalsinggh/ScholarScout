// Shared API types - mirror of backend schemas

export interface Stage {
  key: string;
  label: string;
  state: "pending" | "active" | "done" | "failed" | "skipped";
  detail?: string;
}

export interface PaperSummary {
  id: string;
  title: string;
  filename?: string | null;
  authors: { name: string; affiliation?: string | null }[];
  year?: number | null;
  venue?: string | null;
  doi?: string | null;
  status: "queued" | "processing" | "ready" | "failed";
  stages: Stage[];
  page_count: number;
  citation_count: number;
  created_at?: string;
  ecosystem_ok: boolean;
  ecosystem_note?: string | null;
  error?: string | null;
}

export interface PaperOverview extends PaperSummary {
  abstract: string;
  keywords: string[];
  methods: string[];
  datasets: string[];
  metrics?: string[];
  figures?: { kind: string; number: number; text: string; page: number }[];
  tables?: { kind: string; number: number; text: string; page: number }[];
  funding: { funder: string; grant?: string | null; quote?: string }[];
  conflicts: string[];
  openalex_id?: string | null;
  match_confidence?: string | null;
  match_status?: string | null;
  match_source?: string | null;
  embedding_backend: string;
}

export interface SearchHit {
  paper_id: string;
  paper_title: string;
  section?: string | null;
  page?: number | null;
  snippet: string;
  score: number;
  explanation: string;
}

export interface EvidenceCard {
  paper_id?: string;
  paper_title?: string;
  section?: string | null;
  page?: number | null;
  snippet: string;
  score: number;
}

export interface ChatResponse {
  message_id?: number | null;
  answer: string;
  evidence: EvidenceCard[];
  mode: string;
  insufficient: boolean;
  suggestions: string[];
  cached?: boolean;
}

export interface Eli5Response {
  fields: Record<string, string>;
  mode: "llm" | "deterministic";
  disclaimer: string;
}

export interface XRayField {
  text: string;
  evidence_section?: string | null;
  evidence_page?: number | null;
  evidence_snippet?: string | null;
}

export interface XRayResponse {
  research_question?: XRayField | null;
  main_claim?: XRayField | null;
  methodology?: XRayField | null;
  dataset?: XRayField | null;
  evaluation?: XRayField | null;
  main_results: XRayField[];
  limitations: XRayField[];
  assumptions: XRayField[];
  reproducibility?: XRayField | null;
  disclaimer: string;
}

export interface ComplexityScore {
  dimension: string;
  score: number;
  rationale: string;
}

export interface ComplexityResponse {
  scores: ComplexityScore[];
  overall: number;
  prerequisites: { topic: string; needed: boolean }[];
  disclaimer: string;
}

export interface CitationRow {
  id: number;
  direction: "referenced" | "citing" | "related";
  title: string;
  authors: string[];
  year?: number | null;
  venue?: string | null;
  cited_by_count: number;
  url?: string | null;
  is_replication: boolean;
  is_contradictory: boolean;
  is_correction: boolean;
  is_extension: boolean;
  snippet?: string | null;
}

export interface SimilarPaper {
  paper_id?: string | null;
  title: string;
  year?: number | null;
  authors: string[];
  similarity?: number | null;
  reasons: string[];
  url?: string | null;
  source: string;
}

export interface TimelineEntry {
  kind: string;
  year?: number | null;
  date_text?: string | null;
  title: string;
  description?: string | null;
  source_type?: string | null;
  source_name?: string | null;
  source_url?: string | null;
  confidence: string;
}

export interface TensionItem {
  text: string;
  section?: string | null;
  page?: number | null;
}

export interface DisagreementItem {
  original_claim: string;
  alternative_finding: string;
  why_they_differ?: string | null;
  paper_title: string;
  year?: number | null;
  source_url?: string | null;
}

export interface ReplicationItem {
  category: string;
  paper_title: string;
  year?: number | null;
  snippet?: string | null;
  source_url?: string | null;
}

export interface PersonProfile {
  name: string;
  role: string;
  affiliation?: string | null;
  research_areas: string[];
  h_index?: number | null;
  works_count?: number | null;
  notable_works: { title: string; year?: number | null; url?: string | null }[];
  profile_url?: string | null;
}

export interface InstitutionItem {
  name: string;
  roles: string[];
  related_papers: number;
}

export interface HealthCheck {
  label: string;
  state: "ok" | "partial" | "warning" | "none";
  detail: string;
}

export interface NewsItem {
  headline: string;
  date?: string | null;
  publisher?: string | null;
  summary?: string | null;
  connection?: string | null;
  url?: string | null;
  category: "academic" | "news" | "commentary";
}

export interface ResearchSourceStatus {
  provider: string;
  status: string;
  note?: string | null;
  query?: string | null;
}

export interface NewsResponse {
  academic: NewsItem[];
  news: NewsItem[];
  commentary: NewsItem[];
  note?: string | null;
  status?: "ok" | "partial" | "empty" | "unavailable" | "insufficient_topic" | "disabled";
  sources?: ResearchSourceStatus[];
}

export interface TimelineResponse {
  entries: TimelineEntry[];
  note?: string | null;
  status?: "ok" | "partial" | "empty" | "unavailable" | "unmatched" | "disabled";
}

export interface GapItem {
  title: string;
  why: string;
  supporting: { title: string; year?: number | null; url?: string | null; cited_by?: number; source?: string }[];
  direction?: string | null;
}

export interface ReproductionChecklist {
  item: string;
  found: boolean;
  detail?: string | null;
}

export interface GraphNode {
  id: string;
  label: string;
  kind: string;
  year?: number | null;
  meta: Record<string, unknown>;
}

export interface GraphEdge {
  source: string;
  target: string;
  kind: string;
  weight: number;
}

export interface TopicCluster {
  label: string;
  terms: string[];
  share: number;
  paper_ids: string[];
}

export interface TrendPoint {
  year: number;
  count: number;
}

export interface TrendSeries {
  topic: string;
  status: string;
  points: TrendPoint[];
}

export interface WatchItem {
  id: number;
  paper_id: string;
  label: string;
  updates: { kind: string; title: string; year?: number; url?: string; date: string }[];
  last_count: number;
  new_count: number;
}
