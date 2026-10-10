import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { FileText, Loader2, Search } from "lucide-react";
import { GlassCard, Chip, SectionTitle } from "../glass/Glass";
import { api, fetchAuthFile, friendlyError } from "../../services/api";
import type { SearchHit } from "../../types/api";
import { SectionState } from "./SectionState";

export default function EvidenceView({ paperId, title, keywords = [] }: { paperId: string; title: string; keywords?: string[] }) {
  const starter = (keywords.slice(0, 3).join(" ") || title).slice(0, 180);
  const [query, setQuery] = useState(starter);
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [libraryScope, setLibraryScope] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fileError, setFileError] = useState("");
  const requestId = useRef(0);

  const run = async (q = query, allPapers = libraryScope) => {
    const searchQuery = q.trim();
    if (searchQuery.length < 2) { setError("Enter a topic or question with at least two characters."); return; }
    const id = ++requestId.current;
    setQuery(searchQuery);
    setLoading(true);
    setHits(null);
    setError(null);
    try {
      const result = await api.post<{ results: SearchHit[] }>("/search", {
        query: searchQuery, paper_ids: allPapers ? undefined : [paperId],
      });
      if (requestId.current === id) setHits(result.results);
    } catch (reason) {
      if (requestId.current === id) setError(friendlyError(reason));
    } finally {
      if (requestId.current === id) setLoading(false);
    }
  };

  useEffect(() => {
    setLibraryScope(false);
    setFileError("");
    void run(starter, false);
    return () => { requestId.current += 1; };
  }, [paperId]);

  const openSource = async (hit: SearchHit) => {
    setFileError("");
    try { await fetchAuthFile(`/papers/${hit.paper_id}/file`, undefined, hit.page); }
    catch (reason) { setFileError(friendlyError(reason)); }
  };

  return (
    <div className="space-y-4">
      <SectionTitle title="Search the evidence" sub="Inspect passages from the selected document. You can expand the search to other documents in this session." />
      <div className="flex flex-wrap gap-2" role="group" aria-label="Evidence search scope">
        {[false, true].map((allPapers) => <button key={String(allPapers)} type="button" aria-pressed={libraryScope === allPapers}
          className={`chip ${libraryScope === allPapers ? "chip-accent" : "hover:bg-white/10"}`}
          onClick={() => { setLibraryScope(allPapers); void run(query, allPapers); }}>
          {allPapers ? "Entire session library" : "This document"}
        </button>)}
      </div>
      <form onSubmit={(event) => { event.preventDefault(); void run(); }} className="glass flex items-center gap-2 p-2 pl-4">
        <Search size={17} className="shrink-0 text-slate-500" />
        <input aria-label="Search evidence passages" className="min-w-0 flex-1 bg-transparent text-sm text-white outline-none placeholder:text-slate-500"
          placeholder="Search a topic, claim, or question…" value={query} onChange={(event) => setQuery(event.target.value)} />
        <button type="submit" disabled={loading} className="btn-primary shrink-0 px-4 py-2 text-sm">
          {loading ? <Loader2 size={15} className="animate-spin" /> : "Search"}
        </button>
      </form>
      {!!keywords.length && <div className="flex flex-wrap gap-2">{keywords.slice(0, 5).map((keyword) => <button key={keyword} type="button" className="chip hover:bg-white/10" onClick={() => void run(keyword)}>{keyword}</button>)}</div>}
      {loading && <SectionState loading title={libraryScope ? "Searching this session's documents…" : "Searching this document…"} />}
      {error && <SectionState error title="Evidence search could not complete" onRetry={() => void run()}>{error}</SectionState>}
      {fileError && <SectionState error title="The source PDF could not open">{fileError}</SectionState>}
      {hits !== null && <>
        <p className="text-xs text-faint">{hits.length} passages · {libraryScope ? "session library" : "selected document"}. Relevance scores rank passages; they are not confidence percentages.</p>
        {hits.length === 0 ? <SectionState title="No matching passages found">Try a shorter phrase, a named topic, or words used in the document. Text inside scanned images may not be searchable.</SectionState>
          : <div className="space-y-3">{hits.map((hit, i) => <motion.div key={`${hit.paper_id}-${hit.page}-${i}`} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: Math.min(i * 0.03, 0.3) }}>
            <GlassCard className="glass-ring p-4">
              <div className="flex items-start justify-between gap-3"><div className="min-w-0 text-sm font-medium text-white">{hit.paper_title}</div><Chip tone="azure" className="shrink-0">score {hit.score.toFixed(2)}</Chip></div>
              <div className="mt-1 text-[11px] text-faint">{hit.section || "document"}{hit.page ? ` · page ${hit.page}` : ""}</div>
              <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-slate-300">{hit.snippet}</p>
              {hit.explanation && <div className="mt-2 text-[11px] text-iris-300/80">{hit.explanation}</div>}
              <button type="button" onClick={() => void openSource(hit)} className="mt-3 inline-flex items-center gap-1.5 text-xs text-azure-300 hover:text-azure-200"><FileText size={13} /> Open source PDF{hit.page ? ` · see page ${hit.page}` : ""}</button>
            </GlassCard>
          </motion.div>)}</div>}
      </>}
    </div>
  );
}
