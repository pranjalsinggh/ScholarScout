import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Lock, Menu, Plus, X } from "lucide-react";

/**
 * Floating Liquid Glass navbar - pill-shaped, frosted, with a scroll-aware
 * intensity shift and an animated mobile drawer.
 */
export function GlassNavbar({ active }: { active?: "library" | "upload" | "home" }) {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    let aboveThreshold = false;
    const onScroll = () => {
      const next = window.scrollY > 24;
      if (next === aboveThreshold) return;
      aboveThreshold = next;
      setScrolled(next);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <motion.header
      initial={reducedMotion ? false : { y: -60, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: reducedMotion ? 0 : 0.5, ease: [0.22, 1, 0.36, 1] }}
      className={`nav-shell ${scrolled ? "scrolled" : ""} ${open ? "menu-open" : ""}`}
    >
      <div className="flex h-14 items-center justify-between gap-3 px-4 sm:px-5">
        {/* logo */}
        <Link to="/" className="group flex shrink-0 items-center gap-2.5">
          <span className="logo-orb h-8 w-8 rounded-full transition-transform duration-300 group-hover:scale-110" />
          <span className="text-[15px] font-bold tracking-tight text-white">
            Scholar<span className="gradient-text">Scout</span>
          </span>
        </Link>

        {/* desktop links */}
        <nav className="hidden items-center gap-1 sm:flex">
          <Link to="/" className={`nav-link ${active === "home" ? "active" : ""}`}>
            {active === "home" && <motion.span layoutId={reducedMotion ? undefined : "nav-glow"} className="nav-link-glow" transition={reducedMotion ? { duration: 0 } : { type: "spring", stiffness: 380, damping: 32 }} />}
            <span className="relative z-10">Home</span>
          </Link>
          {active === "home" && (
            <>
              <a href="#how" className="nav-link hidden lg:inline-flex">How it works</a>
              <a href="#features" className="nav-link hidden lg:inline-flex">Features</a>
            </>
          )}
          <Link to="/library" className={`nav-link ${active === "library" ? "active" : ""}`}>
            {active === "library" && <motion.span layoutId={reducedMotion ? undefined : "nav-glow"} className="nav-link-glow" transition={reducedMotion ? { duration: 0 } : { type: "spring", stiffness: 380, damping: 32 }} />}
            <span className="relative z-10">Library</span>
          </Link>
        </nav>

        {/* CTA */}
        <div className="hidden shrink-0 items-center gap-2.5 sm:flex">
          <button onClick={() => nav("/upload")} className="btn-primary px-4 py-2 text-sm">
            <Plus size={15} /> Analyze a Paper
          </button>
        </div>

        {/* privacy indicator */}
        <span
          className="hidden md:inline-flex chip !py-1 cursor-default"
          title="Anonymous tab session. Uploaded PDFs and chats are encrypted. Leaving requests deletion; idle sessions expire automatically."
        >
          <Lock size={11} className="text-emerald-300" /> Session only
        </span>

        {/* mobile toggle */}
        <button
          onClick={() => setOpen((v) => !v)}
          className="rounded-full p-2 text-slate-300 transition-colors hover:bg-white/10 hover:text-white sm:hidden"
          aria-label={open ? "Close menu" : "Open menu"}
          aria-expanded={open}
        >
          {open ? <X size={19} /> : <Menu size={19} />}
        </button>
      </div>

      {/* mobile drawer */}
      <AnimatePresence>
        {open && (
          <motion.nav
            initial={reducedMotion ? false : { height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: reducedMotion ? 0 : 0.3, ease: [0.22, 1, 0.36, 1] }}
            className="overflow-hidden sm:hidden"
          >
            <div className="space-y-1 border-t border-white/10 p-3">
              {[
                { to: "/", label: "Home", key: "home" },
                { to: "/library", label: "Library", key: "library" },
                { to: "/upload", label: "Analyze a Paper", key: "upload" },
              ].map((l, i) => (
                <motion.div
                  key={l.key}
                  initial={reducedMotion ? false : { opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: reducedMotion ? 0 : 0.2, delay: reducedMotion ? 0 : 0.05 * i }}
                >
                  <Link
                    to={l.to}
                    onClick={() => setOpen(false)}
                    className={`block rounded-xl px-4 py-2.5 text-sm font-medium transition-colors ${
                      active === l.key
                        ? "bg-gradient-to-r from-iris-500/20 to-azure-500/15 text-white"
                        : "text-slate-400 hover:bg-white/5 hover:text-white"
                    }`}
                  >
                    {l.label}
                  </Link>
                </motion.div>
              ))}
              {active === "home" && (
                <>
                  <a href="#how" onClick={() => setOpen(false)} className="block rounded-xl px-4 py-2.5 text-sm font-medium text-slate-400 transition-colors hover:bg-white/5 hover:text-white">How it works</a>
                  <a href="#features" onClick={() => setOpen(false)} className="block rounded-xl px-4 py-2.5 text-sm font-medium text-slate-400 transition-colors hover:bg-white/5 hover:text-white">Features</a>
                </>
              )}
            </div>
          </motion.nav>
        )}
      </AnimatePresence>
    </motion.header>
  );
}
