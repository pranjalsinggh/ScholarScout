import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { NavLink, useNavigate, useParams } from "react-router-dom";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  ScanLine, Network, Users, GitBranch, MessageSquareText, LibraryBig,
  Newspaper, Swords, Lightbulb, Search, LayoutDashboard, Loader2,
  FlaskConical, Clock, ChevronLeft, CheckCircle2, XCircle, Menu, FileText, Download,
} from "lucide-react";
import { AuroraField } from "../components/glass/Backgrounds";
import { GlassCard, Chip } from "../components/glass/Glass";
import { api } from "../services/api";
import type { PaperOverview } from "../types/api";
import { SectionErrorBoundary } from "../components/glass/SectionErrorBoundary";
import { fetchAuthFile, friendlyError } from "../services/api";
import { PaperActions } from "../components/dashboard/PaperActions";
import { ConnectedResearch } from "../components/dashboard/ConnectedResearch";
import { SectionState } from "../components/dashboard/SectionState";

function dashboardViews() {
  // React and the browser can both remember a failed module download. Retry a
  // local production chunk with a fresh URL while keeping the private session.
  const recover = <T,>(load: () => Promise<T>) => async (): Promise<T> => {
    try { return await load(); }
    catch (reason) {
      const message = reason instanceof Error ? reason.message : "";
      const failedUrl = message.match(/Failed to fetch dynamically imported module:\s*(\S+)/)?.[1];
      if (!failedUrl) throw reason;
      const url = new URL(failedUrl, window.location.href);
      if (url.origin !== window.location.origin || !/^\/assets\/[^/]+\.js$/.test(url.pathname)) throw reason;
      url.searchParams.set("ss_retry", crypto.randomUUID());
      return import(/* @vite-ignore */ url.href) as Promise<T>;
    }
  };
  return {
    Overview: lazy(recover(() => import("../components/dashboard/Overview"))),
    XRayView: lazy(recover(() => import("../components/dashboard/XRayView"))),
    Literature: lazy(recover(() => import("../components/dashboard/Literature"))),
    NetworkView: lazy(recover(() => import("../components/dashboard/NetworkView"))),
    PeopleView: lazy(recover(() => import("../components/dashboard/PeopleView"))),
    TimelineView: lazy(recover(() => import("../components/dashboard/TimelineView"))),
    DebateView: lazy(recover(() => import("../components/dashboard/DebateView"))),
    NewsView: lazy(recover(() => import("../components/dashboard/NewsView"))),
    EvidenceView: lazy(recover(() => import("../components/dashboard/EvidenceView"))),
    GapsView: lazy(recover(() => import("../components/dashboard/GapsView"))),
    ChatView: lazy(recover(() => import("../components/dashboard/ChatView"))),
  };
}

const SECTIONS = [
  { key: "overview", label: "Overview", icon: LayoutDashboard, group: "Understand" },
  { key: "xray", label: "Research X-Ray", icon: ScanLine, group: "Understand" },
  { key: "chat", label: "Ask ScholarScout", icon: MessageSquareText, group: "Understand" },
  { key: "evidence", label: "Evidence", icon: Search, group: "Understand" },
  { key: "literature", label: "Literature", icon: LibraryBig, group: "Explore context" },
  { key: "network", label: "Network", icon: Network, group: "Explore context" },
  { key: "people", label: "People", icon: Users, group: "Explore context" },
  { key: "timeline", label: "Timeline", icon: Clock, group: "Explore context" },
  { key: "debate", label: "Debate", icon: Swords, group: "Explore context" },
  { key: "news", label: "News", icon: Newspaper, group: "Explore context" },
  { key: "gaps", label: "Future directions", icon: Lightbulb, group: "Next steps" },
];

export default function Dashboard() {
  const { paperId, section = "overview" } = useParams();
  const nav = useNavigate();
  const [paper, setPaper] = useState<PaperOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [paperRevision, setPaperRevision] = useState(0);
  const [sectionRevision, setSectionRevision] = useState(0);
  const [fileError, setFileError] = useState("");
  const refreshExpected = useRef(false);
  const [views, setViews] = useState(dashboardViews);
  const paperSnapshot = useRef("");
  const reducedMotion = useReducedMotion();
  const { Overview, XRayView, Literature, NetworkView, PeopleView, TimelineView, DebateView, NewsView, EvidenceView, GapsView, ChatView } = views;

  useEffect(() => { setPaper(null); setError(null); setFileError(""); refreshExpected.current = false; paperSnapshot.current = ""; }, [paperId]);

  useEffect(() => {
    if (!paperId) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let sawResearchRefreshing = refreshExpected.current;
    const tick = () => {
      api.get<PaperOverview>(`/papers/${paperId}`)
        .then((p) => {
          if (!alive) return;
          const snapshot = JSON.stringify(p);
          if (snapshot !== paperSnapshot.current) {
            paperSnapshot.current = snapshot;
            setPaper(p);
          }
          setError(null);
          const researchRefreshing = p.stages?.some((stage) => stage.key === "ecosystem" && stage.state === "active");
          if (sawResearchRefreshing && !researchRefreshing) {
            refreshExpected.current = false;
            setSectionRevision((value) => value + 1);
          }
          sawResearchRefreshing = !!researchRefreshing;
          if ((p.status !== "ready" && p.status !== "failed") || researchRefreshing) {
            timer = setTimeout(tick, 2500);
          }
        })
        .catch((e) => { if (alive) setError(e instanceof Error ? e.message : String(e)); });
    };
    tick();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [paperId, paperRevision]);

  useEffect(() => { setSidebarOpen(false); }, [section]);

  const reloadPaper = () => { setError(null); setPaperRevision((value) => value + 1); };
  const openFile = (download = false) => {
    if (!paper) return;
    setFileError("");
    void fetchAuthFile(download ? `/papers/${paper.id}/export` : `/papers/${paper.id}/file`, download ? `dossier-${paper.id}.md` : undefined)
      .catch((reason) => setFileError(friendlyError(reason)));
  };

  if (error && !paper) {
    return (
      <div className="grid min-h-screen place-items-center px-6">
        <GlassCard strong className="max-w-md p-8 text-center">
          <FlaskConical className="mx-auto mb-3 text-iris-400" size={26} />
          <h2 className="text-lg font-semibold text-white">We couldn't load this dossier</h2>
          <p className="mt-2 text-sm text-dim">{error}</p>
          <button onClick={reloadPaper} className="btn-ghost mt-5 mr-2 px-5 py-2 text-sm">Try again</button>
          <button onClick={() => nav("/library")} className="btn-primary mt-5 px-5 py-2 text-sm">
            Back to library
          </button>
        </GlassCard>
      </div>
    );
  }

  if (!paper) {
    return (
      <div className="grid min-h-screen place-items-center">
        <div className="flex items-center gap-3 text-dim">
          <Loader2 className="animate-spin text-iris-400" size={22} /> Preparing your research dossier…
        </div>
      </div>
    );
  }

  if (paper.status !== "ready") {
    return (
      <div className="grid min-h-screen place-items-center px-6">
        <GlassCard strong className="max-w-md w-full p-8">
          <div className="text-center">
            {paper.status === "failed" ? (
              <>
                <FlaskConical className="mx-auto mb-3 text-rose-400" size={26} />
                <h2 className="text-lg font-semibold text-white">We couldn't process this paper</h2>
                <p className="mt-2 text-sm text-dim">{paper.error || "Something went wrong during analysis."}</p>
                <button onClick={() => nav("/upload")} className="btn-primary mt-5 px-5 py-2 text-sm">
                  Try another upload
                </button>
              </>
            ) : (
              <>
                <Loader2 className="mx-auto mb-3 animate-spin text-iris-400" size={26} />
                <h2 className="text-lg font-semibold text-white">Building your research dossier…</h2>
                <p className="mt-1 text-sm text-dim">This usually takes under a minute.</p>
              </>
            )}
          </div>
          {error && <div className="mt-4"><SectionState error title="Processing updates could not load" onRetry={reloadPaper}>{error}</SectionState></div>}
          {paper.status !== "failed" && (
            <div className="mt-6 space-y-2">
              {(paper.stages || []).map((s) => (
                <div key={s.key} className="flex items-center gap-2.5 text-sm">
                  {s.state === "done" && <CheckCircle2 size={15} className="text-emerald-400" />}
                  {s.state === "active" && <Loader2 size={15} className="animate-spin text-iris-400" />}
                  {s.state === "failed" && <XCircle size={15} className="text-rose-400" />}
                  {(s.state === "pending" || s.state === "skipped") && (
                    <div className="h-3.5 w-3.5 rounded-full border border-slate-600" />
                  )}
                  <span className={s.state === "done" ? "text-slate-300" : s.state === "active" ? "text-iris-200" : "text-slate-500"}>
                    {s.label}{s.detail ? ` · ${s.detail}` : ""}
                  </span>
                </div>
              ))}
            </div>
          )}
        </GlassCard>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen">
      <AuroraField />

      {/* ── sidebar ── */}
      <AnimatePresence>
        {sidebarOpen && (
          <motion.div
            className="fixed inset-0 z-30 bg-black/60 backdrop-blur-sm lg:hidden"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            onClick={() => setSidebarOpen(false)}
          />
        )}
      </AnimatePresence>

      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-white/8 bg-ink-900/70 px-4 py-5 backdrop-blur-2xl transition-transform lg:sticky lg:top-0 lg:h-screen lg:translate-x-0 ${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <NavLink to="/" className="mb-6 flex items-center gap-2.5 px-2">
          <div className="logo-orb h-8 w-8 rounded-full" />
          <span className="text-base font-bold text-white">Scholar<span className="gradient-text">Scout</span></span>
        </NavLink>

        <nav className="flex-1 space-y-1 overflow-y-auto">
          {SECTIONS.map((s, i) => (
            <div key={s.key}>
              {(i === 0 || SECTIONS[i - 1].group !== s.group) && (
                <div className={`px-3 pb-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500 ${i === 0 ? "pt-0" : "pt-5"}`}>
                  {s.group}
                </div>
              )}
              <NavLink
                to={`/paper/${paperId}/${s.key}`}
                className={({ isActive }) =>
                  `flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-all ${
                    isActive
                      ? "bg-gradient-to-r from-iris-500/25 to-azure-500/15 font-semibold text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.12)]"
                      : "text-slate-400 hover:bg-white/5 hover:text-white"
                  }`
                }
              >
                <s.icon size={17} />
                {s.label}
              </NavLink>
            </div>
          ))}
        </nav>

        <NavLink to="/library" className="mt-4 flex items-center gap-2 rounded-xl px-3 py-2.5 text-sm text-slate-400 hover:bg-white/5 hover:text-white">
          <LibraryBig size={16} /> Library
        </NavLink>
      </aside>

      {/* ── main ── */}
      <main className="min-w-0 flex-1">
        {/* top bar - floating liquid glass */}
        <div className="sticky top-3 z-20 mx-3 px-3 py-2.5 glass-strong sm:mx-5 sm:px-5" style={{ borderRadius: "1.25rem" }}>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <button
              onClick={() => setSidebarOpen((v) => !v)}
              className="rounded-lg p-2 text-slate-400 hover:bg-white/10 hover:text-white lg:hidden"
              aria-label={sidebarOpen ? "Close research navigation" : "Open research navigation"}
              aria-expanded={sidebarOpen}
            >
              {sidebarOpen ? <ChevronLeft size={18} /> : <Menu size={18} />}
            </button>
            <div className="min-w-0">
              <h1 className="truncate text-sm font-semibold text-white sm:text-base">
                {paper.title}
              </h1>
              <div className="mt-0.5 hidden flex-wrap items-center gap-x-2 gap-y-1 text-xs text-faint sm:flex">
                {paper.authors.slice(0, 4).map((a) => a.name).join(", ")}
                {paper.authors.length > 4 && ` +${paper.authors.length - 4}`}
                {paper.year ? ` · ${paper.year}` : ""}
                {paper.venue ? ` · ${paper.venue}` : ""}
                {paper.citation_count > 0 ? ` · ${paper.citation_count} citations indexed` : ""}
              </div>
            </div>
            <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
              {paper.match_confidence === "weak" ? (
                <Chip tone="warn">External record unverified</Chip>
              ) : paper.openalex_id ? (
                <Chip tone="azure" className="hidden sm:inline-flex">OpenAlex match</Chip>
              ) : (
                <Chip className="hidden sm:inline-flex">local analysis</Chip>
              )}
              <button
                onClick={() => openFile()}
                className="chip hover:bg-white/10"
                title="Open the paper PDF"
                aria-label="Open the paper PDF"
              >
                <FileText size={12} /> <span className="hidden sm:inline">PDF</span>
              </button>
              <button
                onClick={() => openFile(true)}
                className="chip hover:bg-white/10"
                title="Download the whole dossier as Markdown"
                aria-label="Download the whole dossier as Markdown"
              >
                <Download size={12} /> <span className="hidden sm:inline">Export</span>
              </button>
              {paper.doi && (
                <a
                  href={`https://doi.org/${paper.doi}`}
                  target="_blank"
                  rel="noreferrer"
                  className="chip chip-accent hover:bg-iris-500/25 hidden sm:inline-flex"
                >
                  DOI ↗
                </a>
              )}
              <PaperActions paperId={paper.id} />
            </div>
          </div>
        </div>

        {/* section content */}
        <div className="mx-auto max-w-6xl px-3 py-5 sm:px-8 sm:py-8">
          {error && <div className="mb-4"><SectionState error title="Dossier updates could not load" onRetry={reloadPaper}>{error}</SectionState></div>}
          {fileError && <div className="mb-4"><SectionState error title="The file could not open">{fileError}</SectionState></div>}
          {SECTIONS.find((item) => item.key === section)?.group === "Explore context" && <ConnectedResearch
            paperId={paper.id}
            refreshing={!!paper.stages?.some((stage) => stage.key === "ecosystem" && stage.state === "active")}
            onChange={(expected) => { refreshExpected.current = expected; setPaperRevision((value) => value + 1); setSectionRevision((value) => value + 1); }}
          />}
            <motion.div
              key={`${paperId}-${section}-${sectionRevision}`}
              initial={reducedMotion ? false : { opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
            >
              <SectionErrorBoundary name={SECTIONS.find(s => s.key === section)?.label || section} onRetry={() => setViews(dashboardViews())}>
              <Suspense fallback={<div className="glass-soft flex min-h-48 items-center justify-center gap-2 text-sm text-dim"><Loader2 className="animate-spin text-iris-400" size={17} /> Loading this view…</div>}>
              {section === "overview" && <Overview paper={paper} paperId={paper.id} />}
              {section === "xray" && <XRayView paperId={paper.id} />}
              {section === "literature" && <Literature paper={paper} />}
              {section === "network" && <NetworkView paperId={paper.id} />}
              {section === "people" && <PeopleView paperId={paper.id} />}
              {section === "timeline" && <TimelineView paperId={paper.id} />}
              {section === "debate" && <DebateView paperId={paper.id} />}
              {section === "news" && <NewsView paperId={paper.id} />}
              {section === "evidence" && <EvidenceView paperId={paper.id} title={paper.title} keywords={paper.keywords} />}
              {section === "gaps" && <GapsView paperId={paper.id} />}
              {section === "chat" && <ChatView paperId={paper.id} />}
              </Suspense>
              </SectionErrorBoundary>
            </motion.div>
        </div>
      </main>
    </div>
  );
}
