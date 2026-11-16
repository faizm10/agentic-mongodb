"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { AlertTriangle, CheckCircle2, Database, FlaskConical, HelpCircle, LayoutDashboard, Search, Siren, XCircle } from "lucide-react";
import { type ApiError, DEV, type ErrorKind, type Meta, POLL_MS, type Status, type StorageInfo, type Window } from "@/lib/api";
import { STATUS_META, ago, clock, useNow } from "@/lib/format";

export function StatusIcon({ status, className = "h-4 w-4" }: { status: Status; className?: string }) {
  const Icon = { operational: CheckCircle2, degraded: AlertTriangle, down: XCircle, unknown: HelpCircle }[status];
  return <Icon className={`flex-none ${className}`} style={{ color: STATUS_META[status].color }} aria-hidden="true" />;
}

export type Tone = "ok" | "warn" | "crit" | "accent" | "neutral";
export const STATUS_TONE: Record<Status, Tone> = { operational: "ok", degraded: "warn", down: "crit", unknown: "neutral" };

// Literal class names so Tailwind keeps them.
const TONE_CLASS: Record<Tone, string> = {
  ok: "tone-ok", warn: "tone-warn", crit: "tone-crit", accent: "tone-accent", neutral: "tone-neutral",
};

export function Badge({ tone, children, title }: { tone: Tone; children: React.ReactNode; title?: string }) {
  return <span title={title} className={`badge ${TONE_CLASS[tone]}`}>{children}</span>;
}

export function StatusPill({ status }: { status: Status }) {
  return (
    <Badge tone={STATUS_TONE[status]}>
      <StatusIcon status={status} className="h-3.5 w-3.5" />
      {STATUS_META[status].label}
    </Badge>
  );
}

const DOT: Record<Status, string> = { operational: "bg-ok", degraded: "bg-warn", down: "bg-crit", unknown: "bg-faint" };

/** A status dot that is never shown without its text label. */
export function StatusText({ status, label, className = "" }: { status: Status; label?: string; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap ${className}`}>
      <span className={`h-2 w-2 flex-none rounded-full ${DOT[status]}`} aria-hidden="true" />
      {label ?? STATUS_META[status].label}
    </span>
  );
}

/** Page title row: heading, optional subtitle and actions on the right. */
export function PageHeader({ title, sub, children }: { title: React.ReactNode; sub?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end gap-4">
      <div className="min-w-0 flex-1">
        <h1 className="text-lg font-semibold">{title}</h1>
        {sub && <p className="mt-1 text-sm text-muted">{sub}</p>}
      </div>
      {children}
    </div>
  );
}

export function Logo() {
  return (
    <Link href="/" className="flex items-center gap-2.5" aria-label="OpsRelay status home">
      <svg width="30" height="30" viewBox="0 0 30 30" aria-hidden="true">
        <rect width="30" height="30" rx="8" fill="#16191e" stroke="#2f343c" />
        <path d="M7 19.5h4.5l2.5-8 3.5 11 2.5-7H23" fill="none" stroke="#3987e5" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="23" cy="15.5" r="1.8" fill="#0ca30c" />
      </svg>
      <span className="text-[15px] font-semibold tracking-tight">OpsRelay</span>
    </Link>
  );
}

/** Whether the data on screen is current: fresh, stale (refreshes missed) or unavailable (last refresh failed). */
export function freshness(updatedAt: number | null, error: boolean, now: number, intervalMs = POLL_MS) {
  if (updatedAt === null) return error ? "unavailable" : "loading";
  if (error) return "unavailable";
  return now - updatedAt > intervalMs * 2.5 ? "stale" : "fresh";
}

export function LiveIndicator({ updatedAt, error, intervalMs = POLL_MS }: { updatedAt: number | null; error: boolean; intervalMs?: number }) {
  const now = useNow();
  const state = freshness(updatedAt, error, now, intervalMs);
  const at = updatedAt ? new Date(updatedAt).toISOString() : null;
  const text = {
    loading: "Loading…",
    unavailable: at ? `Refresh failed · last refresh ${clock(at)}` : "Monitoring API unavailable",
    stale: `Stale · last refresh ${clock(at)}`,
    fresh: `Refreshed ${clock(at)} · every ${Math.round(intervalMs / 1000)}s`,
  }[state];
  return (
    <span className={`tabular inline-flex items-center gap-2 text-xs ${state === "fresh" || state === "loading" ? "text-muted" : "text-warn"}`} role="status">
      <span className={`h-2 w-2 flex-none rounded-full ${state === "fresh" ? "bg-ok" : state === "loading" ? "bg-faint" : "bg-warn"}`} aria-hidden="true" />
      {text}
    </span>
  );
}

/** Shown above the page when the visible data is the last known state rather than a current one. */
function StaleNotice({ updatedAt, error, intervalMs }: { updatedAt: number | null; error: ApiError | null; intervalMs: number }) {
  const now = useNow();
  const state = freshness(updatedAt, Boolean(error), now, intervalMs);
  if (!updatedAt || (state !== "stale" && state !== "unavailable")) return null;
  const at = new Date(updatedAt).toISOString();
  return (
    <p className="tabular mb-5 rounded-md border border-warn/30 bg-warn/[0.07] px-3 py-2 text-sm text-ink" role="alert">
      {state === "unavailable" && error ? error.message : "Refreshes are delayed: the last successful refresh is older than expected."} Showing the last known status from{" "}
      {clock(at)} ({ago(at, now)}). Retrying every {Math.round(intervalMs / 1000)}s.
    </p>
  );
}

export function StorageBadge({ storage }: { storage: StorageInfo | undefined }) {
  if (!storage) return null;
  const atlas = storage.atlas;
  return (
    <Badge tone={atlas ? "neutral" : "warn"} title={`${storage.label}: ${storage.location}`}>
      <Database className="h-3.5 w-3.5" />
      {atlas ? "MongoDB Atlas" : storage.backend === "mongodb" ? "MongoDB (self-hosted)" : "Local dev storage, not Atlas"}
    </Badge>
  );
}

export function WindowTabs({ value, onChange }: { value: Window; onChange: (w: Window) => void }) {
  const items: [Window, string][] = [["24h", "24 hours"], ["7d", "7 days"], ["30d", "30 days"]];
  return (
    <div className="inline-flex rounded-lg border border-line bg-surface p-1" role="tablist" aria-label="Time window">
      {items.map(([w, label]) => (
        <button
          key={w}
          role="tab"
          aria-selected={value === w}
          onClick={() => onChange(w)}
          className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${value === w ? "bg-hover text-ink shadow-sm" : "text-muted hover:text-ink"}`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

export function Stat({ label, value, sub, title }: { label: string; value: React.ReactNode; sub?: React.ReactNode; title?: string }) {
  return (
    <div className="min-w-0" title={title}>
      <p className="label">{label}</p>
      <p className="tabular mt-1.5 truncate text-[22px] font-semibold tracking-tight text-ink">{value}</p>
      {sub && <p className="mt-0.5 truncate text-xs text-muted">{sub}</p>}
    </div>
  );
}

const NAV = [
  { href: "/", label: "Status", Icon: LayoutDashboard },
  { href: "/incidents", label: "Incidents", Icon: Siren },
  { href: "/ai-agent", label: "Investigations", Icon: Search },
  ...(DEV ? [{ href: "/demo", label: "Development", Icon: FlaskConical }] : []),
];

function NavLinks() {
  const path = usePathname();
  return (
    <>
      {NAV.map(({ href, label, Icon }) => {
        const active = href === "/" ? path === "/" || path.startsWith("/services") : path.startsWith(href);
        return (
          <Link key={href} href={href} aria-current={active ? "page" : undefined}
            className={`flex items-center gap-2.5 whitespace-nowrap rounded-md px-2.5 py-1.5 text-sm transition ${
              active ? "bg-raised font-medium text-ink" : "text-muted hover:bg-raised/60 hover:text-ink"}`}>
            <Icon className="h-4 w-4" aria-hidden="true" /> {label}
          </Link>
        );
      })}
    </>
  );
}

export function Shell({ meta, updatedAt, error, intervalMs = POLL_MS, children }: {
  meta: Meta | null; updatedAt: number | null; error: ApiError | null; intervalMs?: number; children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen lg:pl-60">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 flex-col border-r border-line bg-surface lg:flex">
        <div className="flex h-14 items-center border-b border-line px-5"><Logo /></div>
        <nav className="flex flex-1 flex-col gap-0.5 p-3" aria-label="Main"><NavLinks /></nav>
        <div className="space-y-3 border-t border-line p-4">
          <StorageBadge storage={meta?.storage} />
          <LiveIndicator updatedAt={updatedAt} error={Boolean(error)} intervalMs={intervalMs} />
        </div>
      </aside>
      <header className="sticky top-0 z-30 border-b border-line bg-page lg:hidden">
        <div className="flex h-12 items-center gap-3 px-4">
          <Logo />
          <span className="ml-auto min-w-0 truncate"><LiveIndicator updatedAt={updatedAt} error={Boolean(error)} intervalMs={intervalMs} /></span>
        </div>
        <nav className="scroll-x flex gap-1 px-3 pb-2" aria-label="Main"><NavLinks /></nav>
      </header>
      <main className="mx-auto max-w-6xl px-4 pb-20 pt-6 sm:px-8">
        <StaleNotice updatedAt={updatedAt} error={error} intervalMs={intervalMs} />
        {children}
      </main>
    </div>
  );
}

const ERROR_TITLE: Record<ErrorKind, string> = {
  dashboard_offline: "Dashboard server not responding",
  api_unreachable: "Uptime API not reachable",
  not_found: "Not found",
  rejected: "Request rejected",
  api_error: "Uptime API error",
  bad_response: "Unexpected response from the uptime API",
};

export function ErrorPanel({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  return (
    <div className="panel mx-auto mt-16 max-w-md p-8 text-center" role="alert">
      <XCircle className="mx-auto h-8 w-8 text-crit" aria-hidden="true" />
      <p className="mt-4 font-semibold">{ERROR_TITLE[error.kind]}</p>
      <p className="mt-1 text-sm text-muted">{error.message}</p>
      {error.kind === "api_unreachable" && (
        <pre className="mt-5 rounded-lg bg-page p-3 text-left text-xs text-muted">cd admin-dashboard/backend{"\n"}.venv/bin/python -m uptime serve</pre>
      )}
      {onRetry && (
        <button onClick={onRetry} className="btn mt-4">
          Retry
        </button>
      )}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-lg bg-raised ${className}`} />;
}
