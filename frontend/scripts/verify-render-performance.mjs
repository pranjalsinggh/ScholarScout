import fs from "node:fs";
import ts from "typescript";

// Synthetic runtime checks for scheduling, gesture continuity and streaming.
// No server, provider key or uploaded document is used.
const transpile = (path) => ts.transpileModule(fs.readFileSync(new URL(path, import.meta.url), "utf8"), {
  fileName: path,
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
}).outputText;
const graphCode = transpile("../src/components/network/GraphView.tsx");
const chatCode = transpile("../src/components/dashboard/ChatView.tsx");
const apiCode = transpile("../src/services/api.ts");
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const jsx = (type, props) => ({ type, props });
const nodesIn = (tree) => {
  const all = [];
  const visit = (node) => {
    if (!node || typeof node !== "object") return;
    if (Array.isArray(node)) { node.forEach(visit); return; }
    all.push(node); visit(node.props?.children);
  };
  visit(tree); return all;
};
function checkGraph(hz = 120, reduced = false) {
  const effects = [], refs = [], frames = new Map(), handlers = new Map(), observed = [];
  let frameId = 0, draws = 0, now = 0;
  const react = {
    useRef(value) { const ref = { current: value }; refs.push(ref); return ref; },
    useState(value) { return [value, () => {}]; },
    useMemo(callback) { return callback(); },
    useEffect(callback) { effects.push(callback); },
  };
  const exports = {};
  const ctx = new Proxy({}, {
    get(_target, key) { return key === "clearRect" ? () => { draws++; } : () => {}; },
    set() { return true; },
  });
  const canvas = { width: 0, height: 0, style: {}, getContext: () => ctx };
  const wrap = {
    clientWidth: 900, getBoundingClientRect: () => ({ left: 0, top: 0 }),
    addEventListener: (name, fn) => handlers.set(`wrap:${name}`, fn),
    removeEventListener: (name) => handlers.delete(`wrap:${name}`),
  };
  globalThis.document = {
    hidden: false, addEventListener: (name, fn) => handlers.set(name, fn),
    removeEventListener: (name) => handlers.delete(name),
  };
  globalThis.window = { matchMedia: () => ({ matches: reduced, addEventListener() {}, removeEventListener() {} }) };
  globalThis.requestAnimationFrame = (fn) => { const id = ++frameId; frames.set(id, fn); return id; };
  globalThis.cancelAnimationFrame = (id) => frames.delete(id);
  globalThis.ResizeObserver = class { observe() {} disconnect() {} };
  globalThis.IntersectionObserver = class { constructor(fn) { observed.push(fn); } observe() {} disconnect() {} };
  new Function("require", "exports", graphCode)((name) => name === "react" ? react : name === "react/jsx-runtime" ? { jsx, jsxs: jsx } : { motion: { div: "motion.div" } }, exports);
  const nodes = Array.from({ length: 80 }, (_, i) => ({ id: String(i), kind: i % 2 ? "author" : "paper", label: `Node ${i}`, meta: { main: i === 0 } }));
  const tree = exports.GraphView({ nodes, edges: nodes.slice(1).map((n) => ({ source: "0", target: n.id })), touch: true });
  refs[0].current = canvas; refs[1].current = wrap;
  const cleanups = effects.map(fn => fn());
  const frame = () => {
    const batch = [...frames.entries()]; frames.clear();
    now += 1000 / hz;
    // Deliberately pass tiny negative timestamp drift to catch frame skipping.
    batch.forEach(([, fn]) => fn(now - 0.00001));
  };
  for (let i = 0; i < hz && frames.size; i++) frame();
  if (!reduced) assert(draws >= 59 && draws <= 61, `${hz} Hz graph drew ${draws} times in the first second.`);
  for (let i = 0; i < hz * 12 && frames.size; i++) frame();
  assert(!frames.size, "Graph kept drawing after its layout should settle.");
  if (reduced) assert(draws === 1, `Reduced motion drew ${draws} frames.`);
  const idleDraws = draws;
  let prevented = false;
  handlers.get("wrap:wheel")({ deltaY: -1, preventDefault() { prevented = true; } }); frame();
  assert(prevented && draws === idleDraws + 1 && !frames.size, "Settled zoom must draw one frame and prevent page scrolling.");
  document.hidden = true; handlers.get("visibilitychange")();
  handlers.get("wrap:wheel")({ deltaY: -1, preventDefault() {} });
  assert(!frames.size, "Hidden graph scheduled drawing.");
  document.hidden = false; handlers.get("visibilitychange")(); frame();
  observed[0]([{ isIntersecting: false }]);
  handlers.get("wrap:wheel")({ deltaY: -1, preventDefault() {} });
  assert(!frames.size, "Offscreen graph scheduled drawing.");
  observed[0]([{ isIntersecting: true }]); frame();
  const surface = nodesIn(tree).find(node => node.props?.ref === refs[1]).props;
  const pointer = (id, x, y = 100) => ({ pointerId: id, clientX: x, clientY: y, target: { setPointerCapture() {} } });
  const state = refs[4].current;
  surface.onPointerDown(pointer(1, 100));
  for (let i = 1; i <= 5; i++) surface.onPointerMove(pointer(1, 100 + i));
  assert(state.moved, "Slow accumulated pan was classified as a click.");
  surface.onPointerUp(pointer(1, 105));
  surface.onPointerDown(pointer(1, 100)); surface.onPointerDown(pointer(2, 200));
  surface.onPointerMove(pointer(1, 90)); surface.onPointerMove(pointer(2, 210));
  surface.onPointerUp(pointer(2, 210));
  const beforePan = state.tx;
  surface.onPointerMove(pointer(1, 92));
  assert(state.tx - beforePan === 2, "Pinch-to-pan jumped from the released pointer's coordinate.");
  surface.onPointerUp(pointer(1, 92));
  cleanups.filter(Boolean).forEach(fn => fn());
  assert(!frames.size && !handlers.size, "Graph did not clean up animation/listeners.");
  return { hz, reduced, drawsBeforeIdle: idleDraws, idlePause: "passed", gestures: "passed", cleanup: "passed" };
}
async function checkChat(events, expected, abortFallback = false) {
  const hooks = [], pendingEffects = [];
  let cursor = 0, updates = 0, fallbackAborted = false;
  const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => v === b[i]);
  const react = {
    memo: fn => fn,
    useState(initial) {
      const i = cursor++; if (!(i in hooks)) hooks[i] = { value: initial };
      return [hooks[i].value, value => { hooks[i].value = typeof value === "function" ? value(hooks[i].value) : value; if (i === 0) updates++; }];
    },
    useRef(initial) { const i = cursor++; if (!(i in hooks)) hooks[i] = { current: initial }; return hooks[i]; },
    useEffect(fn, deps) {
      const i = cursor++;
      if (!same(hooks[i]?.deps, deps)) {
        hooks[i]?.cleanup?.(); pendingEffects.push(() => { hooks[i] = { deps, cleanup: fn() }; });
      }
    },
    useCallback(fn, deps) {
      const i = cursor++; if (!same(hooks[i]?.deps, deps)) hooks[i] = { deps, value: fn }; return hooks[i].value;
    },
  };
  const api = {
    get: async path => path.includes("history") ? [] : path.includes("suggestions") ? { suggestions: [] } : { enabled: false },
    post: async (path, body, signal) => {
      assert(abortFallback && signal, "Fallback must receive its request AbortSignal.");
      return new Promise((_resolve, reject) => signal.addEventListener("abort", () => { fallbackAborted = true; reject(signal.reason); }));
    },
  };
  const exports = {};
  new Function("require", "exports", chatCode)(name => name === "react" ? react : name === "react/jsx-runtime" ? { jsx, jsxs: jsx } : name === "framer-motion" ? { motion: { div: "motion.div" } } : name.includes("/api") ? { api, friendlyError: e => String(e), ownerToken: () => "test-owner" } : new Proxy({}, { get: (_t, k) => k }), exports);
  const render = () => { cursor = 0; const tree = exports.default({ paperId: "test-paper" }); pendingEffects.splice(0).forEach(fn => fn()); return tree; };
  globalThis.requestAnimationFrame = () => 1; globalThis.cancelAnimationFrame = () => {};
  render(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  const tree = render();
  const suggestion = nodesIn(tree).find(node => node.type === "button" && node.props.children === "What is this document about?");
  assert(suggestion && !suggestion.props.disabled, "Starter question not ready.");
  let delivered = false;
  globalThis.fetch = async () => abortFallback ? { ok: false } : {
    ok: true, body: { getReader: () => ({ read: async () => delivered ? { done: true } :
      (delivered = true, { done: false, value: new TextEncoder().encode(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join("")) }) }) },
  };
  updates = 0;
  const sent = suggestion.props.onClick();
  if (abortFallback) {
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    hooks.forEach(hook => hook?.cleanup?.()); await sent;
    assert(fallbackAborted, "Leaving the conversation did not abort classic fallback.");
    return { fallbackOnLeave: "aborted", staleWrites: updates > 2 ? "unexpected" : "none" };
  }
  await sent;
  const last = hooks[0].value.at(-1);
  assert(last.content === expected && !last.streaming && last.id === 42, `Incorrect final stream message: ${JSON.stringify(last)}`);
  if (events.some(event => event.type === "evidence")) assert(last.evidence?.[0]?.page === 1, "Evidence was lost.");
  assert(updates <= 7, `Token flood caused ${updates} message updates.`);
  hooks.forEach(hook => hook?.cleanup?.());
  return { events: events.length, messageUpdates: updates, finalTextAndEvidence: "passed" };
}
async function checkApiSignal() {
  const exports = {};
  const storage = new Map([["ss_owner_token", "test-owner"]]);
  globalThis.sessionStorage = { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) };
  let receivedSignal;
  globalThis.fetch = async (_path, init) => { receivedSignal = init.signal; return { ok: true, status: 200, json: async () => ({ answer: "test" }) }; };
  new Function("exports", apiCode)(exports);
  const controller = new AbortController();
  await exports.api.post("/chat", { message: "test" }, controller.signal);
  assert(receivedSignal === controller.signal, "API helper did not pass the AbortSignal to fetch.");
  return { apiAbortSignal: "passed" };
}
const tokens = Array.from({ length: 100 }, (_, i) => ({ type: "token", text: `word${i} ` }));
console.log(JSON.stringify({
  graph: [checkGraph(60), checkGraph(120), checkGraph(120, true)],
  chat: [
    await checkChat([{ type: "evidence", evidence: [{ page: 1, snippet: "source" }] }, ...tokens, { type: "done", message_id: 42, mode: "synthesized:gemini", insufficient: false }], tokens.map(t => t.text).join("").trim()),
    await checkChat([{ type: "token", text: "Provider refusal" }, { type: "fallback" }, { type: "token", text: "Grounded local answer" }, { type: "done", message_id: 42, mode: "extractive" }], "Grounded local answer"),
    await checkChat([], "", true),
  ],
  api: await checkApiSignal(),
}, null, 2));
