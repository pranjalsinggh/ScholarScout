import { memo, useCallback, useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import {
  FileText, Loader2, MessageSquareText, SendHorizonal, Sparkles,
  ThumbsDown, ThumbsUp, Check, Cloud, Lock,
} from "lucide-react";
import { Chip } from "../glass/Glass";
import { api, fetchAuthFile, friendlyError, notifySessionResponse, ownerToken } from "../../services/api";
import type { ChatResponse, EvidenceCard } from "../../types/api";

interface Msg {
  id?: number;
  role: "user" | "assistant";
  content: string;
  evidence?: EvidenceCard[];
  insufficient?: boolean;
  mode?: string;
  cached?: boolean;
  streaming?: boolean;
}

const SUGGESTIONS = [
  "What is this document about?",
  "What are its key points?",
  "What evidence supports its main claims?",
  "What context does it provide?",
  "What does it leave unresolved?",
];

export default function ChatView({ paperId }: { paperId: string }) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [suggestions, setSuggestions] = useState<string[]>(SUGGESTIONS);
  const [rated, setRated] = useState<Record<number, "up" | "down">>({});
  const [feedbackError, setFeedbackError] = useState("");
  const [cloudAI, setCloudAI] = useState(false);
  const [cloudAvailable, setCloudAvailable] = useState(false);
  const [cloudBusy, setCloudBusy] = useState(false);
  const [cloudLoading, setCloudLoading] = useState(true);
  const [cloudPreferenceReady, setCloudPreferenceReady] = useState(false);
  const [cloudRetry, setCloudRetry] = useState(0);
  const [cloudConsentOpen, setCloudConsentOpen] = useState(false);
  const [cloudError, setCloudError] = useState("");
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState("");
  const [historyRetry, setHistoryRetry] = useState(0);
  const [fileError, setFileError] = useState("");
  const messagesRef = useRef<HTMLDivElement>(null);
  const followBottom = useRef(true);
  const ratedRef = useRef(rated);
  ratedRef.current = rated;
  const activeStream = useRef<AbortController | null>(null);
  const preferencePending = useRef(false);
  const confirmedCloudAI = useRef(false);
  const cloudPreferenceRequest = useRef<Promise<unknown> | null>(null);
  const historyPending = useRef(false);

  useEffect(() => {
    let alive = true;
    preferencePending.current = true;
    setCloudLoading(true);
    setCloudPreferenceReady(false);
    const request = api.get<{ enabled: boolean; available?: boolean }>("/me/cloud-ai");
    cloudPreferenceRequest.current = request;
    request
      .then((pref) => { if (alive) { confirmedCloudAI.current = pref.enabled; setCloudAI(pref.enabled); setCloudAvailable(pref.available !== false); setCloudPreferenceReady(true); } })
      .catch(() => { if (alive) setCloudError("Could not confirm your answer mode. Select Local Evidence to continue safely."); })
      .finally(() => { if (alive) { preferencePending.current = false; setCloudLoading(false); } });
    return () => { alive = false; };
  }, [cloudRetry]);

  useEffect(() => {
    activeStream.current?.abort();
    activeStream.current = null;
    followBottom.current = true;
    setMessages([]);
    setLoading(false);
    setRated({});
    setSuggestions(SUGGESTIONS);
    setHistoryLoading(true);
    setHistoryError("");
    return () => { activeStream.current?.abort(); activeStream.current = null; };
  }, [paperId]);

  useEffect(() => {
    let alive = true;
    historyPending.current = true;
    setHistoryLoading(true);
    setHistoryError("");
    // The free deployment admits two requests per session. Keep this view's
    // startup reads sequential so the dashboard's preference read also fits.
    void (async () => {
      try { await cloudPreferenceRequest.current; }
      catch { /* preference loading already exposes its own retry action */ }
      if (!alive) return;
      try {
        const hist = await api.get<{ id: number; role: string; content: string; evidence: EvidenceCard[]; mode: string }[]>(`/chat/${paperId}/history`);
        if (alive) {
          // Replace the conversation, including an empty history. Retrying
          // must not append another copy of previously displayed answers.
          setMessages(hist.map((m) => ({
            id: m.id, role: m.role as "user" | "assistant", content: m.content,
            evidence: m.evidence, mode: m.mode,
          })));
        }
      } catch (reason) {
        if (alive) setHistoryError(`Earlier questions could not load. ${friendlyError(reason)} You can still ask a new question.`);
      } finally {
        if (alive) { historyPending.current = false; setHistoryLoading(false); }
      }
      if (!alive) return;
      api.get<{ suggestions: string[] }>(`/papers/${paperId}/suggestions`)
        .then((result) => { if (alive && result.suggestions.length) setSuggestions(result.suggestions); })
        .catch(() => { /* the neutral starter questions remain available */ });
    })();
    return () => { alive = false; };
  }, [paperId, historyRetry]);

  const retryHistory = () => {
    if (loading || historyLoading || activeStream.current || historyPending.current || cloudLoading || preferencePending.current) return;
    historyPending.current = true;
    setHistoryLoading(true);
    setHistoryRetry((value) => value + 1);
  };

  const selectAnswerMode = async (enabled: boolean) => {
    if (loading || historyLoading || activeStream.current || historyPending.current || cloudLoading || preferencePending.current) return;
    if (enabled && !cloudAvailable) return;
    if (cloudPreferenceReady && enabled === cloudAI) {
      setCloudConsentOpen(false);
      return;
    }
    // This ref closes the gap before React renders the pending state: a fast
    // second click or Enter cannot send a question with the previous consent.
    preferencePending.current = true;
    setCloudBusy(true);
    setCloudError("");
    try {
      const pref = await api.post<{ enabled: boolean }>("/me/cloud-ai", { enabled });
      confirmedCloudAI.current = pref.enabled;
      setCloudAI(pref.enabled);
      setCloudPreferenceReady(true);
      setCloudConsentOpen(false);
    } catch (error) {
      setCloudError(friendlyError(error));
    } finally {
      preferencePending.current = false;
      setCloudBusy(false);
    }
  };

  const requestCloudMode = () => {
    if (loading || historyLoading || activeStream.current || historyPending.current || cloudLoading || preferencePending.current || !cloudAvailable || cloudAI) return;
    setCloudError("");
    setCloudConsentOpen(true);
  };

  useEffect(() => {
    if (!followBottom.current) return;
    // Move only the conversation scroller. Repeated smooth scrolling for every
    // token restarts the animation and can also move the surrounding page.
    const frame = requestAnimationFrame(() => {
      const container = messagesRef.current;
      if (container && followBottom.current) container.scrollTop = container.scrollHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [messages, loading]);

  const send = async (text?: string) => {
    const msg = (text ?? input).trim();
    if (msg.length < 2 || loading || historyLoading || activeStream.current || historyPending.current || cloudLoading || preferencePending.current || !cloudPreferenceReady) return;
    const answerMode = confirmedCloudAI.current ? "cloud" : "local";
    followBottom.current = true;
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: msg }]);
    setLoading(true);
    const controller = new AbortController();
    activeStream.current = controller;
    let pendingText = "";
    const requestToken = ownerToken();
    let sessionExpired = false;
    let tokenTimer: ReturnType<typeof setTimeout> | undefined;
    const flushTokens = () => {
      if (tokenTimer) clearTimeout(tokenTimer);
      tokenTimer = undefined;
      const text = pendingText;
      pendingText = "";
      if (!text || controller.signal.aborted) return;
      setMessages((prev) => {
        const copy = [...prev];
        const last = copy[copy.length - 1];
        if (last?.role === "assistant") copy[copy.length - 1] = { ...last, content: last.content + text };
        return copy;
      });
    };

    // placeholder assistant bubble that streams into
    setMessages((prev) => [...prev, { role: "assistant", content: "", streaming: true }]);

    try {
      const res = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Owner-Token": requestToken },
        body: JSON.stringify({ paper_id: paperId, message: msg, answer_mode: answerMode }),
        signal: controller.signal,
      });
      if (res.status === 410) {
        sessionExpired = true;
        notifySessionResponse(res.status, requestToken);
        throw new Error("Session ended. Start a new session to upload research again.");
      }
      if (!res.ok || !res.body) throw new Error("stream unavailable");

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let sawDone = false;
      let streamed = false;

      const apply = (event: any) => {
        if (controller.signal.aborted) return;
        if (event.type === "evidence") {
          setMessages((prev) => {
            const copy = [...prev];
            copy[copy.length - 1] = { ...copy[copy.length - 1], evidence: event.evidence };
            return copy;
          });
        } else if (event.type === "token") {
          streamed = true;
          pendingText += event.text;
          if (!tokenTimer) tokenTimer = setTimeout(flushTokens, 50);
        } else if (event.type === "fallback") {
          pendingText = "";
          if (tokenTimer) clearTimeout(tokenTimer);
          tokenTimer = undefined;
          streamed = false;
          setMessages((prev) => {
            const copy = [...prev];
            copy[copy.length - 1] = { ...copy[copy.length - 1], content: "", mode: "extractive" };
            return copy;
          });
        } else if (event.type === "error") {
          flushTokens();
          sawDone = true; // the backend finalized this question - don't retry
          setMessages((prev) => {
            const copy = [...prev];
            copy[copy.length - 1] = {
              ...copy[copy.length - 1],
              content: event.message || "Something interrupted this answer. Please try again.",
              insufficient: true, streaming: false, mode: "error",
            };
            return copy;
          });
        } else if (event.type === "done") {
          flushTokens();
          sawDone = true;
          setMessages((prev) => {
            const copy = [...prev];
            const last = copy[copy.length - 1];
            copy[copy.length - 1] = {
              ...last,
              content: last.content.trim() || "No answer could be produced from the available evidence. Try a more specific question.",
              id: event.message_id,
              mode: event.mode,
              cached: !!event.cached,
              insufficient: !!event.insufficient || !last.content.trim(),
              streaming: false,
            };
            return copy;
          });
          if (Array.isArray(event.suggestions) && event.suggestions.length) {
            setSuggestions(event.suggestions);
          }
        }
      };

      // parse SSE frames from the byte stream
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          for (const line of frame.split("\n")) {
            if (!line.startsWith("data:")) continue;
            try { apply(JSON.parse(line.slice(5).trim())); } catch { /* skip bad frame */ }
          }
        }
      }
      // stream opened but died before finalizing and nothing arrived →
      // recover through the classic request path
      if (!sawDone && !streamed) throw new Error("stream died");
      if (!sawDone && streamed) {
        flushTokens();
        setMessages((prev) => {
          const copy = [...prev];
          const last = copy[copy.length - 1];
          copy[copy.length - 1] = {
            ...last,
            content: `${last.content}\n\nThe connection ended before this answer finished. Check the evidence above or ask again.`,
            insufficient: true, mode: "partial", streaming: false,
          };
          return copy;
        });
      }
    } catch (e) {
      flushTokens();
      if (controller.signal.aborted) return;
      if (sessionExpired) {
        setMessages((prev) => [...prev.slice(0, -1), {
          role: "assistant", content: friendlyError(e), insufficient: true, mode: "error",
        }]);
        return;
      }
      // stream endpoint unavailable → classic request
      try {
        const r = await api.post<ChatResponse>("/chat", { paper_id: paperId, message: msg, answer_mode: answerMode }, controller.signal);
        if (controller.signal.aborted) return;
        setMessages((prev) => [...prev.slice(0, -1), {
          id: r.message_id ?? undefined, role: "assistant", content: r.answer,
          evidence: r.evidence, insufficient: r.insufficient, mode: r.mode,
          cached: !!r.cached,
        }]);
        if (r.suggestions.length) setSuggestions(r.suggestions);
      } catch (e2) {
        if (controller.signal.aborted) return;
        setMessages((prev) => [...prev.slice(0, -1), {
          role: "assistant", content: friendlyError(e2), insufficient: true,
        }]);
      }
    } finally {
      flushTokens();
      if (controller.signal.aborted) return;
      activeStream.current = null;
      setLoading(false);
      setMessages((prev) => {
        const copy = [...prev];
        const last = copy[copy.length - 1];
        if (last?.role === "assistant") copy[copy.length - 1] = { ...last, streaming: false };
        return copy;
      });
    }
  };

  const rate = useCallback(async (m: Msg, rating: "up" | "down") => {
    if (!m.id || ratedRef.current[m.id]) return;
    setFeedbackError("");
    setRated((prev) => ({ ...prev, [m.id!]: rating }));
    try {
      await api.post("/chat/feedback", { message_id: m.id, rating });
    } catch {
      setRated((prev) => {
        const next = { ...prev };
        delete next[m.id!];
        return next;
      });
      setFeedbackError("Feedback could not be saved. Please try again.");
    }
  }, []);

  const openSource = useCallback((e: EvidenceCard) => {
    setFileError("");
    void fetchAuthFile(`/papers/${e.paper_id || paperId}/file`, undefined, e.page)
      .catch((reason) => setFileError(friendlyError(reason)));
  }, [paperId]);

  return (
    <div className="flex flex-col" style={{ height: "max(440px, calc(100dvh - 220px))" }}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <MessageSquareText size={18} className="text-iris-400" />
        <h2 className="text-xl font-semibold text-white">Ask ScholarScout</h2>
        <Chip tone="accent" className="ml-2 hidden md:inline-flex">evidence-backed answers only</Chip>
        <Chip className="hidden md:inline-flex">feedback tunes this session</Chip>
      </div>
      {feedbackError && <p className="mb-2 text-xs text-rose-300" role="alert">{feedbackError}</p>}
      {historyError && (
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <p className="text-xs text-amber-200" role="status">{historyError}</p>
          <button type="button" onClick={retryHistory} disabled={loading || historyLoading || cloudBusy || cloudLoading} className="btn-ghost min-h-10 px-3 text-xs disabled:opacity-40">Retry history</button>
        </div>
      )}
      {fileError && <p className="mb-2 text-xs text-rose-300" role="alert">{fileError}</p>}

      <div className="glass-soft mb-3 p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-semibold text-white">
            {cloudLoading || cloudBusy ? <Loader2 size={16} className="animate-spin text-iris-300" /> : cloudAI ? <Cloud size={16} className="text-azure-300" /> : <Lock size={16} className="text-iris-300" />}
            {cloudLoading ? "Checking answer mode…" : cloudBusy ? "Saving answer mode…" : !cloudPreferenceReady ? "Choose your answer mode" : cloudAI ? "Cloud AI · this session" : "Local Evidence · this session"}
          </div>
          <p className="mt-1 max-w-2xl text-xs leading-relaxed text-dim">
            {cloudAvailable
              ? cloudAI
                ? "Cloud AI combines your evidence into richer answers. Questions, selected passages and recent chat context may go to Gemini or Groq. Switch to Local Evidence for future questions. Erasing your session cannot erase provider-held copies."
                : "Local Evidence extracts answers from your paper. Cloud AI is optional for richer synthesis; review the data-sharing notice before enabling it."
              : "Local Evidence extracts answers from your paper without sending your questions or passages to cloud AI. Cloud AI is currently unavailable on this instance."}
            {" "}<a className="text-azure-300 underline underline-offset-2" href="https://ai.google.dev/gemini-api/terms" target="_blank" rel="noreferrer">Gemini terms</a>
            {" · "}<a className="text-azure-300 underline underline-offset-2" href="https://console.groq.com/docs/your-data" target="_blank" rel="noreferrer">Groq data controls</a>
          </p>
          {cloudError && <p className="mt-1 text-xs text-rose-300" role="alert">{cloudError}</p>}
          {!cloudPreferenceReady && !cloudLoading && (
            <button type="button" disabled={cloudBusy || loading || historyLoading} className="mt-2 text-xs font-semibold text-azure-300 underline underline-offset-2 disabled:opacity-40" onClick={() => {
              preferencePending.current = true;
              setCloudLoading(true);
              setCloudError("");
              setCloudRetry((value) => value + 1);
            }}>Retry answer mode</button>
          )}
        </div>
        <div role="group" aria-label="Answer mode" aria-busy={cloudBusy || cloudLoading} className="flex shrink-0 rounded-2xl border border-white/10 bg-black/20 p-1">
          <button
            type="button"
            aria-pressed={cloudPreferenceReady && !cloudAI}
            onClick={() => selectAnswerMode(false)}
            disabled={loading || historyLoading || cloudBusy || cloudLoading}
            className={`inline-flex min-h-10 flex-1 items-center justify-center gap-2 rounded-xl px-3 text-xs font-semibold transition-colors disabled:cursor-wait disabled:opacity-60 ${cloudPreferenceReady && !cloudAI ? "bg-iris-500/20 text-iris-100 ring-1 ring-inset ring-iris-400/30" : "text-dim hover:bg-white/5 hover:text-white"}`}
          >
            <Lock size={14} />
            Local Evidence
          </button>
          <button
            type="button"
            aria-pressed={cloudPreferenceReady && cloudAI}
            aria-label="Cloud AI: allow questions and selected passages to be sent to Gemini or Groq"
            onClick={requestCloudMode}
            disabled={loading || historyLoading || cloudBusy || cloudLoading || !cloudAvailable}
            className={`inline-flex min-h-10 flex-1 items-center justify-center gap-2 rounded-xl px-3 text-xs font-semibold transition-colors disabled:opacity-40 ${cloudPreferenceReady && cloudAI ? "bg-azure-500/20 text-azure-100 ring-1 ring-inset ring-azure-400/30" : "text-dim hover:bg-white/5 hover:text-white"}`}
          >
            <Cloud size={14} />
            Cloud AI
          </button>
        </div>
        </div>
        {cloudConsentOpen && (
          <div className="mt-3 border-t border-white/10 pt-3" role="region" aria-label="Cloud AI data-sharing consent">
            <p className="text-sm font-semibold text-white">Before enabling Cloud AI</p>
            <p className="mt-1 max-w-2xl text-xs leading-relaxed text-dim">
              Your questions, selected paper passages and recent chat context may be sent to Gemini or Groq. Unpaid Gemini may use this data for product improvement and human review: keep confidential or personal research in Local Evidence. Erasing your ScholarScout session cannot recall provider-held copies.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button type="button" onClick={() => selectAnswerMode(true)} disabled={loading || historyLoading || cloudBusy || cloudLoading || !cloudAvailable} className="btn-primary inline-flex min-h-10 items-center gap-2 px-4 text-xs disabled:opacity-40">
                {cloudBusy ? <Loader2 size={14} className="animate-spin" /> : <Cloud size={14} />} Enable Cloud AI
              </button>
              <button type="button" onClick={() => setCloudConsentOpen(false)} disabled={cloudBusy} className="btn-ghost min-h-10 px-4 text-xs disabled:opacity-40">Keep Local Evidence</button>
            </div>
          </div>
        )}
      </div>

      {/* messages */}
      <div ref={messagesRef} className="glass mb-3 flex-1 space-y-4 overflow-y-auto overscroll-contain p-5" style={{ scrollBehavior: "auto" }} onScroll={(event) => {
        const container = event.currentTarget;
        followBottom.current = container.scrollHeight - container.scrollTop - container.clientHeight < 80;
      }}>
        {historyLoading ? <div className="flex items-center justify-center gap-2 py-8 text-sm text-dim"><Loader2 size={15} className="animate-spin text-iris-300" /> Loading this document's conversation…</div> : messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center text-center">
            <div className="mb-3 flex h-14 w-14 items-center justify-center rounded-3xl bg-gradient-to-br from-iris-500/30 to-azure-500/30 text-iris-300">
              <MessageSquareText size={24} />
            </div>
            <p className="max-w-sm text-sm text-dim">
              Ask about this document. Answers use retrieved passages and show their
              evidence cards. Cloud AI is optional for richer synthesis.
            </p>
          </div>
        )}
        {messages.map((m, i) => (
          <ChatMessage key={i} message={m} rating={m.id ? rated[m.id] : undefined} onRate={rate} onOpenSource={openSource} />
        ))}
        {loading && messages[messages.length - 1]?.content === "" && (
          <div className="flex items-center gap-2 text-sm text-faint">
            <Loader2 size={15} className="animate-spin text-iris-400" />
            Searching evidence passages…
          </div>
        )}
      </div>

      {/* suggestions */}
      <div className="mb-3 flex gap-2 overflow-x-auto pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {suggestions.slice(0, 5).map((s) => (
          <button key={s} disabled={loading || historyLoading || cloudLoading || cloudBusy || !cloudPreferenceReady} onClick={() => send(s)} className="chip whitespace-nowrap hover:bg-white/10 disabled:opacity-40">
            {s}
          </button>
        ))}
      </div>

      {/* composer */}
      <form
        onSubmit={(e) => { e.preventDefault(); send(); }}
        className="glass flex items-center gap-2 p-2 pl-4"
      >
        <input
          className="min-w-0 flex-1 bg-transparent text-sm text-white outline-none placeholder:text-slate-500"
          aria-label="Ask a question about this document"
          placeholder="Ask about this document…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
        <button type="submit" aria-label="Send question" disabled={loading || historyLoading || cloudLoading || cloudBusy || !cloudPreferenceReady || input.trim().length < 2} className="btn-primary shrink-0 p-2.5 disabled:opacity-40">
          <SendHorizonal size={16} />
        </button>
      </form>
    </div>
  );
}

const tidy = (text: string) =>
  text.replace(/【/g, "[").replace(/】/g, "]")
      .replace(/［/g, "[").replace(/］/g, "]")
      .replace(/\s*\[(?:abstract|paper)\]\s*/gi, " ");

const renderInline = (text: string) => {
  const parts = tidy(text).split(/(\*\*[^*]+\*\*)/g);
  return parts.map((p, i) =>
    p.startsWith("**") && p.endsWith("**") && p.length > 4
      ? <strong key={i} className="text-white">{p.slice(2, -2)}</strong>
      : p
  );
};

const renderBullets = (text: string) => {
  const lines = tidy(text).split("\n");
  const items: string[] = [];
  let lead = "";
  for (const l of lines) {
    if (/^[•\-]\s+/.test(l)) items.push(l.replace(/^[•\-]\s+/, ""));
    else lead += (lead ? " " : "") + l;
  }
  return (
    <>
      {lead && <p>{renderInline(lead)}</p>}
      {items.length > 0 && (
        <ul className="mt-2 space-y-1.5 pl-1">
          {items.map((it, i) => (
            <li key={i} className="flex gap-2">
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-iris-400/80" />
              <span>{renderInline(it)}</span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
};

// Previous bubbles keep the same message object while the last answer streams.
// Memoization avoids re-parsing and repainting the whole conversation per token.
const ChatMessage = memo(function ChatMessage({ message: m, rating, onRate, onOpenSource }: {
  message: Msg;
  rating?: "up" | "down";
  onRate: (message: Msg, rating: "up" | "down") => void;
  onOpenSource: (evidence: EvidenceCard) => void;
}) {
  return (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25 }}
            className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div className={`min-w-0 max-w-[95%] sm:max-w-[85%] ${m.role === "user" ? "order-1" : ""}`}>
              <div
                className={`break-words whitespace-pre-wrap rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                  m.role === "user"
                    ? "bg-gradient-to-br from-iris-500/85 to-azure-500/85 text-white shadow-[0_8px_28px_-8px_rgba(5,150,105,0.55)]"
                    : m.insufficient
                    ? "border border-amber-400/25 bg-amber-500/10 text-amber-100"
                    : "glass text-slate-200"
                }`}
              >
                {m.content.includes("\n• ") || m.content.includes("\n- ")
                  ? renderBullets(m.content)
                  : renderInline(m.content || "")}
                {m.streaming && (
                  <span className="ml-0.5 inline-block h-4 w-2 animate-pulse-soft rounded-sm bg-iris-400 align-middle" />
                )}
              </div>
              {m.role === "assistant" && m.mode?.startsWith("synthesized") && !m.insufficient && !m.streaming && (
                <div className="mt-1.5">
                  <Chip tone="accent">
                    <Sparkles size={11} /> AI-synthesized · {m.mode.split(":")[1] || "llm"}
                  </Chip>
                </div>
              )}
              {m.role === "assistant" && m.cached && !m.streaming && (
                <div className="mt-1.5"><Chip tone="ok"><Check size={11} /> Cached · no new provider call</Chip></div>
              )}
              {m.role === "assistant" && m.mode === "extractive" && !m.insufficient && !m.streaming && (
                <div className="mt-1.5"><Chip><Lock size={11} /> Local evidence answer</Chip></div>
              )}

              {/* feedback */}
              {m.role === "assistant" && !m.insufficient && m.id && !m.streaming && (
                <div className="mt-1.5 flex items-center gap-1.5">
                  {rating ? (
                    <Chip tone="ok"><Check size={11} /> Feedback saved for this session</Chip>
                  ) : (
                    <>
                      <button
                        onClick={() => onRate(m, "up")}
                        className="rounded-lg p-1.5 text-slate-500 transition-colors hover:bg-emerald-500/15 hover:text-emerald-300"
                        title="Helpful - retrieve more like this"
                      >
                        <ThumbsUp size={13} />
                      </button>
                      <button
                        onClick={() => onRate(m, "down")}
                        className="rounded-lg p-1.5 text-slate-500 transition-colors hover:bg-rose-500/15 hover:text-rose-300"
                        title="Not helpful - retrieve differently"
                      >
                        <ThumbsDown size={13} />
                      </button>
                      <span className="text-[10px] text-faint">rate this answer</span>
                    </>
                  )}
                </div>
              )}

              {m.evidence && m.evidence.length > 0 && (
                <div className="mt-2 space-y-2">
                  <div className="text-[10px] font-semibold uppercase tracking-widest text-faint">Evidence</div>
                  {m.evidence.map((e, j) => (
                    <details key={j} className="glass-soft p-3">
                      <summary className="cursor-pointer text-xs leading-relaxed text-azure-300">
                        <span className="font-semibold">[{j + 1}]</span> {e.paper_title || "Document"}
                        <span className="mt-1 block text-faint">{e.section || "document"}{e.page ? ` · page ${e.page}` : ""} · expand passage</span>
                      </summary>
                      <p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-slate-300">{e.snippet}</p>
                      <button type="button" onClick={() => onOpenSource(e)}
                        className="mt-2 inline-flex items-center gap-1 text-[11px] text-azure-300 hover:text-azure-200"><FileText size={12} /> Open source PDF{e.page ? ` · see page ${e.page}` : ""}</button>
                    </details>
                  ))}
                </div>
              )}
            </div>
          </motion.div>
  );
});
