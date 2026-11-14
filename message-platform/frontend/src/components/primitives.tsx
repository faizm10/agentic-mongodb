"use client";

import { AnimatePresence, motion } from "motion/react";
import { X } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { initials } from "@/lib/format";
import type { Conversation, User } from "@/lib/types";

/* ------------------------------------------------------------------ brand */
export function LogoMark({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <defs>
        <linearGradient id="nc-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="rgb(var(--accent))" />
          <stop offset="1" stopColor="#22b8cf" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill="url(#nc-g)" />
      <path d="M9 21.5V12a3 3 0 0 1 3-3h5.5a3 3 0 0 1 3 3v3.5a3 3 0 0 1-3 3H13l-4 3Z" fill="white" fillOpacity=".95" />
      <path d="M22.5 13.2h.5a2.5 2.5 0 0 1 2.5 2.5v8.3l-3.2-2.4H17a2.5 2.5 0 0 1-2.4-1.8" fill="none" stroke="white" strokeOpacity=".75" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="23.6" cy="8.4" r="1.6" fill="white" />
    </svg>
  );
}

export function Wordmark() {
  return (
    <div className="flex items-center gap-2.5">
      <LogoMark size={30} />
      <span className="font-display text-[17px] font-semibold tracking-tight">
        Nova<span className="text-accent">Connect</span>
      </span>
    </div>
  );
}

/* ---------------------------------------------------------------- avatars */
const SIZES = { xs: "h-6 w-6 text-[10px]", sm: "h-8 w-8 text-xs", md: "h-10 w-10 text-sm", lg: "h-12 w-12 text-base", xl: "h-20 w-20 text-2xl" };
const DOT = { xs: "h-2 w-2", sm: "h-2.5 w-2.5", md: "h-3 w-3", lg: "h-3.5 w-3.5", xl: "h-4 w-4" };

export function Avatar({ user, size = "md", online, className = "" }: { user?: User; size?: keyof typeof SIZES; online?: boolean; className?: string }) {
  const hue = user?.avatar_hue ?? 220;
  return (
    <span className={`relative inline-flex flex-none ${className}`}>
      <span
        className={`${SIZES[size]} inline-flex select-none items-center justify-center rounded-full font-semibold text-white ring-1 ring-black/5`}
        style={{ background: `linear-gradient(140deg, hsl(${hue} 78% 64%), hsl(${(hue + 38) % 360} 70% 46%))` }}
        aria-hidden="true"
      >
        {user ? initials(user.name) : "?"}
      </span>
      {online !== undefined && (
        <span
          className={`${DOT[size]} absolute bottom-0 right-0 rounded-full ring-2 ring-surface ${online ? "bg-success" : "bg-faint/70"}`}
          aria-label={online ? "Online" : "Offline"}
        />
      )}
    </span>
  );
}

export function ConversationAvatar({
  conversation,
  me,
  users,
  online,
  size = "md",
}: {
  conversation: Conversation;
  me: string;
  users: Record<string, User>;
  online: Record<string, true>;
  size?: keyof typeof SIZES;
}) {
  if (conversation.kind === "direct") {
    const other = conversation.participant_ids.find((id) => id !== me);
    return <Avatar user={other ? users[other] : undefined} size={size} online={other ? Boolean(online[other]) : undefined} />;
  }
  const hue = conversation.title.split("").reduce((h, ch) => (h + ch.charCodeAt(0) * 7) % 360, 0);
  return (
    <span
      className={`${SIZES[size]} inline-flex flex-none items-center justify-center rounded-2xl ring-1 ring-line`}
      style={{ background: `linear-gradient(140deg, hsl(${hue} 70% 92% / .9), hsl(${(hue + 40) % 360} 70% 80% / .9))` }}
      aria-hidden="true"
    >
      <span className={size === "xl" ? "text-4xl" : size === "lg" ? "text-2xl" : "text-lg"}>{conversation.emoji || "💬"}</span>
    </span>
  );
}

/* ---------------------------------------------------------------- dialogs */
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  width = "max-w-md",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
  width?: string;
}) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    window.setTimeout(() => panel.current?.querySelector<HTMLElement>("input, button, [tabindex]")?.focus(), 30);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-50 flex items-end justify-center p-0 sm:items-center sm:p-6">
          <motion.button
            aria-label="Close dialog"
            className="absolute inset-0 bg-black/40 backdrop-blur-[2px]"
            onClick={onClose}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          />
          <motion.div
            ref={panel}
            role="dialog"
            aria-modal="true"
            aria-label={title}
            className={`relative w-full ${width} max-h-[90vh] overflow-hidden rounded-t-3xl bg-raised shadow-pop sm:rounded-3xl`}
            initial={{ opacity: 0, y: 24, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16, scale: 0.98 }}
            transition={{ type: "spring", stiffness: 420, damping: 34 }}
          >
            <div className="flex items-start justify-between gap-4 border-b border-line px-6 pb-4 pt-5">
              <div>
                <h2 className="font-display text-lg font-semibold tracking-tight">{title}</h2>
                {description && <p className="mt-0.5 text-sm text-muted">{description}</p>}
              </div>
              <button className="icon-btn -mr-2" onClick={onClose} aria-label="Close">
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="scroll-thin max-h-[calc(90vh-80px)] overflow-y-auto">{children}</div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}

export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Dialog open={open} onClose={onCancel} title={title} width="max-w-sm">
      <div className="px-6 py-5">
        <p className="text-sm leading-relaxed text-muted">{body}</p>
        <div className="mt-6 flex justify-end gap-2">
          <button className="btn-ghost" onClick={onCancel}>
            Cancel
          </button>
          <button className="btn bg-danger text-white hover:brightness-110" onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ menus */
export function useDismiss(open: boolean, onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open, onClose]);
  return ref;
}

export function MenuItem({
  icon: Icon,
  label,
  onClick,
  danger,
  shortcut,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  onClick: () => void;
  danger?: boolean;
  shortcut?: string;
}) {
  return (
    <button
      role="menuitem"
      onClick={onClick}
      className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm transition ${danger ? "text-danger hover:bg-danger/10" : "text-ink hover:bg-sunken"}`}
    >
      <Icon className="h-4 w-4 opacity-80" />
      <span className="flex-1">{label}</span>
      {shortcut && <kbd className="text-[11px] text-faint">{shortcut}</kbd>}
    </button>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative h-6 w-10 flex-none rounded-full transition ${checked ? "bg-accent" : "bg-line"}`}
    >
      <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${checked ? "left-[18px]" : "left-0.5"}`} />
    </button>
  );
}

/* ----------------------------------------------------------------- toasts */
export type Toast = { id: number; title: string; body?: string; tone?: "info" | "success" | "error"; user?: User; onClick?: () => void };
type ToastApi = { push: (t: Omit<Toast, "id">) => void };
const ToastContext = createContext<ToastApi>({ push: () => {} });
export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);
  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const push = useCallback(
    (t: Omit<Toast, "id">) => {
      const id = ++seq.current;
      setToasts((list) => [...list.slice(-3), { ...t, id }]);
      window.setTimeout(() => dismiss(id), t.tone === "error" ? 7000 : 4500);
    },
    [dismiss],
  );
  return (
    <ToastContext.Provider value={{ push }}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(360px,calc(100vw-2rem))] flex-col gap-2" aria-live="polite">
        <AnimatePresence initial={false}>
          {toasts.map((t) => (
            <motion.div
              key={t.id}
              layout
              initial={{ opacity: 0, y: 16, scale: 0.96 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, x: 40 }}
              className="glass pointer-events-auto flex items-start gap-3 rounded-2xl border border-line p-3.5 shadow-float"
            >
              {t.user ? (
                <Avatar user={t.user} size="sm" />
              ) : (
                <span className={`mt-1 h-2.5 w-2.5 flex-none rounded-full ${t.tone === "error" ? "bg-danger" : t.tone === "success" ? "bg-success" : "bg-accent"}`} />
              )}
              <button
                className="min-w-0 flex-1 text-left"
                onClick={() => {
                  t.onClick?.();
                  dismiss(t.id);
                }}
              >
                <p className="text-sm font-semibold">{t.title}</p>
                {t.body && <p className="mt-0.5 line-clamp-2 text-sm text-muted">{t.body}</p>}
              </button>
              <button className="text-faint hover:text-ink" onClick={() => dismiss(t.id)} aria-label="Dismiss notification">
                <X className="h-4 w-4" />
              </button>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}
