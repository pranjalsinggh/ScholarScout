import { useState } from "react";
import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import { ArrowRight, BookMarked, GraduationCap, HeartPulse, Landmark, FileWarning, Sparkles } from "lucide-react";
import { GlassCard, Chip, SectionTitle, stagger } from "../glass/Glass";
import type { HealthCheck, PaperOverview, XRayField, XRayResponse } from "../../types/api";
import { SectionState, useSectionResource } from "./SectionState";

const HEALTH_ICON: Record<string, string> = {
  ok: "✓",
  partial: "◐",
  warning: "⚠",
  none: "-",
};
const HEALTH_TONE: Record<string, "ok" | "azure" | "warn" | "default"> = {
  ok: "ok",
  partial: "azure",
  warning: "warn",
  none: "default",
};

export default function Overview({ paper, paperId }: { paper: PaperOverview; paperId: string }) {
  const researchStatus = useSectionResource<{ checks: HealthCheck[] }>(`/papers/${paperId}/health`);
  const brief = useSectionResource<XRayResponse>(`/papers/${paperId}/xray`);
  const health = researchStatus.data?.checks;
  const xray = brief.data;
  const [expanded, setExpanded] = useState(false);

  const meta = paper as unknown as { metrics?: string[]; figures?: { text: string; page: number }[]; tables?: { text: string; page: number }[] };

  return (
    <div className="space-y-6">
      <motion.div {...stagger(0)}>
        <GlassCard strong className="relative overflow-hidden p-5 sm:p-7">
          <div className="pointer-events-none absolute -right-12 -top-20 h-64 w-64 rounded-full bg-iris-500/10 blur-3xl" aria-hidden />
          <div className="relative mb-5 flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-iris-300">
                <Sparkles size={15} /> Research brief
              </div>
              <p className="mt-1 text-sm text-dim">The claim, approach and strongest reported result, drawn from the PDF.</p>
            </div>
            <Link to={`/paper/${paperId}/xray`} className="chip chip-accent inline-flex items-center gap-1.5 hover:bg-iris-500/25">
              Inspect sources <ArrowRight size={13} />
            </Link>
          </div>
          {brief.error && <div className="relative mb-4"><SectionState error title="Research brief could not load" onRetry={brief.retry}>{brief.error}</SectionState></div>}
          {!brief.error && <div className="relative grid gap-3 md:grid-cols-3">
            {([
              ["Core claim", xray?.main_claim],
              ["How it works", xray?.methodology],
              ["Reported result", xray?.main_results?.[0]],
            ] as [string, XRayField | null | undefined][]).map(([label, field]) => (
              <div key={label} className="glass-soft min-w-0 p-4 sm:p-5">
                <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-azure-300">{label}</div>
                {xray === null ? <div className="skeleton h-20 rounded-lg opacity-60" /> : (
                  <>
                    <p className="line-clamp-5 text-sm leading-relaxed text-slate-200">
                      {field?.text || "No reliable statement detected in this paper."}
                    </p>
                    {field?.evidence_section && (
                      <div className="mt-3 text-[11px] text-iris-300">
                        {field.evidence_section}{field.evidence_page ? ` · page ${field.evidence_page}` : ""}
                      </div>
                    )}
                  </>
                )}
              </div>
            ))}
          </div>}
          <p className="relative mt-4 text-[11px] text-faint">Machine extracted. Open the source passage before relying on a claim.</p>
        </GlassCard>
      </motion.div>

      {/* abstract */}
      <motion.div {...stagger(0)}>
        <SectionTitle title="Abstract" icon={<BookMarked size={18} />} />
        <GlassCard className="p-5 text-[14px] leading-relaxed text-slate-300 sm:p-6 sm:text-[15px]">
          <div className={expanded ? "" : "line-clamp-6 sm:line-clamp-none"}>
            {paper.abstract || "No abstract was extracted from this PDF."}
          </div>
          {paper.abstract && paper.abstract.length > 320 && (
            <button onClick={() => setExpanded((v) => !v)} className="mt-2 text-xs font-medium text-iris-300 hover:text-iris-200">
              {expanded ? "Show less" : "Show more"}
            </button>
          )}
        </GlassCard>
      </motion.div>

      {/* topics */}
      <motion.div {...stagger(0.05)}>
        <SectionTitle title="Key concepts" icon={<GraduationCap size={18} />} />
        <div className="grid gap-4 md:grid-cols-2">
          <GlassCard className="glass-ring p-5">
            <div className="mb-3 text-xs font-semibold uppercase tracking-wider text-iris-300">Methods</div>
            <div className="flex flex-wrap gap-2">
              {(paper.methods || []).map((m) => <Chip key={m} tone="accent">{m}</Chip>)}
              {!paper.methods?.length && <span className="text-sm text-faint">None detected</span>}
            </div>
          </GlassCard>
          <GlassCard className="glass-ring p-5">
            <div className="mb-3 text-xs font-semibold uppercase tracking-wider text-azure-300">Datasets</div>
            <div className="flex flex-wrap gap-2">
              {(paper.datasets || []).map((d) => <Chip key={d} tone="azure">{d}</Chip>)}
              {!paper.datasets?.length && <span className="text-sm text-faint">None detected</span>}
            </div>
          </GlassCard>
          <GlassCard className="glass-ring p-5">
            <div className="mb-3 text-xs font-semibold uppercase tracking-wider text-pink-300">Metrics</div>
            <div className="flex flex-wrap gap-2">
              {(meta.metrics || []).map((m) => <Chip key={m}>{m}</Chip>)}
              {!meta.metrics?.length && <span className="text-sm text-faint">None detected</span>}
            </div>
          </GlassCard>
          <GlassCard className="glass-ring p-5">
            <div className="mb-3 text-xs font-semibold uppercase tracking-wider text-amber-300">Keywords</div>
            <div className="flex flex-wrap gap-2">
              {(paper.keywords || []).map((k) => <Chip key={k}>{k}</Chip>)}
              {!paper.keywords?.length && <span className="text-sm text-faint">None detected</span>}
            </div>
          </GlassCard>
        </div>
      </motion.div>

      {/* funding + health */}
      <motion.div {...stagger(0.1)}>
        <SectionTitle title="Research status" icon={<HeartPulse size={18} />} sub="An evidence summary - not a scientific quality score." />
        <div className="grid gap-4 lg:grid-cols-3">
          <GlassCard className="p-5 lg:col-span-2">
            {researchStatus.error ? <SectionState error title="Research status could not load" onRetry={researchStatus.retry}>{researchStatus.error}</SectionState> : !health ? (
              <div className="space-y-2">
                {[...Array(6)].map((_, i) => <div key={i} className="skeleton h-4 w-full opacity-50" />)}
              </div>
            ) : health.length === 0 ? <SectionState title="No research status checks available">Open Evidence to inspect the document directly.</SectionState> : (
              <div className="grid gap-x-6 gap-y-2.5 sm:grid-cols-2">
                {health.map((c) => (
                  <div key={c.label} className="flex items-start gap-2.5 text-sm">
                    <Chip tone={HEALTH_TONE[c.state]} className="shrink-0 !px-2 !py-0.5 font-bold">
                      {HEALTH_ICON[c.state]}
                    </Chip>
                    <div>
                      <span className="font-medium text-slate-200">{c.label}</span>
                      <span className="block text-xs text-faint">{c.detail}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </GlassCard>

          <GlassCard className="p-5">
            <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-emerald-300">
              <Landmark size={13} /> Funding & disclosures
            </div>
            {(paper.funding || []).length ? (
              <ul className="space-y-3">
                {paper.funding.slice(0, 6).map((f, i) => (
                  <li key={i} className="flex items-start gap-2.5">
                    <Landmark size={13} className="mt-0.5 shrink-0 text-emerald-400/80" />
                    <div className="min-w-0">
                      <div className="text-sm font-medium leading-snug text-slate-200">{f.funder}</div>
                      {f.grant && <Chip tone="ok" className="mt-1">grant {f.grant}</Chip>}
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-faint">
                No funding statements detected in the paper - only funders named near explicit
                "supported/funded by" language are counted.
              </p>
            )}

            {(paper as any).conflicts?.length > 0 && (
              <div className="mt-4 border-t border-white/10 pt-3">
                <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-amber-300">
                  Declared conflicts of interest
                </div>
                <ul className="space-y-2">
                  {(paper as any).conflicts.map((c: string, i: number) => (
                    <li key={i} className="text-xs leading-relaxed text-slate-400">“{c}”</li>
                  ))}
                </ul>
              </div>
            )}
            <p className="mt-3 text-[11px] leading-relaxed text-faint">
              Verbatim statements from the paper. Contextual information only - not an indication
              of bias or misconduct.
            </p>
          </GlassCard>
        </div>
      </motion.div>

      {paper.ecosystem_note && (
        <motion.div {...stagger(0.15)}>
          <GlassCard className="flex items-start gap-3 border-amber-400/25 p-4 text-sm text-amber-200/90">
            <FileWarning size={17} className="mt-0.5 shrink-0" />
            {paper.ecosystem_note}
          </GlassCard>
        </motion.div>
      )}
    </div>
  );
}
