"use client";

import Link from "next/link";
import { useState } from "react";
import { Activity, ChevronRight, Clock3, Cpu, Database, Timer } from "lucide-react";
import { IncidentTimeline } from "@/components/incidents";
import { StatusLegend, UptimeBars } from "@/components/charts";
import { UptimeFigure, uptimeBasis } from "@/components/uptime";
import { ErrorPanel, PageHeader, Shell, Skeleton, StatusIcon, StatusPill, WindowTabs } from "@/components/ui";
import { type ServiceOverview, type Summary, type Window, usePoll } from "@/lib/api";
import { STATUS_META, ago, duration, ms, useNow } from "@/lib/format";

const BANNER: Record<string, string> = {
  operational: "border-ok/25 bg-ok/[0.06]",
  degraded: "border-warn/30 bg-warn/[0.06]",
  down: "border-crit/35 bg-crit/[0.08]",
  unknown: "border-line-strong bg-surface",
};

function Kpi({ href, label, value, sub }: { href?: string; label: string; value: React.ReactNode; sub: string }) {
  const body = (
    <>
      <p className="label">{label}</p>
      <p className="tabular mt-1.5 text-2xl font-semibold tracking-tight">{value}</p>
      <p className="mt-0.5 truncate text-xs text-muted">{sub}</p>
    </>
  );
  return href ? <Link href={href} className="panel block p-4 transition hover:border-line-strong">{body}</Link> : <div className="panel p-4">{body}</div>;
}

function ServiceCard({ s, window }: { s: ServiceOverview; window: Window }) {
  const now = useNow();
  const up = s.uptime;
  return (
    <article className="panel animate-rise p-5 sm:p-6">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2.5">
            <StatusIcon status={s.current.status} className="h-5 w-5" />
            <h3 className="text-base font-semibold tracking-tight">{s.name}</h3>
            <StatusPill status={s.current.status} />
          </div>
          <p className="mt-1 text-sm text-muted">{s.description}</p>
        </div>
        <Link href={`/services/${s.id}`} className="btn">
          Details <ChevronRight className="h-3.5 w-3.5" />
        </Link>
      </div>

      <div className="mt-5">
        {s.bars && <UptimeBars bars={s.bars} window={window} label={`${s.name} uptime, last ${window}`} />}
      </div>

      <dl className="tabular mt-5 grid grid-cols-2 gap-4 border-t border-line pt-4 sm:grid-cols-4">
        <div>
          <dt className="label">Uptime · {window}</dt>
          <dd className="mt-1">
            <UptimeFigure stats={up} />
          </dd>
          <dd className="mt-0.5 text-[11px] text-faint">{uptimeBasis(up)}</dd>
        </div>
        <div>
          <dt className="label">Avg latency</dt>
          <dd className="mt-1 text-lg font-semibold">{ms(up.avg_latency_ms)}</dd>
          <dd className="mt-0.5 text-[11px] text-faint">availability endpoint</dd>
        </div>
        <div>
          <dt className="label">Last check</dt>
          <dd className="mt-1 text-lg font-semibold">{ago(s.current.last_checked_at, now)}</dd>
          <dd className="mt-0.5 text-[11px] text-faint">{up.check_count} checks in window</dd>
        </div>
        <div>
          <dt className="label">Last success</dt>
          <dd className="mt-1 text-lg font-semibold">{ago(s.current.last_success_at, now)}</dd>
          <dd className="mt-0.5 text-[11px] text-faint">
            {s.current.since ? `${STATUS_META[s.current.status].label} for ${duration((now - new Date(s.current.since).getTime()) / 1000)}` : "—"}
          </dd>
        </div>
      </dl>
    </article>
  );
}

function MonitoringPanel({ m }: { m: Summary["monitoring"] }) {
  const now = useNow();
  const w = m.worker;
  const rows: [React.ComponentType<{ className?: string }>, string, string][] = [
    [Activity, "Worker", m.status === "running" ? "Running" : m.status === "stale" ? "Stale" : "Not started"],
    [Timer, "Check interval", `every ${m.interval_seconds}s`],
    [Clock3, "Last cycle", w?.last_cycle_started_at ? `${ago(w.last_cycle_started_at, now)} · ${ms(w.last_cycle_duration_ms)}` : "—"],
    [Cpu, "Cycles · checks", `${w?.cycles_completed ?? 0} · ${m.total_checks.toLocaleString()}`],
    [Database, "Storage", m.storage.label],
  ];
  return (
    <section className="panel p-5 sm:p-6" aria-labelledby="monitoring-title">
      <div className="flex items-center gap-2">
        <span className={`h-2 w-2 rounded-full ${m.status === "running" ? "bg-ok" : "bg-warn"}`} />
        <h2 id="monitoring-title" className="text-sm font-semibold">Monitoring health</h2>
      </div>
      <p className="mt-1 text-xs text-muted">{m.message}</p>
      <dl className="mt-4 space-y-3 text-sm">
        {rows.map(([Icon, k, v]) => (
          <div key={k} className="flex items-start gap-3">
            <Icon className="mt-0.5 h-4 w-4 flex-none text-faint" />
            <dt className="w-28 flex-none text-muted">{k}</dt>
            <dd className="tabular min-w-0 text-ink">{v}</dd>
          </div>
        ))}
      </dl>
      {!m.storage.atlas && (
        <p className="mt-4 rounded-lg border border-warn/25 bg-warn/5 p-3 text-xs leading-relaxed text-warn">
          History is stored locally for development ({m.storage.backend}). Set MONGODB_URI to persist it in MongoDB Atlas.
        </p>
      )}
      <p className="mt-4 truncate font-mono text-[11px] text-faint" title={w?.owner ?? ""}>
        lease: {m.lease_held ? w?.owner : "not held"}
      </p>
    </section>
  );
}

export default function Overview() {
  const [window, setWindow] = useState<Window>("24h");
  const { data, meta, error, updatedAt, reload } = usePoll<Summary>(`/api/v1/summary?window=${window}`);
  const now = useNow();

  return (
    <Shell meta={meta} updatedAt={updatedAt} error={Boolean(error)}>
      {!data && error ? (
        <ErrorPanel message={error.message} onRetry={reload} />
      ) : !data ? (
        <div className="space-y-6">
          <Skeleton className="h-28" />
          <Skeleton className="h-56" />
          <Skeleton className="h-56" />
        </div>
      ) : (
        <div className="space-y-8">
          <PageHeader title="Overview" sub={`${data.services.length} services monitored · checked every ${data.monitoring.interval_seconds}s`} />
          <section className={`rounded-xl border p-5 sm:p-6 ${BANNER[data.overall.status]}`} aria-live="polite">
            <div className="flex flex-wrap items-center gap-4">
              <StatusIcon status={data.overall.status} className="h-8 w-8" />
              <div className="min-w-0 flex-1">
                <p className="text-lg font-semibold tracking-tight">{data.overall.label}</p>
                <p className="mt-0.5 text-sm text-muted">
                  {data.overall.affected_services.length ? `Affected: ${data.overall.affected_services.join(", ")}` : "All checks passing"}
                  {data.monitoring.worker?.last_cycle_started_at && ` · last check ${ago(data.monitoring.worker.last_cycle_started_at, now)}`}
                </p>
              </div>
            </div>
          </section>

          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Kpi href="/incidents" label="Active incidents" value={data.active_incidents.length}
              sub={data.active_incidents.some((i) => i.recovery_phase) ? "Recovering interrupted checkouts" : `${data.recent_incidents.length} recently resolved`} />
            <Kpi href="/ai-agent" label="AI agent" value={data.agent_activity.length}
              sub={`${data.agent_activity.filter((a) => a.state === "working" || a.state === "waiting").length} in progress`} />
            <Kpi label="Services operational"
              value={`${data.services.filter((x) => x.current.status === "operational").length}/${data.services.length}`} sub="right now" />
            <Kpi label="Checks recorded" value={data.monitoring.total_checks.toLocaleString()}
              sub={data.monitoring.status === "running" ? "Monitor running" : "Monitor not running"} />
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-sm font-semibold">Services</h2>
            <div className="flex flex-wrap items-center gap-4">
              <span className="hidden md:block">
                <StatusLegend />
              </span>
              <WindowTabs value={window} onChange={setWindow} />
            </div>
          </div>

          <div className="space-y-5">
            {data.services.map((s) => (
              <ServiceCard key={s.id} s={s} window={data.window} />
            ))}
          </div>

          <div className="grid items-start gap-5 lg:grid-cols-[1fr_340px]">
            <IncidentTimeline active={data.active_incidents} recent={data.recent_incidents} />
            <MonitoringPanel m={data.monitoring} />
          </div>
        </div>
      )}
    </Shell>
  );
}
