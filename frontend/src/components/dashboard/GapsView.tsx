import { motion } from "framer-motion";
import { ExternalLink, Lightbulb } from "lucide-react";
import { GlassCard, Chip, SectionTitle, stagger } from "../glass/Glass";
import type { GapItem } from "../../types/api";
import { SectionState, useSectionResource } from "./SectionState";

function GapCard({ g, i, accent }: { g: GapItem; i: number; accent: string }) {
  return (
    <motion.div {...stagger(Math.min(i * 0.06, 0.4))}>
      <GlassCard className="glass-ring h-full p-5">
        <div className="flex items-start gap-2.5">
          <span className="mt-0.5 shrink-0 text-lg" style={{ color: accent }}>◆</span>
          <div>
            <h3 className="font-semibold leading-snug text-white">{g.title}</h3>
            {g.direction && <Chip className="mt-1.5">{g.direction}</Chip>}
          </div>
        </div>
        <p className="mt-3 text-sm leading-relaxed text-slate-400">{g.why}</p>
        {g.supporting.length > 0 && (
          <div className="mt-3 border-t border-white/8 pt-3">
            <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-widest text-faint">Source evidence</div>
            <ul className="space-y-1">
              {g.supporting.slice(0, 4).map((s, j) => (
                <li key={j} className="text-xs text-slate-400">
                  {s.url ? (
                    <a href={s.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:text-azure-300">
                      {s.title}{s.year ? ` (${s.year})` : ""} <ExternalLink size={10} />
                    </a>
                  ) : (
                    <span>{s.title}{s.year ? ` (${s.year})` : ""}{s.source ? ` - ${s.source}` : ""}</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </GlassCard>
    </motion.div>
  );
}

export default function GapsView({ paperId }: { paperId: string }) {
  const { data, error, retry } = useSectionResource<{ gaps: GapItem[]; disclaimer: string }>(`/papers/${paperId}/gaps`);
  const gaps = data?.gaps;

  return (
    <div className="space-y-6">
      <div>
        <SectionTitle title="Future directions from the paper" icon={<Lightbulb size={18} />} sub="Only explicit future work, limitations, and clearly labeled literature signals appear here." />
        {error ? <SectionState error title="Future directions could not load" onRetry={retry}>{error}</SectionState> : !gaps ? (
          <SectionState loading title="Loading explicit limitations and future directions…" />
        ) : gaps.length === 0 ? (
          <SectionState title="No explicit future direction identified">
            No explicit future-work or limitation statement was detected. That does not mean the research has no open questions; inspect the paper and later literature before proposing one.
          </SectionState>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {gaps.map((g, i) => <GapCard key={i} g={g} i={i} accent="#fbbf24" />)}
          </div>
        )}
      </div>
      {data?.disclaimer && <p className="px-1 text-[11px] text-faint">{data.disclaimer}</p>}
    </div>
  );
}
