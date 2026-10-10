import { useState } from "react";
import { motion } from "framer-motion";
import { ExternalLink, LibraryBig } from "lucide-react";
import { GlassCard, Chip, SectionTitle, stagger } from "../glass/Glass";
import type { CitationRow, PaperOverview, SimilarPaper } from "../../types/api";
import { SectionState, useSectionResource } from "./SectionState";

export default function Literature({ paper }: { paper: PaperOverview }) {
  const matches = useSectionResource<{ results: SimilarPaper[] }>(`/papers/${paper.id}/similar`);
  const links = useSectionResource<{ citations: CitationRow[] }>(`/papers/${paper.id}/citations`);
  const similar = matches.data?.results;
  const citations = links.data?.citations;
  const [tab, setTab] = useState<"citing" | "referenced" | "related">("citing");

  const filtered = (citations || []).filter((c) => c.direction === tab)
    .sort((a, b) => (b.cited_by_count || 0) - (a.cited_by_count || 0));

  const badges = (c: CitationRow) => (
    <div className="mt-1.5 flex flex-wrap gap-1.5">
      {c.is_replication && <Chip tone="ok">replication</Chip>}
      {c.is_contradictory && <Chip tone="warn">challenge</Chip>}
      {c.is_correction && <Chip tone="danger">correction</Chip>}
      {c.is_extension && <Chip tone="azure">extension</Chip>}
    </div>
  );

  return (
    <div className="space-y-6">
      <div>
        <SectionTitle title="Find similar research" icon={<LibraryBig size={18} />} sub="Embedding-based matches from your library and OpenAlex related works." />
        {matches.error ? <SectionState error title="Related research could not load" onRetry={matches.retry}>{matches.error}</SectionState> : !similar ? (
          <SectionState loading title="Loading related research…" />
        ) : similar.length === 0 ? (
          <SectionState title="No related works to show">Upload another relevant document for library comparisons. External related works require connected research and a confidently matched scholarly record.</SectionState>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {similar.map((s, i) => (
              <motion.div key={`${s.title}-${i}`} {...stagger(i * 0.04)}>
                <GlassCard className="glass-ring h-full p-5">
                  <div className="flex items-start justify-between gap-3">
                    <h3 className="text-sm font-semibold leading-snug text-white">{s.title}</h3>
                    {s.similarity != null && (
                      <div className="shrink-0 text-right">
                        <div className="gradient-text text-xl font-extrabold">{Math.round(s.similarity)}%</div>
                        <div className="text-[10px] uppercase tracking-wider text-faint">similarity</div>
                      </div>
                    )}
                    {s.similarity == null && <Chip>related</Chip>}
                  </div>
                  <div className="mt-1 text-xs text-faint">
                    {s.authors.slice(0, 3).join(", ")}{s.year ? ` · ${s.year}` : ""}
                  </div>
                  <ul className="mt-2.5 space-y-1">
                    {s.reasons.slice(0, 3).map((r, j) => (
                      <li key={j} className="flex items-start gap-1.5 text-xs text-slate-400">
                        <span className="mt-0.5 text-iris-400">→</span>{r}
                      </li>
                    ))}
                  </ul>
                  {s.url && (
                    <a href={s.url} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1 text-xs text-azure-300 hover:text-azure-200">
                      Source <ExternalLink size={11} />
                    </a>
                  )}
                </GlassCard>
              </motion.div>
            ))}
          </div>
        )}
      </div>

      {/* citation ecosystem */}
      <div>
        <SectionTitle
          title="Citation ecosystem"
          sub="From OpenAlex - the network of works this paper builds on and the works that build on it."
        />
        <div className="mb-4 flex flex-wrap gap-2">
          {([["citing", "Cites this paper"], ["referenced", "It cites"], ["related", "Related"]] as const).map(([k, label]) => (
            <button
              key={k}
              onClick={() => setTab(k)}
              className={`chip transition-all ${tab === k ? "chip-accent scale-105" : "opacity-70 hover:opacity-100"}`}
            >
              {label}
              <span className="ml-1 text-[10px] opacity-70">
                {(citations || []).filter((c) => c.direction === k).length}
              </span>
            </button>
          ))}
        </div>

        {links.error ? <SectionState error title="Citation records could not load" onRetry={links.retry}>{links.error}</SectionState> : !citations ? (
          <SectionState loading title="Loading citation records…" />
        ) : filtered.length === 0 ? (
          <SectionState title="No verified works in this category">{paper.ecosystem_note || "Citation links depend on a matching scholarly record and database coverage. An empty category does not mean the document has never been cited."}</SectionState>
        ) : (
          <div className="space-y-3">
            {filtered.slice(0, 30).map((c, i) => (
              <motion.div key={c.id} {...stagger(Math.min(i * 0.02, 0.3))}>
                <GlassCard className="glass-ring p-4">
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <h4 className="text-sm font-medium leading-snug text-white">{c.title}</h4>
                      <div className="mt-1 text-xs text-faint">
                        {c.authors.slice(0, 3).join(", ")}{c.authors.length > 3 && " et al."}
                        {c.year ? ` · ${c.year}` : ""}{c.venue ? ` · ${c.venue}` : ""}
                        {c.cited_by_count ? ` · cited by ${c.cited_by_count}` : ""}
                      </div>
                      {badges(c)}
                    </div>
                    {c.url && (
                      <a href={c.url} target="_blank" rel="noreferrer" className="shrink-0 rounded-lg p-1.5 text-slate-500 hover:bg-white/10 hover:text-azure-300">
                        <ExternalLink size={15} />
                      </a>
                    )}
                  </div>
                </GlassCard>
              </motion.div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
