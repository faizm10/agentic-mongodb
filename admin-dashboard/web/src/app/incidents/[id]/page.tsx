"use client";

import Link from "next/link";
import { use, useState } from "react";
import { ArrowLeft, Bot, ChevronDown, ExternalLink, GitPullRequest, Workflow } from "lucide-react";
import { RouteBadge } from "@/components/incidents";
import { ErrorPanel, Shell, Skeleton, Stat, StatusIcon, StatusPill } from "@/components/ui";
import { type Incident, usePoll } from "@/lib/api";
import { FAILURE_LABELS, ROUTE_LABELS, clock, duration, useNow } from "@/lib/format";

type Handoff = Record<string, unknown> & {
  probe_failures: { check: string; reason: string; source?: string | null; expected?: unknown; actual?: unknown }[];
  suspect_sources: string[];
  next_steps: string[];
  code_repair_candidate: boolean;
};

const EVENT_LABEL: Record<string, string> = {
  opened: "Incident detected",
  escalated: "Escalated",
  deescalated: "De-escalated",
  failure_changed: "Failure changed",
  resolved: "Recovered",
  acknowledged: "Acknowledged",
  link_added: "Link added",
};

export default function IncidentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const inc = usePoll<Incident>(`/api/v1/incidents/${encodeURIComponent(id)}`);
  const handoff = usePoll<Handoff>(`/api/v1/incidents/${encodeURIComponent(id)}/evidence`);
  const [showJson, setShowJson] = useState(false);
  const now = useNow();
  const i = inc.data;
  const live = i ? (i.state === "open" ? (now - new Date(i.started_at).getTime()) / 1000 : i.duration_seconds) : 0;

  return (
    <Shell meta={inc.meta} updatedAt={inc.updatedAt} error={Boolean(inc.error)}>
      <Link href={i ? `/services/${i.service_id}` : "/"} className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink">
        <ArrowLeft className="h-4 w-4" /> {i ? i.service_name : "Back"}
      </Link>
      {!i && inc.error ? (
        <ErrorPanel message={inc.error.message} onRetry={inc.reload} />
      ) : !i ? (
        <Skeleton className="mt-6 h-48" />
      ) : (
        <div className="mt-5 space-y-6">
          <section className="panel p-6 sm:p-7">
            <div className="flex flex-wrap items-start gap-4">
              <StatusIcon status={i.state === "open" ? i.severity : "operational"} className="h-8 w-8" />
              <div className="min-w-0 flex-1">
                <p className="font-mono text-xs text-faint">{i.id}</p>
                <h1 className="mt-1 text-2xl font-semibold tracking-tight">{i.title}</h1>
                <p className="mt-1 text-sm text-muted">{i.summary}</p>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <span className={`rounded-md px-2 py-0.5 text-xs font-semibold ${i.state === "open" ? "bg-crit/15 text-crit" : "bg-ok/10 text-ok"}`}>
                    {i.state === "open" ? "Ongoing" : "Resolved"}
                  </span>
                  <StatusPill status={i.peak_severity} size="sm" />
                  <RouteBadge route={i.investigation_route} />
                </div>
              </div>
            </div>
            <div className="mt-6 grid grid-cols-2 gap-5 border-t border-line pt-5 md:grid-cols-4">
              <Stat label="Started" value={clock(i.started_at)} sub={new Date(i.started_at).toLocaleDateString()} />
              <Stat label={i.state === "open" ? "Ongoing for" : "Duration"} value={duration(live)} sub={i.resolved_at ? `recovered ${clock(i.resolved_at)}` : "until recovery"} />
              <Stat label="Failure type" value={<span className="text-base">{FAILURE_LABELS[i.failure_type ?? ""] ?? "—"}</span>} sub={`${i.failed_check_count} failed checks`} />
              <Stat label="Service" value={<span className="text-base">{i.service_name}</span>} sub={`agent app: ${i.app}`} />
            </div>
          </section>

          <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
            <section className="panel p-6">
              <h2 className="text-sm font-semibold">Timeline</h2>
              <ol className="mt-5 space-y-5 border-l border-line pl-5">
                {i.events.map((e, n) => (
                  <li key={n} className="relative">
                    <span className="absolute -left-[27px] top-0.5 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-surface">
                      <StatusIcon status={e.status ?? "unknown"} className="h-3.5 w-3.5" />
                    </span>
                    <p className="text-sm font-medium">{EVENT_LABEL[e.type] ?? e.type}</p>
                    <p className="text-xs text-faint">{clock(e.at, true)}</p>
                    <p className="mt-1 text-sm text-muted">{e.message}</p>
                    {e.check_id && <p className="mt-1 font-mono text-[11px] text-faint">check {e.check_id}</p>}
                  </li>
                ))}
                {i.state === "open" && (
                  <li className="relative">
                    <span className="absolute -left-[25px] top-1.5 h-2.5 w-2.5 animate-pulse rounded-full bg-crit" />
                    <p className="text-sm text-muted">Still failing · last failure {clock(i.last_failure_at, true)}</p>
                  </li>
                )}
              </ol>
            </section>

            <div className="space-y-6">
              <section className="panel p-6">
                <h2 className="flex items-center gap-2 text-sm font-semibold"><Bot className="h-4 w-4 text-muted" /> AI investigation hand-off</h2>
                <p className="mt-2 text-sm text-muted">{ROUTE_LABELS[i.investigation_route].hint}</p>
                {i.evidence.route_reason && <p className="mt-2 text-xs leading-relaxed text-faint">{i.evidence.route_reason}</p>}
                {handoff.data && (
                  <>
                    <p className="mt-4 text-xs">
                      Code-repair candidate:{" "}
                      <span className={handoff.data.code_repair_candidate ? "font-semibold text-warn" : "text-muted"}>
                        {handoff.data.code_repair_candidate ? "yes, reproduce first" : "no"}
                      </span>
                    </p>
                    <p className="label mt-4">Next steps</p>
                    <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-muted">
                      {handoff.data.next_steps.map((s) => <li key={s}>{s}</li>)}
                    </ul>
                  </>
                )}
              </section>

              <section className="panel p-6">
                <h2 className="flex items-center gap-2 text-sm font-semibold"><Workflow className="h-4 w-4 text-muted" /> CI & repair links</h2>
                {i.links.length === 0 && i.related_ci.length === 0 ? (
                  <p className="mt-3 text-sm text-muted">
                    No GitHub Actions run or repair PR is linked yet. The agents can attach one via the admin API.
                  </p>
                ) : (
                  <ul className="mt-3 space-y-2 text-sm">
                    {i.links.map((l) => (
                      <li key={l.url}>
                        <a href={l.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-accent hover:underline">
                          {l.kind === "repair_pr" ? <GitPullRequest className="h-3.5 w-3.5" /> : <ExternalLink className="h-3.5 w-3.5" />}
                          {l.label || l.url}
                        </a>
                      </li>
                    ))}
                    {i.related_ci.map((c) => (
                      <li key={c.id} className="rounded-xl border border-line p-3 text-xs">
                        <p className="font-mono text-faint">{c.id} · {c.status} · {c.stage}</p>
                        <div className="mt-1 flex flex-wrap gap-3">
                          {c.run_url && <a className="text-accent hover:underline" href={c.run_url} target="_blank" rel="noreferrer">GitHub Actions run</a>}
                          {c.pr_url && <a className="text-accent hover:underline" href={c.pr_url} target="_blank" rel="noreferrer">Repair PR</a>}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>
          </div>

          <section className="panel p-6">
            <h2 className="text-sm font-semibold">Evidence</h2>
            {handoff.data && handoff.data.probe_failures.length > 0 ? (
              <ul className="mt-4 space-y-3">
                {handoff.data.probe_failures.map((f) => (
                  <li key={f.check} className="rounded-xl border border-crit/25 bg-crit/5 p-4 text-sm">
                    <p className="font-mono text-[13px] font-medium">{f.check}</p>
                    <p className="mt-1 text-muted">{f.reason}</p>
                    {f.source && <p className="mt-1.5 font-mono text-xs text-accent">{f.source}</p>}
                    <div className="scroll-x mt-2 font-mono text-[11.5px]">
                      <p className="whitespace-nowrap text-ok">expected {JSON.stringify(f.expected)}</p>
                      <p className="whitespace-nowrap text-crit">actual&nbsp;&nbsp; {JSON.stringify(f.actual)}</p>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-3 text-sm text-muted">{i.summary}</p>
            )}
            <button onClick={() => setShowJson((v) => !v)} className="mt-5 inline-flex items-center gap-1.5 text-xs font-medium text-muted hover:text-ink" aria-expanded={showJson}>
              <ChevronDown className={`h-3.5 w-3.5 transition ${showJson ? "rotate-180" : ""}`} /> Structured hand-off payload (opsrelay.uptime_incident.v1)
            </button>
            {showJson && handoff.data && (
              <pre className="scroll-x mt-3 max-h-[480px] overflow-y-auto rounded-xl border border-line bg-page p-4 font-mono text-[11.5px] leading-relaxed text-muted">
                {JSON.stringify(handoff.data, null, 2)}
              </pre>
            )}
          </section>
        </div>
      )}
    </Shell>
  );
}
