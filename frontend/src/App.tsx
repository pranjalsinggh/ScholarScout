import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { MotionConfig } from "framer-motion";
import { lazy, Suspense, useEffect, useLayoutEffect, useState } from "react";
import { ShieldAlert, ShieldCheck, X } from "lucide-react";
import Landing from "./pages/Landing";
import {
  dismissPrivacyBanner, dismissSessionEndNotice, hasSessionPapers,
  privacyBannerSeen, sessionEndNotice, startNewSession,
} from "./services/api";
import type { SessionEndNotice } from "./services/api";

const UploadPage = lazy(() => import("./pages/Upload"));
const Library = lazy(() => import("./pages/Library"));
const Dashboard = lazy(() => import("./pages/Dashboard"));

export default function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const [endNotice, setEndNotice] = useState<SessionEndNotice>(sessionEndNotice);
  const [showPrivacy, setShowPrivacy] = useState(() => sessionEndNotice() !== "expired" && hasSessionPapers() && !privacyBannerSeen());
  const [sessionGeneration, setSessionGeneration] = useState(0);

  useLayoutEffect(() => {
    // Route changes reset immediately; smooth scrolling is reserved for landing anchors.
    document.documentElement.removeAttribute("data-landing");
    const landing = location.pathname === "/";
    if (!landing || !location.hash) window.scrollTo({ top: 0, left: 0, behavior: "instant" });
    if (landing) {
      document.documentElement.setAttribute("data-landing", "");
      if (location.hash === "#how" || location.hash === "#features") {
        document.getElementById(location.hash.slice(1))?.scrollIntoView({ behavior: "auto", block: "start" });
      }
    }
    return () => document.documentElement.removeAttribute("data-landing");
  }, [location.pathname, location.hash, location.key]);

  useEffect(() => {
    const onPapers = () => setShowPrivacy(sessionEndNotice() !== "expired" && hasSessionPapers() && !privacyBannerSeen());
    const onSessionEnded = () => {
      const notice = sessionEndNotice();
      setEndNotice(notice);
      if (notice === "expired") setShowPrivacy(false);
    };
    window.addEventListener("scholarscout:papers", onPapers);
    window.addEventListener("scholarscout:session-ended", onSessionEnded);
    return () => {
      window.removeEventListener("scholarscout:papers", onPapers);
      window.removeEventListener("scholarscout:session-ended", onSessionEnded);
    };
  }, []);

  return (
    <MotionConfig reducedMotion="user" transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}>
        <Suspense fallback={<div className="grid min-h-screen place-items-center text-sm text-dim">Opening your workspace…</div>}>
          <Routes key={sessionGeneration} location={location}>
            <Route path="/" element={<Landing />} />
            <Route path="/upload" element={<UploadPage />} />
            <Route path="/library" element={<Library />} />
            <Route path="/paper/:paperId/:section?" element={<Dashboard />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>

      {(endNotice || showPrivacy) && (
        <div className="fixed bottom-3 left-3 right-3 z-[70] mx-auto max-w-lg space-y-2 sm:bottom-5" role="status">
          {endNotice && (
            <div className="glass-strong flex items-start gap-3 border-iris-400/30 p-4 shadow-2xl">
              {endNotice === "expired"
                ? <ShieldAlert size={19} className="mt-0.5 shrink-0 text-amber-300" />
                : <ShieldCheck size={19} className="mt-0.5 shrink-0 text-iris-300" />}
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-white">Previous session ended</p>
                <p className="mt-1 text-xs leading-relaxed text-dim">
                  {endNotice === "confirmed"
                    ? "The server confirmed your papers and chats were erased. Start a new session whenever you're ready."
                    : endNotice === "expired"
                    ? "Session ended. Previous research is unavailable. Idle cleanup removes abandoned data."
                    : "Your previous papers are no longer available in this tab. A deletion request was sent; automatic idle expiry clears data if that request did not arrive."}
                </p>
                {endNotice === "expired" && (
                  <button className="btn-primary mt-3 px-4 py-2 text-xs" onClick={() => {
                    startNewSession();
                    setEndNotice(null);
                    setShowPrivacy(false);
                    setSessionGeneration((generation) => generation + 1);
                    navigate("/upload", { replace: true });
                  }}>Start a new session</button>
                )}
              </div>
              <button aria-label="Dismiss session notice" onClick={() => { dismissSessionEndNotice(); setEndNotice(null); }} className="rounded-lg p-1 text-slate-400 hover:bg-white/10 hover:text-white"><X size={16} /></button>
            </div>
          )}
          {showPrivacy && endNotice !== "expired" && (
            <div className="glass-strong flex items-start gap-3 border-amber-400/25 p-4 shadow-2xl">
              <ShieldAlert size={19} className="mt-0.5 shrink-0 text-amber-300" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-white">This library lasts only for this tab session</p>
                <p className="mt-1 text-xs leading-relaxed text-dim">
                  Leaving or refreshing requests deletion of your papers and chats. Export anything you need first. You can erase everything immediately in the <Link to="/library" className="text-iris-300 underline underline-offset-2">library privacy panel</Link>.
                </p>
              </div>
              <button aria-label="Dismiss privacy notice" onClick={() => { dismissPrivacyBanner(); setShowPrivacy(false); }} className="rounded-lg p-1 text-slate-400 hover:bg-white/10 hover:text-white"><X size={16} /></button>
            </div>
          )}
        </div>
      )}
    </MotionConfig>
  );
}
