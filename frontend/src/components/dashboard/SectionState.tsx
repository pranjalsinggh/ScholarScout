import { useCallback, useEffect, useState } from "react";
import { AlertCircle, Info, Loader2, RotateCw } from "lucide-react";
import { GlassCard } from "../glass/Glass";
import { api, friendlyError } from "../../services/api";

/** A request belongs to one document and one mounted section. Late responses
 * cannot replace the next document's results or leave failed loads spinning. */
export function useSectionResource<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const retry = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    api.get<T>(path).then((value) => { if (alive) setData(value); })
      .catch((reason) => { if (alive) setError(friendlyError(reason)); });
    return () => { alive = false; };
  }, [path, revision]);
  return { data, error, retry, loading: data === null && error === null };
}

export function SectionState({ title, children, error = false, loading = false, onRetry }: {
  title: string;
  children?: React.ReactNode;
  error?: boolean;
  loading?: boolean;
  onRetry?: () => void;
}) {
  const Icon = loading ? Loader2 : error ? AlertCircle : Info;
  return (
    <GlassCard className={`p-5 ${error ? "border-rose-400/25" : ""}`} role={error ? "alert" : "status"}>
      <div className="flex items-start gap-3">
        <Icon size={18} className={`mt-0.5 shrink-0 ${loading ? "animate-spin text-iris-300" : error ? "text-rose-300" : "text-azure-300"}`} />
        <div className="min-w-0">
          <div className="text-sm font-medium text-slate-200">{title}</div>
          {children && <div className="mt-1 text-sm leading-relaxed text-dim">{children}</div>}
          {onRetry && <button type="button" className="btn-ghost mt-3 inline-flex items-center gap-1.5 px-3 py-1.5 text-xs" onClick={onRetry}><RotateCw size={12} /> Try again</button>}
        </div>
      </div>
    </GlassCard>
  );
}
