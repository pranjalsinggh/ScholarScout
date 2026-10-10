import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, ChevronRight, FileText, Globe2, Loader2, Lock, X, XCircle, UploadCloud } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { AuroraField, NetworkBackground } from "../components/glass/Backgrounds";
import { GlassCard, Chip } from "../components/glass/Glass";
import { GlassNavbar } from "../components/glass/GlassNavbar";
import { api, friendlyError, markSessionHasData, notifySessionResponse, ownerToken, requestSessionIsCurrent } from "../services/api";
import type { PaperSummary } from "../types/api";

const MAX_MB = 60;

interface LocalFile {
  file: File;
  id: string;
  state: "idle" | "uploading" | "processing" | "ready" | "failed" | "duplicate";
  progress: number;
  error?: string;
  serverId?: string;
}

const newId = () => Math.random().toString(36).slice(2);

export default function UploadPage() {
  const nav = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const [items, setItems] = useState<LocalFile[]>([]);
  const latestItems = useRef(items);
  latestItems.current = items;
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [connectionBusy, setConnectionBusy] = useState(false);
  const mounted = useRef(false);
  const activeUpload = useRef<XMLHttpRequest | null>(null);
  const startingBatch = useRef(false);
  const [batchBusy, setBatchBusy] = useState(false);
  const [limits, setLimits] = useState({ upload_mb: MAX_MB, pdf_pages: 300, papers: 10, concurrent_analyses: 2 });

  useEffect(() => {
    mounted.current = true;
    api.get<{ limits?: typeof limits }>("/me/privacy")
      .then((info) => { if (info.limits) setLimits(info.limits); })
      .catch(() => {});
    api.get<{ enabled: boolean }>("/me/research-lookups")
      .then((pref) => setConnected(pref.enabled))
      .catch(() => {});
    return () => { mounted.current = false; activeUpload.current?.abort(); };
  }, []);

  const toggleConnected = async () => {
    if (connectionBusy) return;
    setConnectionBusy(true);
    try {
      const pref = await api.post<{ enabled: boolean }>("/me/research-lookups", { enabled: !connected });
      setConnected(pref.enabled);
      setError(null);
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setConnectionBusy(false);
    }
  };

  const patch = (id: string, upd: Partial<LocalFile>) =>
    setItems((prev) => prev.map((it) => (it.id === id ? { ...it, ...upd } : it)));

  const addFiles = useCallback((files: FileList | File[]) => {
    setError(null);
    const next: LocalFile[] = [];
    for (const f of Array.from(files)) {
      if (!f.name.toLowerCase().endsWith(".pdf")) {
        setError(`“${f.name}” is not a PDF. ScholarScout accepts PDF files only.`);
        continue;
      }
      if (f.size > limits.upload_mb * 1024 * 1024) {
        setError(`“${f.name}” is larger than ${limits.upload_mb} MB.`);
        continue;
      }
      next.push({ file: f, id: newId(), state: "idle", progress: 0 });
    }
    if (next.length) setItems((prev) => [...prev, ...next]);
  }, [limits.upload_mb]);

  const uploadOne = (item: LocalFile) =>
    new Promise<void>((resolve) => {
      const form = new FormData();
      form.append("files", item.file);
      const xhr = new XMLHttpRequest();
      const requestToken = ownerToken();
      activeUpload.current = xhr;
      xhr.open("POST", "/api/papers/upload");
      xhr.setRequestHeader("X-Owner-Token", requestToken);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          patch(item.id, { progress: Math.round((e.loaded / e.total) * 100), state: "uploading" });
        }
      };
      xhr.onload = async () => {
        notifySessionResponse(xhr.status, requestToken);
        if (!mounted.current || !requestSessionIsCurrent(requestToken)) { resolve(); return; }
        try {
          if (xhr.status === 410) throw new Error("Session ended. Start a new session to upload research again.");
          const data = JSON.parse(xhr.responseText);
          if (!data || !Array.isArray(data.created)) throw new Error(data?.detail || "Upload failed.");
          const mine = data.created.find((c: PaperSummary) => c.filename === item.file.name.replace(/[^\w.\- ]/g, "_"))
            || data.created[0];
          if (mine) {
            markSessionHasData(true);
            patch(item.id, { serverId: mine.id, state: "processing", progress: 100 });
          } else {
            const dup = (data.duplicates || []).find((d: any) => d.filename === item.file.name.replace(/[^\w.\- ]/g, "_")) || (data.duplicates || [])[0];
            patch(item.id, {
              state: "duplicate",
              serverId: dup?.existing_id,
              error: dup
                ? `Already in your library as “${(dup.existing_title || "a previous upload").slice(0, 60)}”.`
                : "Already in your library.",
            });
          }
        } catch (e) {
          patch(item.id, { state: "failed", error: friendlyError(e) });
        }
        resolve();
      };
      xhr.onerror = () => {
        if (mounted.current) patch(item.id, { state: "failed", error: "Network error - is the backend running?" });
        resolve();
      };
      xhr.onabort = () => resolve();
      xhr.send(form);
    });

  const startAll = async () => {
    if (startingBatch.current) return;
    startingBatch.current = true;
    setBatchBusy(true);
    try {
      for (const item of items.filter((i) => i.state === "idle")) {
        const stillSelected = () => latestItems.current.some((selected) => selected.id === item.id);
        if (!stillSelected()) continue;
        const deadline = performance.now() + 60000;
        for (;;) {
          if (!mounted.current) return;
          if (!stillSelected()) break;
          const papers = await api.get<PaperSummary[]>("/papers");
          if (!mounted.current) return;
          const pending = papers.filter((paper) => paper.status === "queued" || paper.status === "processing").length;
          if (pending < limits.concurrent_analyses) break;
          if (performance.now() >= deadline) {
            patch(item.id, { state: "failed", error: "Your analysis queue is still busy. Retry this file when a dossier finishes." });
            return;
          }
          await new Promise<void>((resolve) => setTimeout(resolve, 1000));
        }
        if (stillSelected()) await uploadOne(item);
      }
    } catch (reason) {
      if (mounted.current) setError(friendlyError(reason));
    } finally {
      startingBatch.current = false;
      if (mounted.current) setBatchBusy(false);
    }
  };

  const anyProcessing = items.some((i) => i.state === "processing" || i.state === "ready" || i.state === "duplicate");
  const anyBusy = items.some((i) => i.state === "idle" || i.state === "uploading");

  const openFirst = async () => {
    const first = items.find((i) => i.serverId);
    if (!first?.serverId) return;
    const paper = await api.get<PaperSummary>(`/papers/${first.serverId}`);
    if (paper.status === "ready") nav(`/paper/${paper.id}`);
    else nav("/library");
  };

  return (
    <div className="relative min-h-screen pt-24">
      <AuroraField />
      <NetworkBackground density={30} />
      <GlassNavbar active="upload" />

      <main className="mx-auto max-w-3xl px-6 pb-24">
        <motion.div initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5 }}>
          <h1 className="text-center text-3xl font-extrabold text-white sm:text-4xl">
            Feed the scout a <span className="gradient-text">paper</span>
          </h1>
          <p className="mt-3 text-center text-dim">Start with private paper analysis. Add connected research only if you choose.</p>
        </motion.div>

        <GlassCard
          strong
          className={`mt-10 p-6 text-center transition-all duration-300 sm:p-10 ${dragOver ? "scale-[1.02] !border-iris-400/60 shadow-[0_0_60px_-12px_rgba(16,185,129,0.5)]" : ""}`}
          onDragOver={(e: React.DragEvent) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e: React.DragEvent) => { e.preventDefault(); setDragOver(false); addFiles(e.dataTransfer.files); }}
        >
          <motion.div
            animate={dragOver ? { y: -4 } : { y: 0 }}
            className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-3xl bg-gradient-to-br from-iris-500/30 to-azure-500/30 text-iris-300 shadow-[inset_0_1px_0_rgba(255,255,255,0.25)]"
          >
            <UploadCloud size={30} />
          </motion.div>
          <p className="font-semibold text-white">Drag & drop your paper here</p>
          <p className="mt-1 text-sm text-faint">PDF only · up to {limits.upload_mb} MB / {limits.pdf_pages} pages · text-based (not scanned)</p>
          <p className="mt-2 text-xs text-faint">Up to {limits.papers} papers per session and {limits.concurrent_analyses} analyses queued or running. Selected files wait for capacity before uploading.</p>
          <button onClick={() => inputRef.current?.click()} className="btn-primary mt-5 px-6 py-2.5 text-sm">
            Browse files
          </button>
          <input
            ref={inputRef}
            type="file"
            accept=".pdf,application/pdf"
            multiple
            className="hidden"
            onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ""; }}
          />
          <div className="glass-soft mx-auto mt-5 flex max-w-xl flex-col gap-3 p-4 text-left sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <p className="flex items-center gap-2 text-sm font-semibold text-white">
                {connected ? <Globe2 size={16} className="text-azure-300" /> : <Lock size={16} className="text-iris-300" />}
                {connected ? "Connected research" : "Private analysis"}
              </p>
              <p className="mt-1 text-xs leading-relaxed text-dim">
                {connected
                  ? "Title and keywords may be sent to OpenAlex, Crossref, arXiv and news indexes to find related work."
                  : "Analyze the PDF here without research-database lookups. Enable connected research before uploading to explore citations and follow-ups."}
              </p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={connected}
              aria-label="Enable connected research lookups for this session"
              onClick={toggleConnected}
              disabled={connectionBusy}
              className={`inline-flex min-h-10 shrink-0 items-center justify-center gap-2 rounded-full px-4 text-xs font-semibold ${connected ? "border border-azure-400/35 bg-azure-500/20 text-azure-100" : "btn-ghost"}`}
            >
              {connectionBusy && <Loader2 size={13} className="animate-spin" />}
              {connected ? "Lookups on" : "Enable lookups"}
            </button>
          </div>
          {error && (
            <div className="mt-4 rounded-xl border border-rose-400/30 bg-rose-500/10 px-4 py-2.5 text-sm text-rose-200">
              {error}
            </div>
          )}
          <IngestByIdentifier onQueued={(id) => nav(`/paper/${id}`)} />
        </GlassCard>

        <AnimatePresence>
          {items.map((item) => (
            <motion.div key={item.id} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, x: -20 }} className="mt-4">
              <GlassCard className="glass-ring p-4">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/5 text-iris-300">
                    <FileText size={18} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-white">{item.file.name}</div>
                    <div className="text-xs text-faint">{(item.file.size / 1024 / 1024).toFixed(1)} MB</div>
                  </div>
                  {item.state === "idle" && <Chip>ready to start</Chip>}
                  {item.state === "uploading" && <Chip tone="azure">uploading {item.progress}%</Chip>}
                  {item.state === "processing" && (
                    <Chip tone="accent"><Loader2 size={12} className="animate-spin" /> building dossier…</Chip>
                  )}
                  {item.state === "ready" && <Chip tone="ok"><CheckCircle2 size={12} /> ready</Chip>}
                  {item.state === "failed" && <Chip tone="danger"><XCircle size={12} /> failed</Chip>}
                  {item.state === "duplicate" && <Chip tone="warn">duplicate</Chip>}
                  <button onClick={() => removeItem(items, setItems, item.id)} className="rounded-lg p-1.5 text-slate-500 hover:bg-white/10 hover:text-white">
                    <X size={15} />
                  </button>
                </div>
                {item.state !== "idle" && item.state !== "failed" && (
                  <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/10">
                    <motion.div
                      className="h-full rounded-full bg-gradient-to-r from-iris-500 to-azure-500"
                      animate={{ width: `${item.progress}%` }}
                      transition={{ duration: 0.3 }}
                    />
                  </div>
                )}
                {item.error && <div className="mt-2 text-xs text-rose-300">{item.error}</div>}
                {item.state === "failed" && <button type="button" onClick={() => patch(item.id, { state: "idle", error: undefined, progress: 0 })} className="btn-ghost mt-2 px-3 py-1.5 text-xs">Retry upload</button>}
                {item.state === "processing" && item.serverId && (
                  <ProcessingTracker
                    paperId={item.serverId}
                    onReady={() => patch(item.id, { state: "ready" })}
                    onFailed={(message) => patch(item.id, { state: "failed", error: message })}
                  />
                )}
              </GlassCard>
            </motion.div>
          ))}
        </AnimatePresence>

        <div className="mt-8 flex items-center justify-center gap-3">
          {items.some((i) => i.state === "idle") && (
            <button onClick={startAll} disabled={batchBusy} className="btn-primary px-7 py-3 disabled:opacity-50">
              {batchBusy ? "Queueing selected files…" : "Start analysis"} <ChevronRight size={16} />
            </button>
          )}
          {anyProcessing && !anyBusy && (
            <button onClick={openFirst} className="btn-primary px-7 py-3">
              Open research dossier <ChevronRight size={16} />
            </button>
          )}
        </div>
      </main>
    </div>
  );
}

function removeItem(items: LocalFile[], setItems: React.Dispatch<React.SetStateAction<LocalFile[]>>, id: string) {
  setItems(items.filter((i) => i.id !== id));
}

function IngestByIdentifier({ onQueued }: { onQueued: (id: string) => void }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const ingest = async () => {
    const ident = value.trim();
    if (!ident || busy) return;
    setBusy(true);
    setMsg("");
    try {
      const p = await api.post<{ id: string; status: string }>("/papers/ingest"
        + "?identifier=" + encodeURIComponent(ident));
      onQueued(p.id);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Ingestion failed.");
      setBusy(false);
    }
  };

  return (
    <div className="mt-6 border-t border-white/10 pt-6">
      <p className="text-center text-sm text-dim">
        …or paste an <span className="text-slate-300">arXiv ID / link</span> or a{" "}
        <span className="text-slate-300">DOI</span> - we fetch the PDF for you
      </p>
      <p className="mt-1 text-center text-xs text-faint">Fetching contacts the public source you enter, even in private analysis mode.</p>
      <div className="mx-auto mt-3 flex max-w-xl items-center gap-2">
        <input
          className="glass-input !py-2 text-sm"
          placeholder="e.g. 1706.03762, arxiv.org/abs/1706.03762 or a DOI"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") ingest(); }}
        />
        <button onClick={ingest} disabled={busy || value.trim().length < 5} className="btn-primary shrink-0 px-5 py-2 text-sm disabled:opacity-40">
          {busy ? "Fetching…" : "Fetch"}
        </button>
      </div>
      {msg && <div className="mt-2 text-center text-xs text-rose-300">{msg}</div>}
    </div>
  );
}

function ProcessingTracker({ paperId, onReady, onFailed }: { paperId: string; onReady: () => void; onFailed: (message: string) => void }) {
  const [paper, setPaper] = useState<PaperSummary | null>(null);
  const notified = useRef(false);
  const cb = useRef(onReady);
  cb.current = onReady;
  const failedCb = useRef(onFailed);
  failedCb.current = onFailed;

  useEffect(() => {
    let alive = true;
    const tick = () => {
      api.get<PaperSummary>(`/papers/${paperId}`)
        .then((p) => {
          if (!alive) return;
          setPaper(p);
          if (p.status === "failed" && !notified.current) {
            notified.current = true;
            failedCb.current(p.error || "Analysis could not finish. Try a smaller text-based PDF.");
          }
          if (p.status === "ready" && !notified.current) {
            notified.current = true;
            cb.current();
          }
        })
        .catch(() => {});
    };
    tick();
    const id = setInterval(tick, 2000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [paperId]);

  return (
    <div className="mt-3 space-y-1.5 pl-1">
      {(paper?.stages || []).map((s) => (
        <div key={s.key} className="flex items-center gap-2 text-xs">
          {s.state === "done" && <CheckCircle2 size={13} className="text-emerald-400" />}
          {s.state === "active" && <Loader2 size={13} className="animate-spin text-iris-400" />}
          {s.state === "failed" && <XCircle size={13} className="text-rose-400" />}
          {(s.state === "pending" || s.state === "skipped") && (
            <div className="h-3 w-3 rounded-full border border-slate-600" />
          )}
          <span className={s.state === "done" ? "text-slate-300" : s.state === "active" ? "text-iris-300" : "text-slate-600"}>
            {s.label}{s.detail ? ` · ${s.detail}` : ""}
          </span>
        </div>
      ))}
      {paper?.error && (
        <div className="mt-1 rounded-lg bg-rose-500/10 px-3 py-2 text-xs text-rose-200">{paper.error}</div>
      )}
    </div>
  );
}
