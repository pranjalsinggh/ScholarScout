import { useEffect, useState } from "react";
import { Network } from "lucide-react";
import { SectionTitle } from "../glass/Glass";
import { GraphView } from "../network/GraphView";
import type { GraphEdge, GraphNode } from "../../types/api";
import { SectionState, useSectionResource } from "./SectionState";

export default function NetworkView({ paperId }: { paperId: string }) {
  const { data, error, retry } = useSectionResource<{ nodes: GraphNode[]; edges: GraphEdge[]; note?: string }>(`/graph?paper_ids=${paperId}`);
  const nodes = data?.nodes;
  const edges = data?.edges || [];
  const [isMobile, setIsMobile] = useState(() => window.matchMedia("(max-width: 767px)").matches);
  const [textView, setTextView] = useState(false);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 767px)");
    const update = () => setIsMobile(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  return (
    <div>
      <SectionTitle
        title="Research graph"
        icon={<Network size={18} />}
        sub="Papers, people, institutions, methods, datasets and events - with typed relationships."
      />
      {error ? <SectionState error title="Research graph could not load" onRetry={retry}>{error}</SectionState> : !nodes ? (
        <SectionState loading title="Building the document's research graph…" />
      ) : nodes.length === 0 ? (
        <SectionState title="No verified relationships to graph">The document's extracted entities and confirmed research links appear here when available. Use Evidence or Ask ScholarScout to inspect the document text.</SectionState>
      ) : (
        <>
          <button type="button" className="btn-ghost mb-3 px-4 py-2 text-xs" aria-pressed={textView} onClick={() => setTextView(!textView)}>
            {textView ? "Show graph" : "Show readable list"}
          </button>
          {textView ? (
            <div className="glass max-h-[540px] overflow-y-auto p-4" aria-label="Research relationships">
              <ul className="space-y-3">
                {nodes.map((node) => (
                  <li key={node.id} className="glass-soft p-3 text-sm text-dim">
                    <p className="font-semibold text-white">{node.label}</p>
                    <p className="mt-1 text-xs">{node.kind}{node.year ? ` · ${node.year}` : ""}</p>
                    <ul className="mt-2 space-y-1 text-xs">
                      {edges.filter((edge) => edge.source === node.id).map((edge, index) => (
                        <li key={`${edge.target}-${index}`}>{edge.kind.replace(/_/g, " ")} → {nodes.find((other) => other.id === edge.target)?.label || "Related item"}</li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            </div>
          ) : <GraphView nodes={nodes} edges={edges} height={isMobile ? 420 : 540} touch={isMobile} />}
        </>
      )}
      {data?.note && <p className="mt-3 text-[11px] text-faint">{data.note}</p>}
    </div>
  );
}
