import { motion } from "framer-motion";
import { ExternalLink, Scale, ShieldCheck } from "lucide-react";
import { GlassCard, Chip, SectionTitle, stagger } from "../glass/Glass";
import type { DisagreementItem, ReplicationItem, TensionItem } from "../../types/api";
import { SectionState, useSectionResource } from "./SectionState";

const REPL_TONE: Record<string, "ok" | "azure" | "warn" | "danger" | "default"> = {
  supporting: "ok", partial: "azure", contradictory: "warn", failed: "danger", insufficient: "default",
};

export default function DebateView({ paperId }: { paperId: string }) {
  const debate = useSectionResource<{ disagreements: DisagreementItem[]; internal_tensions?: TensionItem[]; note?: string }>(`/papers/${paperId}/debate`);
  const repl = useSectionResource<{ status: string; items: ReplicationItem[]; note?: string }>(`/papers/${paperId}/replication`);
  const disagreements = debate.data?.disagreements;
  const tensions = debate.data?.internal_tensions || [];
  const dNote = debate.data?.note;
  const replication = repl.data;

  return (
    <div className="space-y-10">
      <div>
        <SectionTitle title="Who disagrees?" icon={<Scale size={18} />} sub="Documented challenges to this work, presented neutrally. A listing here does not mean either paper is wrong." />
        {disagreements && tensions.length > 0 && (
          <GlassCard strong className="mb-4 p-5">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-amber-300">
              Open questions the paper itself raises
            </div>
            <ul className="space-y-2">
              {tensions.slice(0, 4).map((t, i) => (
                <li key={i} className="text-sm leading-relaxed text-slate-300">
                  <span className="mr-2 text-amber-400">↳</span>“{t.text}”
                  <span className="ml-2 text-[11px] text-faint">({t.section}{t.page ? ` · p.${t.page}` : ""})</span>
                </li>
              ))}
            </ul>
          </GlassCard>
        )}
        {debate.error ? <SectionState error title="Debate information could not load" onRetry={debate.retry}>{debate.error}</SectionState> : !disagreements ? (
          <SectionState loading title="Loading documented challenges…" />
        ) : disagreements.length === 0 ? (
          <SectionState title="No verified external challenges found">{dNote || "Only source-backed challenges from a matched research record appear here. An empty list does not establish agreement or validate the document's claims."}</SectionState>
        ) : (
          <div className="space-y-4">
            {disagreements.map((d, i) => (
              <motion.div key={i} {...stagger(i * 0.06)}>
                <GlassCard className="glass-ring p-5">
                  <div className="grid gap-4 md:grid-cols-2">
                    <div>
                      <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-widest text-iris-300">Original claim</div>
                      <p className="text-sm leading-relaxed text-slate-300">{d.original_claim}</p>
                    </div>
                    <div className="border-t border-white/10 pt-3 md:border-l md:border-t-0 md:pl-4 md:pt-0">
                      <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-widest text-amber-300">
                        {d.paper_title}{d.year ? ` (${d.year})` : ""}
                      </div>
                      <p className="text-sm leading-relaxed text-slate-300">{d.alternative_finding}</p>
                    </div>
                  </div>
                  {d.why_they_differ && (
                    <p className="mt-3 border-t border-white/8 pt-3 text-xs leading-relaxed text-faint">{d.why_they_differ}</p>
                  )}
                  {d.source_url && (
                    <a href={d.source_url} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1 text-xs text-azure-300 hover:text-azure-200">
                      View paper <ExternalLink size={11} />
                    </a>
                  )}
                </GlassCard>
              </motion.div>
            ))}
          </div>
        )}
      </div>

      <div>
        <SectionTitle title="Has this been replicated?" icon={<ShieldCheck size={18} />} />
        {repl.error ? <SectionState error title="Replication information could not load" onRetry={repl.retry}>{repl.error}</SectionState> : !replication ? (
          <SectionState loading title="Loading replication evidence…" />
        ) : (
          <GlassCard className="p-5">
            <Chip tone={(REPL_TONE[replication.status] || "default") as "ok"} className="mb-4">
              status: {replication.status}
            </Chip>
            {replication.items.length === 0 ? (
              <p className="text-sm leading-relaxed text-faint">{replication.note || "No verified replication reports are available. Historical essays, policy documents and theoretical works may not describe an experiment that can be replicated."}</p>
            ) : (
              <div className="space-y-3">
                {replication.items.map((r, i) => (
                  <div key={i} className="flex items-start justify-between gap-3 border-b border-white/6 pb-3 last:border-0 last:pb-0">
                    <div>
                      <div className="text-sm text-slate-200">{r.paper_title}</div>
                      <div className="mt-1 text-[11px] text-faint">{r.year} · {r.snippet}</div>
                    </div>
                    <Chip tone={(REPL_TONE[r.category] || "default") as "ok"}>{r.category}</Chip>
                  </div>
                ))}
                <p className="pt-1 text-[11px] text-faint">{replication.note}</p>
              </div>
            )}
          </GlassCard>
        )}
      </div>
    </div>
  );
}
