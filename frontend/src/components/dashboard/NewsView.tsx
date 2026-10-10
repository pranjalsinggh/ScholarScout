import { motion } from "framer-motion";
import { ExternalLink, Newspaper } from "lucide-react";
import { GlassCard, Chip, SectionTitle, stagger } from "../glass/Glass";
import type { NewsItem, NewsResponse } from "../../types/api";
import { SectionState, useSectionResource } from "./SectionState";

const TIERS: [keyof Pick<Record<string, NewsItem[]>, "academic" | "news" | "commentary">, string, string][] = [
  ["academic", "Academic evidence", "Research publications and preprints with source links."],
  ["news", "News reporting", "Recent coverage from news organizations indexed by GDELT."],
  ["commentary", "Public commentary", "Blog and discussion sources when identifiable."],
];

function emptyCoverage(tier: string, data: NewsResponse) {
  if (data.status === "disabled") return "Enable connected research above to look for external sources.";
  const failedSources = data.sources?.filter((source) => {
    const relevant = tier === "academic" ? /arxiv|openalex|crossref/i.test(source.provider)
      : tier === "news" ? /gdelt|newsapi/i.test(source.provider) : false;
    return relevant && ["unavailable", "rate_limited", "error"].includes(source.status);
  });
  if (data.status === "unavailable" || failedSources?.length) {
    return "A source could not be checked. This is not evidence that no coverage exists. Check the source status below or refresh sources later.";
  }
  if (data.status === "insufficient_topic") return "A reliable topic is needed before these sources can be searched. Local document analysis remains available.";
  if (tier === "commentary") return "Only identifiable commentary with a source is included.";
  return "No relevant items were returned by the available sources. This does not affect the PDF analysis.";
}

function NewsCard({ n, i }: { n: NewsItem; i: number }) {
  return (
    <motion.div {...stagger(Math.min(i * 0.04, 0.4))}>
      <GlassCard className="glass-ring h-full p-4">
        <div className="flex items-start justify-between gap-3">
          <h4 className="text-sm font-medium leading-snug text-white">{n.headline}</h4>
          {n.url && (
            <a href={n.url} target="_blank" rel="noreferrer" className="shrink-0 rounded-lg p-1.5 text-slate-500 hover:bg-white/10 hover:text-azure-300">
              <ExternalLink size={14} />
            </a>
          )}
        </div>
        <div className="mt-1 text-[11px] text-faint">
          {n.publisher}{n.date ? ` · ${n.date}` : ""}
        </div>
        {n.summary && <p className="mt-2 text-xs leading-relaxed text-slate-400">{n.summary}</p>}
        {n.connection && <div className="mt-2"><Chip>{n.connection}</Chip></div>}
      </GlassCard>
    </motion.div>
  );
}

export default function NewsView({ paperId }: { paperId: string }) {
  const { data, error, retry } = useSectionResource<NewsResponse>(`/papers/${paperId}/news`);
  const STATUS_LABELS: Record<string, string> = {
    partial: "Some research sources are unavailable",
    empty: "No relevant recent coverage found",
    unavailable: "Research sources are temporarily unavailable",
    insufficient_topic: "A reliable search topic could not be identified",
    disabled: "Connected research is off",
  };

  return (
    <div className="space-y-8">
      <SectionTitle title="Latest developments" icon={<Newspaper size={18} />} sub="External coverage of this document's topics. Academic works, news reporting and commentary have separate source labels." />
      {error ? <SectionState error title="Latest developments could not load" onRetry={retry}>{error}</SectionState>
        : !data ? <SectionState loading title="Checking external research sources…">Searching for relevant coverage may take longer than local document analysis.</SectionState>
        : <>
      {(data.note || (data.status && data.status !== "ok")) && (
        <SectionState title={STATUS_LABELS[data.status || ""] || "Coverage information"}>{data.note || "Coverage depends on the document topic and the available source index. Your local evidence and answers remain available."}</SectionState>
      )}
      {TIERS.map(([tier, label, desc]) => {
        const items = data[tier];
        return (
          <div key={tier}>
            <h3 className="text-sm font-semibold text-slate-200">{label}</h3>
            <p className="mb-3 text-xs text-faint">{desc}</p>
            {!items || items.length === 0 ? (
              <SectionState title={`No ${label.toLowerCase()} to show`}>
                {emptyCoverage(tier, data)}
              </SectionState>
            ) : (
              <div className="grid gap-4 md:grid-cols-2">
                {items.slice(0, 8).map((n, i) => <NewsCard key={i} n={n} i={i} />)}
              </div>
            )}
          </div>
        );
      })}
      {!!data.sources?.length && <details className="glass-soft p-4 text-xs text-dim"><summary className="cursor-pointer text-slate-300">Research source status</summary><div className="mt-3 space-y-3">{data.sources.map((source, i) => <div key={`${source.provider}-${i}`}><div className="flex flex-wrap items-center gap-2"><span className="font-medium text-slate-200">{source.provider}</span><Chip tone={["unavailable", "rate_limited", "error"].includes(source.status) ? "warn" : "default"}>{source.status.replace(/_/g, " ")}</Chip></div>{source.note && <p className="mt-1 leading-relaxed">{source.note}</p>}{source.query && <p className="mt-1 break-words text-faint">Search: {source.query}</p>}</div>)}</div></details>}
      </>}
    </div>
  );
}
