import { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import type { GraphEdge, GraphNode } from "../../types/api";

const KIND_COLORS: Record<string, string> = {
  paper: "#34d399",
  author: "#22d3ee",
  institution: "#2dd4bf",
  method: "#f472b6",
  dataset: "#fbbf24",
  topic: "#67e8f9",
  event: "#fb923c",
};

const KIND_SIZES: Record<string, number> = {
  paper: 8,
  author: 5,
  institution: 6,
  method: 5,
  dataset: 5,
  topic: 5,
  event: 5,
};

/** "#rrggbb" + alpha → rgba() string. */
function hexA(hex: string, a: number): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

interface Sim {
  x: number; y: number; vx: number; vy: number;
}

/**
 * Lightweight force-directed graph on canvas: pan, zoom, hover, click,
 * type filtering. Falls back to readable on small screens via `compact`.
 */
export function GraphView({
  nodes,
  edges,
  onNodeClick,
  height = 520,
  touch = false,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  onNodeClick?: (n: GraphNode) => void;
  height?: number;
  touch?: boolean;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [hover, setHover] = useState<GraphNode | null>(null);
  const selectedRef = useRef<GraphNode | null>(null);
  const requestDraw = useRef<() => void>(() => {});

  const state = useRef({
    tx: 0, ty: 0, scale: 1,
    dragging: false, lx: 0, ly: 0,
    hoverId: null as string | null,
    sims: new Map<string, Sim>(),
    raf: 0,
    pointers: new Map<number, { x: number; y: number }>(),
    pinchDist: 0,
    moved: false,
    travel: 0,
    width: 0,
  });

  const activeNodes = useMemo(() => nodes.filter((n) => !hidden.has(n.kind)), [nodes, hidden]);
  const activeIds = useMemo(() => new Set(activeNodes.map((n) => n.id)), [activeNodes]);
  const activeEdges = useMemo(
    () => edges.filter((e) => activeIds.has(e.source) && activeIds.has(e.target)),
    [edges, activeIds],
  );
  const kinds = useMemo(() => Array.from(new Set(nodes.map((n) => n.kind))), [nodes]);

  // init positions once per node set
  useEffect(() => {
    const s = state.current;
    for (const n of activeNodes) {
      if (!s.sims.has(n.id)) {
        const a = Math.random() * Math.PI * 2;
        const r = 120 + Math.random() * 160;
        s.sims.set(n.id, { x: Math.cos(a) * r, y: Math.sin(a) * r, vx: 0, vy: 0 });
      }
    }
  }, [activeNodes]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let visible = true;
    let disposed = false;
    let settled = false;
    let stableFrames = 0;
    let physicsFrames = 0;
    let nextPhysics = 0;
    const frameInterval = 1000 / 60;
    const motionPreference = window.matchMedia("(prefers-reduced-motion: reduce)");

    const schedule = () => {
      if (!disposed && visible && !document.hidden && !state.current.raf) {
        state.current.raf = requestAnimationFrame(step);
      }
    };
    requestDraw.current = schedule;

    const resize = () => {
      // DPR 1 - the graph is a stylized visualization; the perf win is large
      state.current.width = wrap.clientWidth;
      canvas.width = state.current.width;
      canvas.height = height;
      canvas.style.height = `${height}px`;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      schedule();
    };

    const step = (timestamp: number) => {
      const s = state.current;
      s.raf = 0;
      if (disposed || !visible || document.hidden) return;
      const W = s.width;
      const H = height;
      const selected = selectedRef.current;
      if (!s.dragging && !settled && !motionPreference.matches && timestamp + 0.5 < nextPhysics) {
        schedule();
        return;
      }

      // Settle once, then draw only for interaction. High refresh displays must
      // not multiply the O(n²) simulation work or change the layout's speed.
      if (!s.dragging && !settled && !motionPreference.matches) {
        // Preserve the frame phase with a small timestamp tolerance. Reset on
        // long pauses instead of replaying missed physics frames on resume.
        nextPhysics = nextPhysics === 0 || timestamp - nextPhysics > frameInterval
          ? timestamp + frameInterval
          : nextPhysics + frameInterval;
        physicsFrames += 1;
        const repel = 2600;
        const nodesArr = activeNodes;
        for (let i = 0; i < nodesArr.length; i++) {
          const a = s.sims.get(nodesArr[i].id);
          if (!a) continue;
          for (let j = i + 1; j < nodesArr.length; j++) {
            const b = s.sims.get(nodesArr[j].id);
            if (!b) continue;
            const dx = a.x - b.x, dy = a.y - b.y;
            const d2 = dx * dx + dy * dy + 0.01;
            const f = repel / d2;
            const d = Math.sqrt(d2);
            a.vx += (dx / d) * f * 0.5;
            a.vy += (dy / d) * f * 0.5;
            b.vx -= (dx / d) * f * 0.5;
            b.vy -= (dy / d) * f * 0.5;
          }
          // centering spring
          a.vx += -a.x * 0.0012;
          a.vy += -a.y * 0.0012;
        }
        for (const e of activeEdges) {
          const a = s.sims.get(e.source);
          const b = s.sims.get(e.target);
          if (!a || !b) continue;
          const dx = b.x - a.x, dy = b.y - a.y;
          const d = Math.sqrt(dx * dx + dy * dy) + 0.01;
          const target = 90;
          const f = (d - target) * 0.012;
          a.vx += (dx / d) * f;
          a.vy += (dy / d) * f;
          b.vx -= (dx / d) * f;
          b.vy -= (dy / d) * f;
        }
        let movement = 0;
        for (const n of nodesArr) {
          const p = s.sims.get(n.id);
          if (!p) continue;
          p.vx *= 0.82; p.vy *= 0.82;
          p.x += p.vx; p.y += p.vy;
          movement += Math.abs(p.vx) + Math.abs(p.vy);
        }
        stableFrames = movement / Math.max(1, nodesArr.length) < 0.04 ? stableFrames + 1 : 0;
        settled = stableFrames >= 12 || physicsFrames >= 300;
      }

      ctx.clearRect(0, 0, W, H);
      ctx.save();
      ctx.translate(W / 2 + s.tx, H / 2 + s.ty);
      ctx.scale(s.scale, s.scale);

      // edges
      for (const e of activeEdges) {
        const a = s.sims.get(e.source);
        const b = s.sims.get(e.target);
        if (!a || !b) continue;
        const highlight =
          (s.hoverId === e.source || s.hoverId === e.target) ||
          (selected && (e.source === selected.id || e.target === selected.id));
        ctx.strokeStyle = highlight ? "rgba(52,211,153,0.65)" : "rgba(255,255,255,0.09)";
        ctx.lineWidth = highlight ? 1.6 : 0.8;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }

      // nodes - plain fills + ring highlight (no shadowBlur: keeps 60fps at 80 nodes)
      for (const n of activeNodes) {
        const p = s.sims.get(n.id);
        if (!p) continue;
        const r = (KIND_SIZES[n.kind] || 5) * (n.meta?.main ? 1.8 : 1) * (s.hoverId === n.id ? 1.35 : 1)
              * (touch ? 1.25 : 1);
        const color = KIND_COLORS[n.kind] || "#94a3b8";
        const isHover = s.hoverId === n.id;
        const isSel = selected && n.id === selected.id;
        if (isHover || isSel) {
          ctx.beginPath();
          ctx.arc(p.x, p.y, r + 5, 0, Math.PI * 2);
          ctx.fillStyle = hexA(color, 0.16);
          ctx.fill();
        }
        ctx.beginPath();
        ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.globalAlpha = s.hoverId && !isHover ? 0.5 : 1;
        ctx.fill();
        ctx.globalAlpha = 1;
        if (n.meta?.main) {
          ctx.beginPath();
          ctx.arc(p.x, p.y, r + 2.5, 0, Math.PI * 2);
          ctx.strokeStyle = hexA(color, 0.5);
          ctx.lineWidth = 1.5;
          ctx.stroke();
        }
        // label for big nodes or hover
        if ((n.kind === "paper" && (n.meta?.main || isHover)) || isHover) {
          ctx.font = "11px 'Red Rose', sans-serif";
          ctx.fillStyle = "rgba(255,255,255,0.85)";
          ctx.textAlign = "center";
          const label = n.label.length > 34 ? n.label.slice(0, 32) + "…" : n.label;
          ctx.fillText(label, p.x, p.y - r - 6);
        }
      }
      ctx.restore();
      if (!settled && !s.dragging && !motionPreference.matches) schedule();
    };

    const onVisibility = () => {
      if (document.hidden) {
        cancelAnimationFrame(state.current.raf);
        state.current.raf = 0;
      } else schedule();
    };
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const s = state.current;
      s.scale = Math.min(3, Math.max(0.35, s.scale * (event.deltaY < 0 ? 1.1 : 0.9)));
      schedule();
    };
    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(wrap);
    const intersectionObserver = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      if (visible) schedule();
      else {
        cancelAnimationFrame(state.current.raf);
        state.current.raf = 0;
      }
    });
    intersectionObserver.observe(wrap);
    document.addEventListener("visibilitychange", onVisibility);
    motionPreference.addEventListener("change", schedule);
    // React's delegated wheel listener is passive; register here so zoom does
    // not simultaneously scroll the page and trigger repeated layout work.
    wrap.addEventListener("wheel", onWheel, { passive: false });
    resize();
    // A settled canvas must redraw when the Google Font finishes loading.
    void document.fonts?.ready.then(schedule);
    return () => {
      disposed = true;
      cancelAnimationFrame(state.current.raf);
      state.current.raf = 0;
      requestDraw.current = () => {};
      resizeObserver.disconnect();
      intersectionObserver.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      motionPreference.removeEventListener("change", schedule);
      wrap.removeEventListener("wheel", onWheel);
    };
  }, [activeNodes, activeEdges, height, touch]);

  // pointer interactions
  const pick = (mx: number, my: number): GraphNode | null => {
    const s = state.current;
    const W = s.width;
    const x = (mx - W / 2 - s.tx) / s.scale;
    const y = (my - height / 2 - s.ty) / s.scale;
    for (const n of activeNodes) {
      const p = s.sims.get(n.id);
      if (!p) continue;
      const r = (KIND_SIZES[n.kind] || 5) * (n.meta?.main ? 1.8 : 1) * (touch ? 1.25 : 1) + 6 / s.scale;
      if ((p.x - x) ** 2 + (p.y - y) ** 2 < r * r) return n;
    }
    return null;
  };

  const releasePointer = (pointerId: number, cancelled = false) => {
    const s = state.current;
    s.pointers.delete(pointerId);
    s.dragging = s.pointers.size > 0;
    if (cancelled) s.moved = true;
    if (s.pointers.size === 1) {
      // The remaining finger can continue panning after a pinch without using
      // the released finger's stale coordinate as its starting point.
      const [remaining] = s.pointers.values();
      s.lx = remaining.x;
      s.ly = remaining.y;
      s.pinchDist = 0;
    }
    requestDraw.current();
  };

  return (
    <div className="graph-wrap glass overflow-hidden">
      {/* type filter */}
      <div className="flex flex-wrap items-center gap-2 border-b border-white/10 px-4 py-3">
        {kinds.map((k) => (
          <button
            key={k}
            onClick={() => setHidden((prev) => {
              const next = new Set(prev);
              if (next.has(k)) next.delete(k); else next.add(k);
              return next;
            })}
            className={`chip capitalize transition-opacity ${hidden.has(k) ? "opacity-35" : ""}`}
            style={{ borderColor: `${KIND_COLORS[k]}55`, color: KIND_COLORS[k] }}
          >
            <span className="h-2 w-2 rounded-full" style={{ background: KIND_COLORS[k] }} />
            {k}
          </button>
        ))}
        <span className="ml-auto text-xs text-faint">{activeNodes.length} nodes · scroll to zoom · drag to pan</span>
      </div>

      <div
        ref={wrapRef}
        className="relative cursor-grab active:cursor-grabbing"
        onPointerDown={(e) => {
          const s = state.current;
          if (s.pointers.size === 0 && touch) {
            // on touch, hover highlight follows the pressed node
            const rect = wrapRef.current!.getBoundingClientRect();
            const n = pick(e.clientX - rect.left, e.clientY - rect.top);
            s.hoverId = n?.id || null;
          }
          (e.target as Element).setPointerCapture?.(e.pointerId);
          if (s.pointers.size === 0) {
            s.moved = false;
            s.travel = 0;
          }
          s.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
          s.dragging = true;
          s.lx = e.clientX;
          s.ly = e.clientY;
          if (s.pointers.size === 2) {
            s.moved = true;
            const [a, b] = [...s.pointers.values()];
            s.pinchDist = Math.hypot(a.x - b.x, a.y - b.y);
          }
          requestDraw.current();
        }}
        onPointerMove={(e) => {
          const s = state.current;
          if (!s.pointers.has(e.pointerId)) {
            const rect = wrapRef.current!.getBoundingClientRect();
            const n = pick(e.clientX - rect.left, e.clientY - rect.top);
            if (s.hoverId !== (n?.id || null)) {
              s.hoverId = n?.id || null;
              setHover(n);
              requestDraw.current();
            }
            return;
          }
          s.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
          const s2 = state.current;
          if (s2.pointers.size === 2) {
            const [a, b] = [...s2.pointers.values()];
            const dist = Math.hypot(a.x - b.x, a.y - b.y);
            if (s2.pinchDist > 0) {
              s2.scale = Math.min(3, Math.max(0.35, s2.scale * (dist / s2.pinchDist)));
            }
            s2.pinchDist = dist;
            s2.moved = true;
            requestDraw.current();
            return;
          }
          if (s.dragging) {
            const dx = e.clientX - s.lx;
            const dy = e.clientY - s.ly;
            s.travel += Math.hypot(dx, dy);
            if (s.travel > 4) s.moved = true;
            s.tx += dx;
            s.ty += dy;
            s.lx = e.clientX;
            s.ly = e.clientY;
            requestDraw.current();
          } else {
            const rect = wrapRef.current!.getBoundingClientRect();
            const n = pick(e.clientX - rect.left, e.clientY - rect.top);
            if (s.hoverId !== (n?.id || null)) {
              s.hoverId = n?.id || null;
              setHover(n);
              requestDraw.current();
            }
          }
        }}
        onPointerUp={(e) => releasePointer(e.pointerId)}
        onPointerCancel={(e) => releasePointer(e.pointerId, true)}
        onPointerLeave={() => {
          const s = state.current;
          s.dragging = false;
          s.hoverId = null;
          setHover(null);
          requestDraw.current();
        }}
        onClick={(e) => {
          if (state.current.moved) return;  // it was a pan/pinch, not a tap
          const rect = wrapRef.current!.getBoundingClientRect();
          const n = pick(e.clientX - rect.left, e.clientY - rect.top);
          selectedRef.current = n;
          requestDraw.current();
          if (n) onNodeClick?.(n);
        }}
      >
        <canvas ref={canvasRef} aria-label="Interactive research graph; use the readable list for text relationships" className="block w-full touch-none" style={{ height }} />
        <div className="absolute bottom-3 right-3 flex flex-col gap-1.5">
          {[
            { label: "+", fn: () => { state.current.scale = Math.min(3, state.current.scale * 1.25); } },
            { label: "−", fn: () => { state.current.scale = Math.max(0.35, state.current.scale / 1.25); } },
            { label: "⟲", fn: () => { state.current.scale = 1; state.current.tx = 0; state.current.ty = 0; } },
          ].map((b) => (
            <button
              key={b.label}
              type="button"
              aria-label={b.label === "+" ? "Zoom in" : b.label === "−" ? "Zoom out" : "Reset graph view"}
              onClick={(e) => { e.stopPropagation(); b.fn(); requestDraw.current(); }}
              className="glass-strong h-9 w-9 rounded-xl text-sm font-semibold text-slate-200 hover:text-white"
            >
              {b.label}
            </button>
          ))}
        </div>
        {hover && (
          <motion.div
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            className="glass-strong pointer-events-none absolute bottom-4 left-4 max-w-sm p-3"
          >
            <div className="text-xs font-semibold" style={{ color: KIND_COLORS[hover.kind] }}>
              {hover.kind.toUpperCase()}
            </div>
            <div className="mt-0.5 text-sm text-white">{hover.label}</div>
            {hover.year && <div className="text-xs text-faint">{hover.year}</div>}
          </motion.div>
        )}
      </div>
    </div>
  );
}
