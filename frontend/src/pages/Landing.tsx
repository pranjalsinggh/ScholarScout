import { motion, useReducedMotion } from "framer-motion";
import { ArrowDown, Play } from "lucide-react";
import { useNavigate } from "react-router-dom";
import {
  ArrowRight, Network, ScanLine, GitCompareArrows,
  MessagesSquare, Users, TrendingUp, FlaskConical, BookOpen, Sparkles,
  FileText, ShieldCheck, Quote, ArrowUpRight, Check,
} from "lucide-react";
import { AuroraField, NetworkBackground } from "../components/glass/Backgrounds";
import { GlassCard, Chip } from "../components/glass/Glass";
import { GlassNavbar } from "../components/glass/GlassNavbar";
import { api } from "../services/api";

const FEATURES = [
  {
    icon: ScanLine,
    title: "Research X-Ray",
    body: "Inspect the problem, claim, method, evidence and limits when the PDF supports them. Open the source passage behind each finding.",
  },
  {
    icon: Network,
    title: "Research Graph",
    body: "Papers, people, institutions, methods and datasets in one interactive map. Zoom, filter, click, explore.",
  },
  {
    icon: GitCompareArrows,
    title: "Debate Tracker",
    body: "Who disagrees? Follow challenges, contradictions and replication attempts - presented neutrally, fully sourced.",
  },
  {
    icon: TrendingUp,
    title: "Timeline & Trends",
    body: "When you enable connected research, explore follow-ups, new methods and corrections from public research databases.",
  },
  {
    icon: MessagesSquare,
    title: "Ask with Evidence",
    body: "Ask questions against retrieved passages, inspect evidence cards, and choose whether to enable cloud synthesis.",
  },
  {
    icon: FlaskConical,
    title: "Future Directions",
    body: "Explore limitations and future-work statements found in the paper, with the source text close at hand.",
  },
];

const STEPS = [
  { n: "01", t: "Upload", d: "Add a text-based research PDF" },
  { n: "02", t: "Analyze", d: "Structure, methods, claims extracted" },
  { n: "03", t: "Explore", d: "Add public context if you choose" },
  { n: "04", t: "Discover", d: "Follow sourced future directions" },
];

export default function Landing() {
  const nav = useNavigate();
  const reducedMotion = useReducedMotion();
  const reveal = (delay = 0) => ({
    initial: reducedMotion ? false as const : { opacity: 0, y: 18 },
    whileInView: { opacity: 1, y: 0 },
    viewport: { once: true, amount: 0.15 },
    transition: { duration: reducedMotion ? 0 : 0.45, delay: reducedMotion ? 0 : delay, ease: [0.22, 1, 0.36, 1] as const },
  });
  return (
    <div className="landing-page relative min-h-screen">
      <AuroraField />
      <NetworkBackground density={40} />
      <GlassNavbar active="home" />

      {/* hero */}
      <section className="mx-auto max-w-6xl px-4 pb-16 pt-32 text-center sm:px-6 sm:pt-40">
        <motion.div
          initial={reducedMotion ? false : { opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: reducedMotion ? 0 : 0.5, ease: [0.22, 1, 0.36, 1] }}
        >
          <Chip tone="accent" className="mb-7">
            <Sparkles size={13} /> Your research, in full context
          </Chip>
          <h1 className="hero-title mx-auto max-w-4xl text-[clamp(2.8rem,7vw,5.6rem)] font-extrabold leading-[1.03] tracking-[-0.055em] text-white">
            Read a paper.
            <br />
            <span className="gradient-text">See the whole story.</span>
          </h1>
          <p className="mx-auto mt-7 max-w-2xl text-base leading-relaxed text-dim sm:text-lg">
            Turn a text-based research PDF into a clear, evidence-linked workspace.
            Choose when to add public citations and later work.
          </p>
          <div className="mt-9 flex flex-col items-stretch justify-center gap-3 sm:flex-row sm:items-center">
            <button
              onClick={() => nav("/upload")}
              className="btn-primary justify-center px-7 py-3 text-base"
            >
              Analyze a Paper <ArrowRight size={17} />
            </button>
            <button
              onClick={async () => {
                try {
                  const p = await api.post<{ id: string }>("/papers/demo");
                  nav(`/paper/${p.id}`);
                } catch { nav("/upload"); }
              }}
              className="btn-ghost justify-center px-6 py-3 text-base"
            >
              <Play size={16} /> Explore Demo
            </button>
          </div>
          <p className="mt-5 flex items-center justify-center gap-2 text-xs text-faint">
            <ShieldCheck size={14} className="text-iris-400" /> No account · Exit deletion request · Automatic idle expiry
          </p>
          <a href="#how" className="mt-7 inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm text-slate-400 transition-colors hover:bg-white/5 hover:text-iris-300">
            See how it works <ArrowDown size={15} />
          </a>
        </motion.div>

        {/* product preview: one consistent glass language across the app */}
        <motion.div
          {...reveal(0.1)}
          className="showcase-shell glass-strong mx-auto mt-14 max-w-5xl p-3 text-left sm:p-5"
        >
          <div className="flex items-center gap-2 border-b border-white/10 px-2 pb-4 sm:px-3">
            <div className="flex gap-1.5" aria-hidden>
              <span className="h-2 w-2 rounded-full bg-rose-300/60" />
              <span className="h-2 w-2 rounded-full bg-amber-300/60" />
              <span className="h-2 w-2 rounded-full bg-emerald-300/60" />
            </div>
            <span className="ml-3 text-xs font-medium tracking-wide text-slate-400">RESEARCH WORKSPACE</span>
            <span className="ml-auto hidden items-center gap-1.5 text-xs text-iris-300 sm:flex">
              <span className="h-1.5 w-1.5 rounded-full bg-iris-400" /> Evidence linked
            </span>
          </div>
          <div className="grid gap-3 pt-3 lg:grid-cols-[1.1fr_0.9fr]">
            <div className="glass-soft p-5 sm:p-6">
              <div className="flex items-start gap-3">
                <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-iris-500/25 to-azure-500/25 text-iris-300">
                  <FileText size={21} />
                </div>
                <div className="min-w-0">
                  <div className="text-xs font-semibold uppercase tracking-[0.16em] text-iris-300">Paper analysis</div>
                  <h2 className="mt-1 text-lg font-semibold text-white sm:text-xl">From PDF to a research map</h2>
                </div>
              </div>
              <p className="mt-5 text-sm leading-relaxed text-dim">
                A structured view of the question, method, results and limitations,
                each connected to the passage that supports it.
              </p>
              <div className="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {[
                  { label: "Question", icon: ScanLine },
                  { label: "Methods", icon: FlaskConical },
                  { label: "Evidence", icon: BookOpen },
                  { label: "Limits", icon: GitCompareArrows },
                ].map((item) => (
                  <div key={item.label} className="preview-tile rounded-xl px-3 py-3">
                    <item.icon size={16} className="mb-2 text-iris-300" />
                    <span className="text-xs font-medium text-slate-200">{item.label}</span>
                  </div>
                ))}
              </div>
              <div className="mt-5 flex items-center gap-2 text-xs text-iris-300">
                <Check size={14} /> Source passages stay one click away
              </div>
            </div>
            <div className="grid gap-3">
              <div className="glass-soft relative overflow-hidden p-5 sm:p-6">
                <div className="preview-ambient absolute -right-10 -top-10 h-36 w-36 rounded-full" aria-hidden />
                <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] text-azure-300">
                  <Quote size={15} /> Ask with evidence
                </div>
                <p className="mt-4 text-sm leading-relaxed text-slate-200 sm:text-base">
                  “What does the evidence actually support?”
                </p>
                <div className="mt-4 border-l-2 border-iris-400/60 pl-4 text-sm leading-relaxed text-dim">
                  Answers draw from relevant passages and show where each point came from.
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  <Chip tone="accent">[1] Methods · p. 4</Chip>
                  <Chip tone="azure">[2] Results · p. 7</Chip>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="glass-soft p-4">
                  <Network size={17} className="text-iris-300" />
                  <div className="mt-2 text-sm font-semibold text-white">Connected work</div>
                  <div className="mt-1 text-xs text-faint">Citations & follow-ups</div>
                </div>
                <div className="glass-soft p-4">
                  <ArrowUpRight size={17} className="text-azure-300" />
                  <div className="mt-2 text-sm font-semibold text-white">Next questions</div>
                  <div className="mt-1 text-xs text-faint">Gaps worth exploring</div>
                </div>
              </div>
            </div>
          </div>
        </motion.div>
      </section>

      {/* how it works */}
      <section id="how" tabIndex={-1} aria-labelledby="how-title" className="mx-auto max-w-6xl scroll-mt-28 px-6 py-16">
        <motion.div {...reveal()} className="mb-10 text-center">
          <h2 id="how-title" className="text-3xl font-bold text-white">A clearer path through research.</h2>
          <p className="mx-auto mt-3 max-w-xl text-dim">Start with your document. Follow the evidence at your own pace.</p>
        </motion.div>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {STEPS.map((s, i) => (
            <motion.div
              key={s.n}
              {...reveal(i * 0.05)}
              className="relative"
            >
              <GlassCard className="p-5 h-full">
                <div className="gradient-text text-2xl font-extrabold">{s.n}</div>
                <div className="mt-2 font-semibold text-white">{s.t}</div>
                <div className="mt-1 text-xs text-faint">{s.d}</div>
              </GlassCard>
              {i < STEPS.length - 1 && (
                <ArrowRight size={14} className="absolute -right-3 top-1/2 z-10 hidden -translate-y-1/2 text-slate-600 sm:block" />
              )}
            </motion.div>
          ))}
        </div>
      </section>

      {/* features */}
      <section id="features" tabIndex={-1} aria-labelledby="features-title" className="mx-auto max-w-6xl scroll-mt-28 px-6 py-16">
        <motion.div {...reveal()}>
        <h2 id="features-title" className="text-center text-3xl font-bold text-white">
          Everything around the paper.
        </h2>
        <p className="mx-auto mt-3 max-w-xl text-center text-dim">
          With connected research enabled, ScholarScout adds public context to your
          paper and shows the source records it found.
        </p>
        </motion.div>
        <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((f, i) => (
            <GlassCard
              key={f.title}
              className="glass-ring card-lift p-6"
              {...reveal((i % 3) * 0.05)}
            >
              <div className="mb-4 inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-iris-500/25 to-azure-500/25 text-iris-300 shadow-[inset_0_1px_0_rgba(255,255,255,0.2)]">
                <f.icon size={20} />
              </div>
              <h3 className="font-semibold text-white">{f.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-dim">{f.body}</p>
            </GlassCard>
          ))}
        </div>
      </section>

      {/* honesty banner */}
      <section className="mx-auto max-w-4xl px-6 py-10">
        <GlassCard strong {...reveal()} className="p-7 text-center">
          <Users size={20} className="mx-auto mb-3 text-azure-400" />
          <p className="text-sm leading-relaxed text-dim">
          ScholarScout links answers to the passages it used and states when the
          evidence is insufficient. External context comes from research databases
          and remains visible for you to verify.
          </p>
        </GlassCard>
      </section>

      {/* footer */}
      <footer className="border-t border-white/5 py-8 text-center text-sm text-faint">
        Don't just read the paper. <span className="gradient-text font-medium">Explore everything around it.</span>
        <a href="https://github.com/pranjalsinggh/ScholarScout" target="_blank" rel="noopener noreferrer" className="mt-2 block text-xs transition-colors hover:text-white">Source code and license notices</a>
      </footer>
    </div>
  );
}
