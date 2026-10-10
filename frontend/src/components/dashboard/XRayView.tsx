import { useState } from "react";
import { motion } from "framer-motion";
import { ScanLine, Gauge, ListChecks, Wrench, Baby, GraduationCap, Sparkles, FileText } from "lucide-react";
import { GlassCard, SectionTitle, Chip, stagger } from "../glass/Glass";
import { api, fetchAuthFile, friendlyError } from "../../services/api";
import type { ComplexityResponse, Eli5Response, ReproductionChecklist, XRayField, XRayResponse } from "../../types/api";
import { SectionState, useSectionResource } from "./SectionState";

function EvidenceTag({ f, paperId }: { f: XRayField; paperId: string }) {
  const [error, setError] = useState("");
  if (!f.evidence_section && !f.evidence_page && !f.evidence_snippet) return null;
  return (
    <details className="mt-3 rounded-xl border border-white/10 bg-white/[0.035] px-3 py-2 text-xs">
      <summary className="cursor-pointer text-iris-300 hover:text-iris-200">
        Source passage{f.evidence_section ? ` · ${f.evidence_section}` : ""}
        {f.evidence_page ? ` · page ${f.evidence_page}` : ""}
      </summary>
      <p className="mt-2 border-l-2 border-iris-400/40 pl-3 leading-relaxed text-slate-400">
        {f.evidence_snippet || f.text}
      </p>
      <button type="button" className="mt-2 inline-flex items-center gap-1 text-azure-300 hover:text-azure-200"
        onClick={() => { setError(""); void fetchAuthFile(`/papers/${paperId}/file`, undefined, f.evidence_page).catch((reason) => setError(friendlyError(reason))); }}>
        <FileText size={12} /> Open source PDF{f.evidence_page ? ` · page ${f.evidence_page}` : ""}
      </button>
      {error && <p role="alert" className="mt-2 text-rose-300">{error}</p>}
    </details>
  );
}

const EMPTY_FIELDS: Record<string, string> = {
  "Problem context": "No clear problem statement was found in the extracted text. Check the introduction or ask about a specific section.",
  "Main claim": "No central claim could be identified confidently. This document may present several goals instead of one finding.",
  "Methodology": "No clear method or approach was identified. This may be a policy or strategy document rather than an empirical study.",
  "Dataset": "No named dataset was found. The document may not use a dataset.",
  "Evaluation": "No explicit evaluation method or metric was found in the document.",
};

type Style = "technical" | "eli5";

function StyleToggle({ style, pickStyle, eli5 }: {
  style: Style;
  pickStyle: (s: Style) => void;
  eli5: Eli5Response | null;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-center gap-2">
      <div className="glass inline-flex items-center gap-1 rounded-full p-1">
        <button
          onClick={() => pickStyle("technical")}
          className={`relative flex items-center gap-1.5 rounded-full px-4 py-1.5 text-sm font-medium transition-colors ${
            style === "technical" ? "text-white" : "text-slate-400 hover:text-slate-200"
          }`}
        >
          {style === "technical" && (
            <motion.span
              layoutId="xray-style"
              className="absolute inset-0 rounded-full bg-gradient-to-r from-iris-500/35 to-azure-500/30 shadow-[inset_0_1px_0_rgba(255,255,255,0.15)]"
              transition={{ type: "spring", stiffness: 380, damping: 32 }}
            />
          )}
          <GraduationCap size={14} className="relative z-10" />
          <span className="relative z-10">Technical</span>
        </button>
        <button
          onClick={() => pickStyle("eli5")}
          className={`relative flex items-center gap-1.5 rounded-full px-4 py-1.5 text-sm font-medium transition-colors ${
            style === "eli5" ? "text-white" : "text-slate-400 hover:text-slate-200"
          }`}
        >
          {style === "eli5" && (
            <motion.span
              layoutId="xray-style"
              className="absolute inset-0 rounded-full bg-gradient-to-r from-iris-500/35 to-azure-500/30 shadow-[inset_0_1px_0_rgba(255,255,255,0.15)]"
              transition={{ type: "spring", stiffness: 380, damping: 32 }}
            />
          )}
          <Baby size={14} className="relative z-10" />
          <span className="relative z-10">Explain simply</span>
        </button>
      </div>
      {eli5?.mode === "llm" && style === "eli5" && (
        <Chip tone="accent"><Sparkles size={11} /> AI-rewritten</Chip>
      )}
    </div>
  );
}

export default function XRayView({ paperId }: { paperId: string }) {
  const xrayResource = useSectionResource<XRayResponse>(`/papers/${paperId}/xray`);
  const complexityResource = useSectionResource<ComplexityResponse>(`/papers/${paperId}/complexity`);
  const reproductionResource = useSectionResource<{ checklist: ReproductionChecklist[]; summary: string }>(`/papers/${paperId}/reproduction`);
  const xray = xrayResource.data;
  const complexity = complexityResource.data;
  const repro = reproductionResource.data?.checklist;
  const reproSummary = reproductionResource.data?.summary || "";
  const [style, setStyle] = useState<Style>("technical");
  const [eli5, setEli5] = useState<Eli5Response | null>(null);
  const [eli5Loading, setEli5Loading] = useState(false);

  const pickStyle = (s: Style) => {
    setStyle(s);
    if (s === "eli5" && !eli5 && !eli5Loading) {
      setEli5Loading(true);
      api.get<Eli5Response>(`/papers/${paperId}/eli5`)
        .then(setEli5)
        .catch(() => setEli5({ fields: {}, mode: "deterministic", disclaimer: "Simple view unavailable for this paper." }))
        .finally(() => setEli5Loading(false));
    }
  };

  if (xrayResource.error) {
    return <SectionState error title="Research X-Ray could not load" onRetry={xrayResource.retry}>{xrayResource.error}</SectionState>;
  }

  if (!xray) {
    return <SectionState loading title="Loading the document's research X-Ray…" />;
  }

  const fields: [string, XRayField | null | undefined][] = [
    ["Problem context", xray.research_question],
    ["Main claim", xray.main_claim],
    ["Methodology", xray.methodology],
    ["Dataset", xray.dataset],
    ["Evaluation", xray.evaluation],
  ];

  // ELI5 view ----------------------------------------------
  if (style === "eli5") {
    return (
      <div className="space-y-6">
        <StyleToggle style={style} pickStyle={pickStyle} eli5={eli5} />
        <GlassCard strong className="p-6">
          {eli5Loading ? (
            <div className="space-y-3">
              {[...Array(6)].map((_, i) => <div key={i} className="skeleton h-5 w-full opacity-50" />)}
            </div>
          ) : eli5 && Object.keys(eli5.fields).length > 0 ? (
            <div className="space-y-5">
              <div className="flex items-center gap-2 text-sm text-dim">
                <Sparkles size={15} className="text-iris-400" />
                Explained simply{eli5.mode === "llm" ? " (AI-rewritten)" : ""} - same facts, friendlier words:
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                {[
                  ["research_question", "The problem they address"],
                  ["main_claim", "What they say they found"],
                  ["methodology", "How they studied it"],
                  ["dataset", "What examples they used"],
                  ["evaluation", "How they checked it"],
                  ["reproducibility", "Can others redo it"],
                  ["main_results", "What they found"],
                  ["limitations", "What it can't do yet"],
                ].map(([key, label]) =>
                  (
                    <div key={key} className="glass-soft p-4">
                      <div className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-iris-300">{label}</div>
                      <p className={`text-[15px] leading-relaxed ${eli5.fields[key] ? "text-slate-200" : "text-slate-500"}`}>
                        {eli5.fields[key] || ({
                          research_question: EMPTY_FIELDS["Problem context"],
                          main_claim: EMPTY_FIELDS["Main claim"],
                          methodology: EMPTY_FIELDS.Methodology,
                          dataset: EMPTY_FIELDS.Dataset,
                          evaluation: EMPTY_FIELDS.Evaluation,
                          reproducibility: "No reproducibility details were detected in the document.",
                          main_results: "No explicit result statement was detected in the document.",
                          limitations: "No explicit limitations were detected; this does not mean there are none.",
                        } as Record<string, string>)[key]}
                      </p>
                    </div>
                  ),
                )}
              </div>
              <p className="text-[11px] text-faint">{eli5.disclaimer}</p>
            </div>
          ) : (
            <p className="text-sm text-faint">No simple explanation could be generated for this paper.</p>
          )}
        </GlassCard>
      </div>
    );
  }

  // Technical view (default) -------------------------------
  return (
    <div className="space-y-6">
      <div>
        <StyleToggle style={style} pickStyle={pickStyle} eli5={eli5} />
        <SectionTitle title="Research X-Ray" icon={<ScanLine size={18} />} sub="Findings are drawn from the document text. Open a source passage to check its context." />
        <div className="grid gap-4 md:grid-cols-2">
          {fields.map(([label, f], i) => (
            <motion.div key={label} {...stagger(i * 0.05)}>
              <GlassCard className={`glass-ring h-full p-5 ${i === 0 || i === 1 ? "md:col-span-1" : ""}`}>
                <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-iris-300">{label}</div>
                {f ? (
                  <>
                    <p className="text-sm leading-relaxed text-slate-300">{f.text}</p>
                    <EvidenceTag f={f} paperId={paperId} />
                  </>
                ) : (
                  <p className="text-sm leading-relaxed text-faint">{EMPTY_FIELDS[label]}</p>
                )}
              </GlassCard>
            </motion.div>
          ))}

          {/* results */}
          <motion.div {...stagger(0.25)}>
            <GlassCard className="glass-ring h-full p-5">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-emerald-300">Main Results</div>
              {xray.main_results.length ? (
                <ul className="space-y-2.5">
                  {xray.main_results.slice(0, 5).map((r, i) => (
                    <li key={i} className="text-sm leading-relaxed text-slate-300">
                      <span className="mr-2 text-emerald-400">›</span>{r.text}
                      <EvidenceTag f={r} paperId={paperId} />
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm leading-relaxed text-faint">No explicit result statement was detected. The document may describe goals or recommendations without measured outcomes.</p>
              )}
            </GlassCard>
          </motion.div>

          {/* limitations */}
          <motion.div {...stagger(0.3)}>
            <GlassCard className="glass-ring h-full p-5">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-amber-300">Limitations</div>
              {xray.limitations.length ? (
                <ul className="space-y-2.5">
                  {xray.limitations.slice(0, 5).map((r, i) => (
                    <li key={i} className="text-sm leading-relaxed text-slate-300">
                      <span className="mr-2 text-amber-400">›</span>{r.text}
                      <EvidenceTag f={r} paperId={paperId} />
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm leading-relaxed text-faint">No explicit limitations were detected in the document. This does not mean the work has no limitations.</p>
              )}
            </GlassCard>
          </motion.div>
        </div>
      </div>

      {/* complexity + reproducibility */}
      <div className="grid gap-4 lg:grid-cols-2">
        <motion.div {...stagger(0.35)}>
          <GlassCard className="h-full p-5">
            <div className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-azure-300">
              <Gauge size={14} /> Complexity profile
            </div>
            {complexityResource.error ? <SectionState error title="Complexity profile could not load" onRetry={complexityResource.retry}>{complexityResource.error}</SectionState> : complexity ? (
              <>
                <div className="space-y-3">
                  {complexity.scores.map((s) => (
                    <div key={s.dimension}>
                      <div className="mb-1 flex items-baseline justify-between text-sm">
                        <span className="text-slate-200">{s.dimension}</span>
                        <span className="text-xs text-faint">{s.score}/5</span>
                      </div>
                      <div className="h-1.5 overflow-hidden rounded-full bg-white/10">
                        <motion.div
                          className="h-full rounded-full bg-gradient-to-r from-iris-500 to-azure-500"
                          initial={{ width: 0 }}
                          animate={{ width: `${(s.score / 5) * 100}%` }}
                          transition={{ duration: 0.7, ease: "easeOut" }}
                        />
                      </div>
                      <div className="mt-1 text-[11px] text-faint">{s.rationale}</div>
                    </div>
                  ))}
                </div>
                <div className="mt-4 rounded-xl bg-white/5 p-3 text-xs leading-relaxed text-slate-400">
                  {complexity.disclaimer}
                </div>
              </>
            ) : (
              <div className="skeleton h-40 w-full opacity-50" />
            )}
          </GlassCard>
        </motion.div>

        <motion.div {...stagger(0.4)}>
          <GlassCard className="h-full p-5">
            <div className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-pink-300">
              <ListChecks size={14} /> Can I reproduce this?
            </div>
            {reproductionResource.error ? <SectionState error title="Reproduction checklist could not load" onRetry={reproductionResource.retry}>{reproductionResource.error}</SectionState> : repro ? (
              <>
                <div className="space-y-1.5">
                  {repro.map((c) => (
                    <div key={c.item} className="flex items-start gap-2 text-sm">
                      <span className={c.found ? "text-emerald-400" : "text-slate-600"}>{c.found ? "✓" : "○"}</span>
                      <span className={c.found ? "text-slate-200" : "text-slate-500"}>
                        {c.item}
                        {c.detail && <span className="block text-[11px] text-faint">{c.detail}</span>}
                      </span>
                    </div>
                  ))}
                </div>
                <p className="mt-3 text-[11px] leading-relaxed text-faint">{reproSummary}</p>
              </>
            ) : (
              <div className="skeleton h-40 w-full opacity-50" />
            )}
          </GlassCard>
        </motion.div>
      </div>

      {/* assumptions + reproducibility notes */}
      {(xray.assumptions.length > 0 || xray.reproducibility) && (
        <motion.div {...stagger(0.45)}>
          <SectionTitle title="Assumptions & reproducibility signals" icon={<Wrench size={18} />} />
          <div className="grid gap-4 md:grid-cols-2">
            {xray.assumptions.length > 0 && (
              <GlassCard className="p-5">
                <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">Assumptions detected</div>
                <ul className="space-y-2 text-sm text-slate-300">
                  {xray.assumptions.slice(0, 4).map((a, i) => (
                    <li key={i}>“{a.text}” <EvidenceTag f={a} paperId={paperId} /></li>
                  ))}
                </ul>
              </GlassCard>
            )}
            {xray.reproducibility && (
              <GlassCard className="p-5">
                <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">Reproducibility notes</div>
                <p className="text-sm leading-relaxed text-slate-300">{xray.reproducibility.text}</p>
              </GlassCard>
            )}
          </div>
        </motion.div>
      )}

      <p className="px-1 text-[11px] text-faint">{xray.disclaimer}</p>
    </div>
  );
}
