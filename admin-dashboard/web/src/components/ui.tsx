"use client";

import Link from "next/link";
import { AlertTriangle, CheckCircle2, Database, HelpCircle, XCircle } from "lucide-react";
import type { Meta, Status, StorageInfo, Window } from "@/lib/api";
import { STATUS_META, ago, useNow } from "@/lib/format";

export function StatusIcon({ status, className = "h-4 w-4" }: { status: Status; className?: string }) {
  const Icon = { operational: CheckCircle2, degraded: AlertTriangle, down: XCircle, unknown: HelpCircle }[status];
  return <Icon className={`flex-none ${className}`} style={{ color: STATUS_META[status].color }} aria-hidden="true" />;
}

export function StatusPill({ status, size = "md" }: { status: Status; size?: "sm" | "md" }) {
  const meta = STATUS_META[status];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border font-medium ${meta.soft} ${
        size === "sm" ? "px-2 py-0.5 text-[11.5px]" : "px-2.5 py-1 text-xs"
      }`}
    >
      <StatusIcon status={status} className={size === "sm" ? "h-3.5 w-3.5" : "h-3.5 w-3.5"} />
      <span className="text-ink">{meta.label}</span>
    </span>
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
      <span className="text-[15px] font-semibold tracking-tight">
        OpsRelay <span className="font-normal text-muted">Status</span>
      </span>
    </Link>
  );
}

export function LiveIndicator({ updatedAt, error }: { updatedAt: number | null; error: boolean }) {
  const now = useNow();
  const live = !error && updatedAt !== null;
  return (
    <span className="inline-flex items-center gap-2 text-xs text-muted" role="status">
      <span className="relative flex h-2 w-2">
        {live && <span className="absolute inline-flex h-full w-full animate-ping2 rounded-full bg-ok/60" />}
        <span className={`relative inline-flex h-2 w-2 rounded-full ${live ? "bg-ok" : "bg-crit"}`} />
      </span>
      {error ? "Monitoring API unreachable, retrying" : updatedAt ? `Live · updated ${ago(new Date(updatedAt).toISOString(), now)}` : "Connecting…"}
    </span>
  );
}

export function StorageBadge({ storage }: { storage: StorageInfo | undefined }) {
  if (!storage) return null;
  const atlas = storage.atlas;
  return (
    <span
      title={`${storage.label}: ${storage.location}`}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11.5px] font-medium ${
        atlas ? "border-ok/30 bg-ok/10 text-ink" : "border-warn/30 bg-warn/10 text-warn"
      }`}
    >
      <Database className="h-3.5 w-3.5" />
      {atlas ? "MongoDB Atlas" : storage.backend === "mongodb" ? "MongoDB (self-hosted)" : "Local dev storage, not Atlas"}
    </span>
  );
}

export function WindowTabs({ value, onChange }: { value: Window; onChange: (w: Window) => void }) {
  const items: [Window, string][] = [["24h", "24 hours"], ["7d", "7 days"], ["30d", "30 days"]];
  return (
    <div className="inline-flex rounded-xl border border-line bg-surface p-1" role="tablist" aria-label="Time window">
      {items.map(([w, label]) => (
        <button
          key={w}
          role="tab"
          aria-selected={value === w}
          onClick={() => onChange(w)}
          className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${value === w ? "bg-hover text-ink shadow-sm" : "text-muted hover:text-ink"}`}
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

export function Shell({ meta, updatedAt, error, children }: { meta: Meta | null; updatedAt: number | null; error: boolean; children: React.ReactNode }) {
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-line bg-page/80 backdrop-blur-xl">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-4 px-4 sm:px-6">
          <Logo />
          <div className="ml-auto flex items-center gap-3">
            <span className="hidden sm:inline-flex">
              <StorageBadge storage={meta?.storage} />
            </span>
            <LiveIndicator updatedAt={updatedAt} error={error} />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 pb-20 pt-8 sm:px-6">{children}</main>
      <footer className="mx-auto max-w-6xl px-4 pb-10 text-xs text-faint sm:px-6">
        Uptime is computed from recorded checks only; periods without samples are shown as no data, never as uptime.
      </footer>
    </div>
  );
}

export function ErrorPanel({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="panel mx-auto mt-16 max-w-md p-8 text-center">
      <XCircle className="mx-auto h-8 w-8 text-crit" />
      <p className="mt-4 font-semibold">Can&apos;t load monitoring data</p>
      <p className="mt-1 text-sm text-muted">{message}</p>
      <pre className="mt-5 rounded-xl bg-page p-3 text-left text-xs text-muted">cd admin-dashboard/backend{"\n"}.venv/bin/python -m uptime serve</pre>
      {onRetry && (
        <button onClick={onRetry} className="mt-4 rounded-lg border border-line-strong px-4 py-2 text-sm font-medium hover:bg-hover">
          Retry
        </button>
      )}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-xl bg-raised ${className}`} />;
}
