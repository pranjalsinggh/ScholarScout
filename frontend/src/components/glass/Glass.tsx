import { motion, type HTMLMotionProps } from "framer-motion";
import type { ReactNode } from "react";

/** Frosted glass pane - the core Liquid Glass surface. */
export function GlassCard({
  children,
  className = "",
  strong = false,
  sheen = true,
  ...rest
}: {
  children: ReactNode;
  className?: string;
  strong?: boolean;
  sheen?: boolean;
} & HTMLMotionProps<"div">) {
  const surfaceClass = `${strong ? "glass-strong" : "glass"} ${sheen ? "glass-sheen" : ""} ${className}`;
  // Most cards are static surfaces inside an animated parent. Avoid registering
  // a separate motion element for each of those cards in long dossiers/lists.
  if (Object.keys(rest).length === 0) {
    return <div className={surfaceClass}>{children}</div>;
  }
  return (
    <motion.div
      className={surfaceClass}
      {...rest}
    >
      {children}
    </motion.div>
  );
}

export function GlassButton({
  children,
  variant = "primary",
  className = "",
  ...rest
}: {
  children: ReactNode;
  variant?: "primary" | "ghost";
  className?: string;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const base = variant === "primary" ? "btn-primary" : "btn-ghost";
  return (
    <button className={`${base} ${className}`} {...rest}>
      {children}
    </button>
  );
}

export function Chip({
  children,
  tone = "default",
  className = "",
}: {
  children: ReactNode;
  tone?: "default" | "accent" | "azure" | "warn" | "danger" | "ok";
  className?: string;
}) {
  const tones: Record<string, string> = {
    default: "",
    accent: "chip-accent",
    azure: "chip-azure",
    warn: "chip-warn",
    danger: "chip-danger",
    ok: "chip-ok",
  };
  return <span className={`chip ${tones[tone]} ${className}`}>{children}</span>;
}

export function SectionTitle({
  title,
  sub,
  icon,
}: {
  title: string;
  sub?: string;
  icon?: ReactNode;
}) {
  return (
    <div className="mb-5">
      <h2 className="flex items-center gap-2.5 text-xl font-semibold text-white">
        {icon && <span className="text-iris-400">{icon}</span>}
        {title}
      </h2>
      {sub && <p className="mt-1 text-sm text-faint">{sub}</p>}
    </div>
  );
}

/** Staggered entrance for dashboard cards. */
export function stagger(delay = 0.05) {
  return {
    initial: { opacity: 0, y: 14 },
    animate: { opacity: 1, y: 0 },
    transition: { duration: 0.32, ease: [0.22, 1, 0.36, 1], delay: Math.min(Math.max(delay, 0), 0.24) },
  };
}
