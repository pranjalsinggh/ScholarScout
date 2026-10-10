import { motion } from "framer-motion";
import { ExternalLink, Info } from "lucide-react";
import { GlassCard, Chip, SectionTitle } from "../glass/Glass";
import type { TimelineResponse } from "../../types/api";
import { SectionState, useSectionResource } from "./SectionState";

const KIND_META: Record<string, { tone: "accent" | "azure" | "warn" | "danger" | "ok" | "default"; label: string }> = {
  publication: { tone: "accent", label: "Published" },
  followup: { tone: "azure", label: "Follow-up" },
  replication: { tone: "ok", label: "Replication" },
  contradiction: { tone: "warn", label: "Contradiction" },
  correction: { tone: "warn", label: "Correction" },
  retraction: { tone: "danger", label: "Retraction" },
  newdataset: { tone: "azure", label: "New dataset" },
  milestone: { tone: "accent", label: "Milestone" },
  news: { tone: "default", label: "News" },
};

export default function TimelineView({ paperId }: { paperId: string }) {
  const { data, error, retry } = useSectionResource<TimelineResponse>(`/papers/${paperId}/timeline`);
  const entries = data?.entries;

  return (
    <div>
      <SectionTitle title="Document timeline" sub="Publication details and verified later works when a matching research record is available." />
      {error ? <SectionState error title="Timeline could not load" onRetry={retry}>{error}</SectionState> : !entries ? (
        <SectionState loading title="Loading publication and follow-up events…" />
      ) : entries.length === 0 ? (
        <SectionState title={data?.status === "disabled" ? "Connected research is off" : data?.status === "unavailable" ? "Timeline sources are unavailable" : "No verified timeline events to show"}>
          {data?.note || "The document may have no confirmed publication date or matching scholarly record. Local analysis and evidence search remain available."}
        </SectionState>
      ) : (
        <div className="relative ml-2 border-l border-white/12 pl-6">
          {entries.map((e, i) => {
            const meta = KIND_META[e.kind] || { tone: "default" as const, label: e.kind };
            return (
              <motion.div
                key={i}
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: Math.min(i * 0.05, 0.5), duration: 0.35 }}
                className="relative pb-6 last:pb-0"
              >
                <span
                  className={`absolute -left-[31px] top-1.5 h-3 w-3 rounded-full border-2 border-ink-950 ${
                    e.kind === "publication" ? "bg-iris-500 shadow-[0_0_12px_rgba(16,185,129,0.7)]"
                    : e.kind === "contradiction" || e.kind === "correction" || e.kind === "retraction" ? "bg-amber-400"
                    : "bg-azure-500"
                  }`}
                />
                <GlassCard className="glass-ring p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="gradient-text text-lg font-extrabold">{e.year ?? e.date_text ?? "-"}</span>
                    <Chip tone={meta.tone}>{meta.label}</Chip>
                    {e.confidence === "heuristic" && (
                      <span className="text-[10px] text-faint">keyword-detected</span>
                    )}
                  </div>
                  <h4 className="mt-1.5 text-sm font-medium leading-snug text-white">{e.title}</h4>
                  {e.description && <p className="mt-1 text-xs leading-relaxed text-dim">{e.description}</p>}
                  <div className="mt-2 flex items-center gap-3 text-[11px] text-faint">
                    {e.source_name && <span>{e.source_name}</span>}
                    {e.source_url && (
                      <a href={e.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-azure-300 hover:text-azure-200">
                        source <ExternalLink size={10} />
                      </a>
                    )}
                  </div>
                </GlassCard>
              </motion.div>
            );
          })}
        </div>
      )}
      {!!entries?.length && data?.note && (
        <GlassCard className="mt-6 flex items-start gap-2.5 p-4 text-sm text-slate-400">
          <Info size={15} className="mt-0.5 shrink-0 text-azure-400" />
          {data.note}
        </GlassCard>
      )}
    </div>
  );
}
