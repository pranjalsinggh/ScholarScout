import { useState } from "react";
import { Check, Globe2, Loader2, Lock, RotateCw } from "lucide-react";
import { api, friendlyError } from "../../services/api";
import { useSectionResource } from "./SectionState";

interface ResearchPreference { enabled: boolean; refreshing?: boolean; papers_queued?: number }

export function ConnectedResearch({ paperId, refreshing, onChange }: { paperId: string; refreshing: boolean; onChange: (refreshExpected: boolean) => void }) {
  const pref = useSectionResource<ResearchPreference>("/me/research-lookups");
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const connected = enabled ?? pref.data?.enabled ?? false;
  const toggle = async () => {
    if (busy || pref.loading || pref.error) return;
    setBusy(true);
    setError("");
    try {
      const result = await api.post<ResearchPreference>("/me/research-lookups", { enabled: !connected });
      setEnabled(result.enabled);
      onChange(!!result.refreshing);
    } catch (reason) { setError(friendlyError(reason)); }
    finally { setBusy(false); }
  };
  const refresh = async () => {
    if (busy || refreshing || !connected) return;
    setBusy(true);
    setError("");
    try { await api.post(`/papers/${paperId}/refresh-context`); onChange(true); }
    catch (reason) { setError(friendlyError(reason)); }
    finally { setBusy(false); }
  };
  return (
    <div className="glass-soft mb-6 flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-sm font-semibold text-white">{connected ? <Globe2 size={16} className="text-azure-300" /> : <Lock size={16} className="text-iris-300" />}{pref.loading ? "Checking connected research preference…" : refreshing ? "Refreshing connected research…" : connected ? "Connected research enabled" : "Connected research is off"}</div>
        <p className="mt-1 text-xs leading-relaxed text-dim">{refreshing ? "Publication links and external context are being refreshed. Results will update when the lookup finishes."
          : connected ? "Coverage varies by topic and database. Source failures and missing matches are explained in each section."
          : "Enable this to explore external publications and news. Document metadata and search terms may be sent to public research services."}</p>
        {(error || pref.error) && <p className="mt-1 text-xs text-rose-300" role="alert">{error || pref.error}</p>}
      </div>
      <div className="flex shrink-0 flex-wrap gap-2">
      {connected && !pref.error && <button type="button" className="btn-ghost inline-flex min-h-10 items-center gap-1.5 px-3 text-xs disabled:opacity-50" disabled={busy || refreshing} onClick={() => void refresh()}><RotateCw size={13} /> Refresh sources</button>}
      {pref.error ? <button className="btn-ghost shrink-0 px-4 py-2 text-xs" onClick={pref.retry}>Retry preference</button>
        : <button type="button" role="switch" aria-checked={connected} aria-label="Enable connected research for this session" disabled={busy || pref.loading}
          className={`inline-flex min-h-10 shrink-0 items-center justify-center gap-2 rounded-full px-4 text-xs font-semibold ${connected ? "border border-azure-400/35 bg-azure-500/20 text-azure-100" : "btn-ghost"}`}
          onClick={() => void toggle()}>{busy || pref.loading || refreshing ? <Loader2 size={14} className="animate-spin" /> : connected ? <Check size={14} /> : <Globe2 size={14} />}{connected ? "Lookups on" : "Enable lookups"}</button>}
      </div>
    </div>
  );
}
