import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import {
  AreaChart, Area, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  Compass, FileText, GitCompareArrows, Loader2, Lock, Play, Plus, Search,
  ShieldAlert, Sparkles, Trash2, TrendingUp, Bell, X,
} from "lucide-react";
import { AuroraField, NetworkBackground } from "../components/glass/Backgrounds";
import { GlassCard, Chip, SectionTitle, stagger } from "../components/glass/Glass";
import { GlassNavbar } from "../components/glass/GlassNavbar";
import { api, endSession, friendlyError } from "../services/api";
import type { PaperSummary, TopicCluster, TrendSeries, WatchItem } from "../types/api";

interface CompareRow {
  id: string;
  title: string;
  year?: number | null;
  venue?: string | null;
  authors: string[];
  question: string;
  methods: string[];
  datasets: string[];
  metrics: string[];
  limitations: string;
  citations?: number | null;
}

interface SessionUsage {
  provider_calls: number;
  input_tokens_estimate: number;
  output_tokens_estimate: number;
  cost_usd_estimate: number | null;
  by_provider: { provider: string; calls: number; input_tokens_estimate: number; output_tokens_estimate: number; cost_usd_estimate: number | null }[];
  note: string;
}

export default function Library() {
  const nav = useNavigate();
  const [papers, setPapers] = useState<PaperSummary[] | null>(null);
  const [topics, setTopics] = useState<TopicCluster[] | null>(null);
  const [trends, setTrends] = useState<TrendSeries[] | null>(null);
  const [watch, setWatch] = useState<WatchItem[]>([]);
  const [usage, setUsage] = useState<SessionUsage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [compareSel, setCompareSel] = useState<string[]>([]);
  const [compareData, setCompareData] = useState<CompareRow[] | null>(null);
  const [loadingCompare, setLoadingCompare] = useState(false);
  const [loadingDemo, setLoadingDemo] = useState(false);

  const toggleCompare = (id: string) => {
    setCompareSel((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : prev.length >= 4 ? prev : [...prev, id]);
  };

  const runCompare = async () => {
    if (compareSel.length < 2) return;
    setLoadingCompare(true);
    setError(null);
    try {
      const r = await api.post<{ papers: CompareRow[] }>("/compare", { paper_ids: compareSel });
      setCompareData(r.papers);
      setTimeout(() => document.getElementById("compare-result")?.scrollIntoView({ behavior: "smooth" }), 100);
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setLoadingCompare(false);
    }
  };

  const [purging, setPurging] = useState(false);
  const [confirmPurge, setConfirmPurge] = useState(false);

  const purgeAll = async () => {
    if (!confirmPurge) { setConfirmPurge(true); return; }
    setPurging(true);
    try {
      await endSession();
      window.location.assign("/");
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setPurging(false);
      setConfirmPurge(false);
    }
  };

  const loadDemo = async () => {
    setLoadingDemo(true);
    setError(null);
    try {
      const p = await api.post<PaperSummary>("/papers/demo");
      setCompareSel([]);
      setTimeout(load, 400);
      setTimeout(() => nav(`/paper/${p.id}`), 600);
    } catch (e) {
      setError(friendlyError(e));
      setLoadingDemo(false);
    }
  };

  const removeCompare = (id: number) => {
    setWatch((prev) => prev.filter((w) => w.id !== id));
    api.del(`/watchlist/${id}`).catch(() => {});
  };

  const load = () => {
    api.get<PaperSummary[]>("/papers").then(setPapers).catch((e) => setError(friendlyError(e)));
    api.get<{ topics: TopicCluster[] }>("/topics").then((r) => {
      // the first request may warm up the vector store; retry once if empty
      if (r.topics.length === 0) {
        setTimeout(() => api.get<{ topics: TopicCluster[] }>("/topics").then((r2) => setTopics(r2.topics)).catch(() => {}), 3500);
      }
      setTopics(r.topics);
    }).catch(() => setTopics([]));
    api.get<{ series: TrendSeries[] }>("/trends").then((r) => setTrends(r.series)).catch(() => setTrends([]));
    api.get<WatchItem[]>("/watchlist").then(setWatch).catch(() => {});
    api.get<SessionUsage>("/me/usage").then(setUsage).catch(() => {});
  };

  useEffect(load, []);

  // keep the library honest: papers may finish processing (or a new upload may
  // land from another path) while this page is open - refresh when the tab
  // becomes visible again and shortly after mount
  useEffect(() => {
    const onVisible = () => { if (document.visibilityState === "visible") load(); };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    const t1 = setTimeout(load, 2500);
    const t2 = setTimeout(load, 8000);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, []);

  const removePaper = async (id: string) => {
    try {
      await api.del(`/papers/${id}`);
      load();
    } catch (e) {
      setError(friendlyError(e));
    }
  };

  const checkWatch = async (item: WatchItem) => {
    try {
      const r = await api.post<WatchItem>(`/watchlist/${item.id}/check`);
      setWatch((prev) => prev.map((w) => (w.id === r.id ? r : w)));
    } catch { /* ignore */ }
  };

  const trendChart = (s: TrendSeries) => s.points.map((p) => ({ year: p.year, count: p.count }));

  return (
    <div className="relative min-h-screen pt-24">
      <AuroraField />
      <NetworkBackground density={26} />
      <GlassNavbar active="library" />

      <main className="mx-auto max-w-6xl space-y-10 px-6 pb-24">
        {error && <GlassCard className="border-rose-400/25 p-4 text-sm text-rose-200">{error}</GlassCard>}

        {/* papers */}
        <section>
          <SectionTitle
            title="Your library"
            icon={<FileText size={18} />}
            sub="Papers you've uploaded, each with its full research dossier. Select 2–4 to compare side by side."
          />
          {papers === null ? (
            <div className="grid gap-4 sm:grid-cols-2">{[...Array(2)].map((_, i) => <div key={i} className="skeleton h-28 opacity-60" />)}</div>
          ) : papers.length === 0 ? (
            <GlassCard strong className="p-10 text-center">
              <Compass className="mx-auto mb-3 text-iris-400" size={26} />
              <p className="font-medium text-white">No papers yet</p>
              <p className="mt-1 text-sm text-dim">Upload your own PDF, or explore the demo paper first.</p>
              <div className="mt-5 flex flex-wrap items-center justify-center gap-3">
                <Link to="/upload" className="btn-primary inline-flex px-6 py-2.5 text-sm">
                  <Plus size={15} /> Analyze a Paper
                </Link>
                <button onClick={loadDemo} disabled={loadingDemo} className="btn-ghost inline-flex px-6 py-2.5 text-sm">
                  {loadingDemo ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}
                  Explore Demo
                </button>
              </div>
            </GlassCard>
          ) : (
            <>
              {compareSel.length > 0 && (
                <motion.div
                  initial={{ opacity: 0, y: -8 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="glass-strong mb-4 flex flex-wrap items-center gap-3 p-3.5"
                >
                  <GitCompareArrows size={17} className="text-iris-400" />
                  <span className="text-sm text-slate-200">{compareSel.length} selected</span>
                  {compareSel.map((id) => {
                    const p = papers.find((x) => x.id === id);
                    return (
                      <Chip key={id} tone="accent">
                        {(p?.title || "").slice(0, 24)}
                        <button onClick={() => toggleCompare(id)} className="ml-1 opacity-70 hover:opacity-100"><X size={10} /></button>
                      </Chip>
                    );
                  })}
                  <div className="ml-auto flex gap-2">
                    <button onClick={runCompare} disabled={compareSel.length < 2 || loadingCompare} className="btn-primary px-4 py-1.5 text-xs disabled:opacity-40">
                      {loadingCompare ? <Loader2 size={13} className="animate-spin" /> : <GitCompareArrows size={13} />}
                      Compare
                    </button>
                    <button onClick={() => setCompareSel([])} className="btn-ghost px-3 py-1.5 text-xs">Clear</button>
                  </div>
                </motion.div>
              )}
              <div className="grid gap-4 sm:grid-cols-2">
                {papers.map((p, i) => (
                  <motion.div key={p.id} {...stagger(i * 0.05)}>
                    <GlassCard className={`glass-ring group p-5 ${compareSel.includes(p.id) ? "!border-iris-400/60 shadow-[0_0_0_1px_rgba(52,211,153,0.35)]" : ""}`}>
                      <button onClick={() => compareSel.includes(p.id) ? toggleCompare(p.id) : nav(`/paper/${p.id}`)} className="w-full text-left">
                        <h3 className="line-clamp-2 break-words pr-16 font-semibold leading-snug text-white group-hover:text-iris-200">
                          {p.title}
                        </h3>
                        <div className="mt-1 text-xs text-faint">
                          {p.authors.slice(0, 3).map((a) => a.name).join(", ")}
                          {p.year ? ` · ${p.year}` : ""}
                        </div>
                        <div className="mt-3 flex flex-wrap items-center gap-2">
                          {p.status === "ready" && <Chip tone="ok">{p.citation_count} citations indexed</Chip>}
                          {p.status === "ready" && <Chip tone="azure">{p.page_count} pages</Chip>}
                          {(p.status === "processing" || p.status === "queued") && (
                            <Chip tone="accent"><Loader2 size={11} className="animate-spin" /> processing</Chip>
                          )}
                          {p.status === "failed" && <Chip tone="danger">failed</Chip>}
                        </div>
                      </button>
                      <div className="absolute right-3 top-3 flex gap-1 opacity-75 transition-opacity hover:opacity-100 focus-within:opacity-100">
                        {p.status === "ready" && (
                          <button
                            onClick={(e) => { e.stopPropagation(); toggleCompare(p.id); }}
                            className={`rounded-lg p-2 ${compareSel.includes(p.id) ? "bg-iris-500/25 text-iris-300" : "text-slate-400 hover:bg-white/10 hover:text-iris-300"}`}
                            title="Select for comparison"
                          >
                            <GitCompareArrows size={14} />
                          </button>
                        )}
                        <button
                          onClick={(e) => { e.stopPropagation(); removePaper(p.id); setCompareSel((s) => s.filter((x) => x !== p.id)); }}
                          className="rounded-lg p-2 text-slate-400 hover:bg-white/10 hover:text-rose-300"
                          title="Remove from library"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </GlassCard>
                  </motion.div>
                ))}
              </div>
            </>
          )}
        </section>

        {/* comparison result */}
        {compareData && (
          <section id="compare-result">
            <SectionTitle title="Paper comparison" icon={<GitCompareArrows size={18} />} />
            <div className="overflow-x-auto">
              <GlassCard className="min-w-[720px] p-5">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr className="border-b border-white/10">
                      <th className="w-36 py-2 pr-3 text-xs font-semibold uppercase tracking-wider text-faint">Aspect</th>
                      {compareData.map((p) => (
                        <th key={p.id} className="px-3 py-2 align-top">
                          <div className="font-semibold text-white">{p.title.slice(0, 44)}</div>
                          <div className="mt-0.5 text-xs font-normal text-faint">
                            {p.year || "-"}{p.venue ? ` · ${p.venue.slice(0, 28)}` : ""}
                            {p.citations ? ` · ${p.citations} citations` : ""}
                          </div>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="align-top">
                    {([
                      ["Research question", (p: CompareRow) => p.question || "-"],
                      ["Methods", (p: CompareRow) => p.methods.join(", ") || "none detected"],
                      ["Datasets", (p: CompareRow) => p.datasets.join(", ") || "none detected"],
                      ["Metrics", (p: CompareRow) => p.metrics.join(", ") || "none detected"],
                      ["Limitations", (p: CompareRow) => p.limitations || "-"],
                    ] as const).map(([label, get]) => (
                      <tr key={label} className="border-b border-white/5 last:border-0">
                        <td className="py-3 pr-3 text-xs font-semibold uppercase tracking-wider text-faint">{label}</td>
                        {compareData.map((p) => (
                          <td key={p.id} className="px-3 py-3 text-[13px] leading-relaxed text-slate-300">{get(p)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </GlassCard>
            </div>
          </section>
        )}

        {/* watchlist */}
        {watch.length > 0 && (
          <section>
            <SectionTitle title="Watchlist" icon={<Bell size={18} />} sub="Saved papers - check for new citations, corrections and follow-ups." />
            <div className="space-y-3">
              {watch.map((w) => (
                <GlassCard key={w.id} className="glass-ring p-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="min-w-0">
                      <button onClick={() => nav(`/paper/${w.paper_id}`)} className="truncate text-sm font-medium text-white hover:text-iris-200">
                        {w.label}
                      </button>
                      <div className="text-xs text-faint">
                        {w.updates.length} recorded updates
                        {w.updates[0] && ` · latest: ${w.updates[0].title.slice(0, 60)}…`}
                      </div>
                    </div>
                    <div className="flex shrink-0 gap-2">
                      <button onClick={() => checkWatch(w)} className="btn-ghost px-4 py-1.5 text-xs">
                        Check now
                      </button>
                      <button
                        onClick={() => removeCompare(w.id)}
                        className="rounded-lg p-1.5 text-slate-600 hover:bg-white/10 hover:text-rose-300"
                        title="Remove from watchlist"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </div>
                  {w.updates.slice(0, 3).map((u, i) => (
                    <div key={i} className="mt-2 flex items-center gap-2 text-xs text-slate-400">
                      <Chip tone="azure">{u.kind.replace("_", " ")}</Chip>
                      <span className="truncate">{u.title}</span>
                      {u.url && <a href={u.url} target="_blank" rel="noreferrer" className="shrink-0 text-azure-300">↗</a>}
                    </div>
                  ))}
                </GlassCard>
              ))}
            </div>
          </section>
        )}

        {/* topics */}
        <section>
          <SectionTitle title="Research topics" icon={<Sparkles size={18} />} sub="Unsupervised clustering across your papers' content." />
          {topics === null || topics.length === 0 ? (
            <GlassCard className="p-5 text-sm text-faint">Topic discovery needs at least one processed paper.</GlassCard>
          ) : (
            <div className="space-y-3">
              {topics.map((t, i) => (
                <motion.div key={i} {...stagger(i * 0.05)}>
                  <GlassCard className="glass-ring p-4">
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-sm font-semibold text-white">{t.label}</span>
                      <span className="gradient-text font-bold">{t.share}%</span>
                    </div>
                    <div className="mt-2 h-2 overflow-hidden rounded-full bg-white/10">
                      <motion.div
                        className="h-full rounded-full bg-gradient-to-r from-iris-500 to-azure-500"
                        initial={{ width: 0 }}
                        animate={{ width: `${t.share}%` }}
                        transition={{ duration: 0.8, ease: "easeOut" }}
                      />
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {t.terms.map((term) => <Chip key={term}>{term}</Chip>)}
                    </div>
                  </GlassCard>
                </motion.div>
              ))}
            </div>
          )}
        </section>

        {/* trends */}
        <section>
          <SectionTitle title="Research trends" icon={<TrendingUp size={18} />} sub="Topic frequency from actual publication years. Past activity doesn't predict future importance." />
          {trends === null || trends.length === 0 ? (
            <GlassCard className="p-5 text-sm text-faint">Trends appear once citation data is available.</GlassCard>
          ) : (
            <div className="grid gap-4 md:grid-cols-2">
              {trends.map((s) => (
                <GlassCard key={s.topic} className="glass-ring p-5">
                  <div className="mb-3 flex items-center justify-between">
                    <span className="text-sm font-semibold text-white">{s.topic}</span>
                    <Chip tone={s.status === "growing" || s.status === "emerging" ? "ok" : s.status === "declining" ? "warn" : "azure"}>
                      {s.status}
                    </Chip>
                  </div>
                  <ResponsiveContainer width="100%" height={130}>
                    <AreaChart data={trendChart(s)}>
                      <defs>
                        <linearGradient id={`g-${s.topic.replace(/\W/g, "")}`} x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#10b981" stopOpacity={0.5} />
                          <stop offset="100%" stopColor="#3b82f6" stopOpacity={0.05} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid stroke="rgba(255,255,255,0.06)" vertical={false} />
                      <XAxis dataKey="year" tick={{ fill: "#64748b", fontSize: 10 }} tickLine={false} axisLine={false} />
                      <YAxis tick={{ fill: "#64748b", fontSize: 10 }} tickLine={false} axisLine={false} width={24} />
                      <Tooltip
                        contentStyle={{ background: "rgba(16,16,28,0.95)", border: "1px solid rgba(255,255,255,0.15)", borderRadius: 12, fontSize: 12 }}
                        labelStyle={{ color: "#e2e8f0" }}
                      />
                      <Area type="monotone" dataKey="count" stroke="#34d399" strokeWidth={2} fill={`url(#g-${s.topic.replace(/\W/g, "")})`} />
                    </AreaChart>
                  </ResponsiveContainer>
                </GlassCard>
              ))}
            </div>
          )}
        </section>

        {/* privacy zone */}
        <section>
          <GlassCard className="p-4 sm:p-6">
            <div className="flex items-center gap-3">
              <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-iris-500/25 to-azure-500/25 text-iris-300">
                <Lock size={20} />
              </div>
              <h3 className="flex flex-wrap items-center gap-2 font-semibold text-white">
                Your data is private <Chip tone="ok">encrypted · AES-256</Chip>
              </h3>
            </div>
                <p className="mt-3 text-sm leading-relaxed text-dim">
                  Your research stays in this tab session. Uploaded PDFs and chats are
                  encrypted on this server. Refreshing or leaving shows a browser warning,
                  then sends a deletion request if you proceed. Idle expiry is the fallback
                  if that request cannot arrive. Cloud AI and
                  public research lookups are separate choices for this session.
                  Session erasure does not remove exports you downloaded or copies
                  retained by external providers.
                </p>
                {usage && (
                  <div className="mt-4 grid gap-2 sm:grid-cols-3" aria-label="Cloud provider usage for this session">
                    <div className="glass-soft px-3 py-2.5">
                      <div className="text-[11px] text-faint">Provider calls</div>
                      <div className="mt-0.5 text-sm font-semibold text-white">{usage.provider_calls.toLocaleString()}</div>
                    </div>
                    <div className="glass-soft px-3 py-2.5">
                      <div className="text-[11px] text-faint">Estimated tokens · in / out</div>
                      <div className="mt-0.5 text-sm font-semibold text-white">{usage.input_tokens_estimate.toLocaleString()} / {usage.output_tokens_estimate.toLocaleString()}</div>
                    </div>
                    <div className="glass-soft px-3 py-2.5">
                      <div className="text-[11px] text-faint">Estimated API cost</div>
                      <div className="mt-0.5 text-sm font-semibold text-white">
                        {usage.cost_usd_estimate === null
                          ? "Rate unavailable"
                          : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 4 }).format(usage.cost_usd_estimate)}
                      </div>
                    </div>
                    {usage.cost_usd_estimate === null && (
                      <p className="text-[11px] text-faint sm:col-span-3">Cost estimate unavailable until provider rates are configured.</p>
                    )}
                    <p className="text-[11px] text-faint sm:col-span-3">{usage.note}</p>
                  </div>
                )}
                <div className="mt-3 flex flex-wrap items-center gap-3">
                  {!confirmPurge ? (
                    <button onClick={purgeAll} className="btn-ghost px-4 py-2 text-xs text-rose-300/90 hover:text-rose-200">
                      <ShieldAlert size={13} /> End session and erase data
                    </button>
                  ) : (
                    <>
                      <button onClick={purgeAll} disabled={purging} className="px-4 py-2 text-xs font-semibold text-white bg-rose-600/80 rounded-full hover:bg-rose-600">
                        {purging ? "Erasing…" : "Yes, erase this session"}
                      </button>
                      <button onClick={() => setConfirmPurge(false)} className="btn-ghost px-4 py-2 text-xs">
                        Cancel
                      </button>
                    </>
                  )}
                  <span className="text-[11px] text-faint">
                    Save any exports you need before ending this session.
                  </span>
                </div>
          </GlassCard>
        </section>

        {/* cross-library search hint */}
        <section>
          <GlassCard strong className="flex flex-col items-center gap-3 p-8 text-center sm:flex-row sm:text-left">
            <Search className="shrink-0 text-iris-400" size={22} />
            <div className="flex-1">
              <p className="font-medium text-white">Search across everything you've uploaded</p>
              <p className="text-sm text-dim">Semantic search lives inside every dossier - open a paper and head to Evidence.</p>
            </div>
            {papers && papers[0] && (
              <button onClick={() => nav(`/paper/${papers[0].id}/evidence`)} className="btn-ghost px-5 py-2 text-sm">
                Try it
              </button>
            )}
          </GlassCard>
        </section>
      </main>
    </div>
  );
}
