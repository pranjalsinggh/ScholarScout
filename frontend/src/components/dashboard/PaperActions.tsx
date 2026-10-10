import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Bookmark, BookmarkCheck, Check, Copy, Quote } from "lucide-react";
import { Chip } from "../glass/Glass";
import { api, notifySessionResponse, ownerToken } from "../../services/api";

/**
 * Top-bar paper actions: Cite (BibTeX/RIS popover with copy) and
 * Watch (save to watchlist). Small, self-contained, glass-styled.
 */
export function PaperActions({ paperId }: { paperId: string }) {
  const [open, setOpen] = useState(false);
  const [format, setFormat] = useState<"bibtex" | "ris">("bibtex");
  const [citation, setCitation] = useState("");
  const [copied, setCopied] = useState(false);
  const [watching, setWatching] = useState<boolean | null>(null);
  const popRef = useRef<HTMLDivElement>(null);

  // watchlist state
  useEffect(() => {
    api.get<{ id: number; paper_id: string }[]>("/watchlist")
      .then((list) => setWatching(list.some((w) => w.paper_id === paperId)))
      .catch(() => setWatching(false));
  }, [paperId]);

  // load citation when the popover opens
  useEffect(() => {
    if (!open || citation) return;
    const requestToken = ownerToken();
    fetch(`/api/papers/${paperId}/cite?format=${format}`, {
      headers: { "X-Owner-Token": requestToken },
    })
      .then((r) => {
        notifySessionResponse(r.status, requestToken);
        return r.ok ? r.text() : Promise.reject(new Error("load failed"));
      })
      .then(setCitation)
      .catch(() => setCitation(""));
  }, [open, format, paperId, citation]);

  // close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (popRef.current && !popRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(citation);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch { /* clipboard unavailable */ }
  };

  const toggleWatch = async () => {
    if (watching === null) return;
    if (watching) return; // already watched; removal lives in the Library
    try {
      await api.post(`/watchlist/${paperId}`);
      setWatching(true);
    } catch { /* best-effort */ }
  };

  const download = () => {
    const blob = new Blob([citation], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = format === "bibtex" ? "citation.bib" : "citation.ris";
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="relative flex items-center gap-2" ref={popRef}>
      <button
        onClick={toggleWatch}
        disabled={watching === null}
        className={`chip transition-all ${watching ? "chip-ok" : "hover:bg-white/10"}`}
        title={watching ? "In your watchlist" : "Save to watchlist - track new citations & corrections"}
      >
        {watching ? <BookmarkCheck size={12} /> : <Bookmark size={12} />}
        <span className="hidden md:inline">{watching ? "Watching" : "Watch"}</span>
      </button>

      <button onClick={() => setOpen((v) => !v)} className="chip chip-accent hover:bg-iris-500/25">
        <Quote size={12} /> <span className="hidden md:inline">Cite</span>
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: 6, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 6, scale: 0.97 }}
            transition={{ duration: 0.18 }}
            className="glass-strong absolute right-0 top-9 z-50 w-[380px] max-w-[calc(100vw-2rem)] p-4"
          >
            <div className="mb-2.5 flex items-center gap-2">
              {(["bibtex", "ris"] as const).map((f) => (
                <button
                  key={f}
                  onClick={() => { setFormat(f); setCitation(""); }}
                  className={`chip ${format === f ? "chip-accent" : "opacity-70"}`}
                >
                  {f === "bibtex" ? "BibTeX" : "RIS (Zotero)"}
                </button>
              ))}
              <div className="ml-auto flex gap-1.5">
                <button onClick={copy} className="btn-ghost px-2.5 py-1 text-xs" title="Copy to clipboard">
                  {copied ? <Check size={12} className="text-emerald-300" /> : <Copy size={12} />}
                  {copied ? "Copied" : "Copy"}
                </button>
                <button onClick={download} className="btn-ghost px-2.5 py-1 text-xs" title="Download file">
                  .{format === "bibtex" ? "bib" : "ris"}
                </button>
              </div>
            </div>
            <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-black/30 p-3 font-sans text-[11px] leading-relaxed text-slate-300">
              {citation || "Loading…"}
            </pre>
            <div className="mt-2">
              <Chip>works with Zotero · Mendeley · Overleaf · LaTeX</Chip>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
