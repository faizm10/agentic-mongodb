"use client";

import Link from "next/link";
import { use, useState } from "react";
import { ArrowLeft, Bot, CheckCircle2, ChevronDown, ExternalLink, GitMerge, GitPullRequest, Workflow } from "lucide-react";
import { RouteBadge } from "@/components/incidents";
import { ErrorPanel, Shell, Skeleton, Stat, StatusIcon, StatusPill } from "@/components/ui";
import { type Incident, isDemoIncident, usePoll } from "@/lib/api";
import { FAILURE_LABELS, clock, duration, useNow } from "@/lib/format";
import { plainFailure } from "@/lib/plain";

const NEXT_STEP: Record<Incident["investigation_route"], string> = {
  investigate: "The app is running, but something it does is giving wrong results. The AI agent will look for the cause, test a fix, and send it for review.",
  service_health: "The app isn't responding at all. That usually needs someone to restart or redeploy it rather than a code fix.",
};

type Handoff = Record<string, unknown> & {
  probe_failures: { check: string; reason: string; source?: string | null; expected?: unknown; actual?: unknown }[];
  suspect_sources: string[];
  next_steps: string[];
  code_repair_candidate: boolean;
};

const AGENT_STATUS: Record<string, string> = {
  open: "AI agent is working on it",
  awaiting_merge: "Fix ready for review",
  needs_review: "AI agent needs a person to help",
  failed: "AI agent couldn't finish",
  resolved: "Fix merged",
  rejected: "Fix was turned down",
};

/** Which problem an agent run was for, in words: this outage, an earlier one, or a failed CI run. */
function agentRunLabel(runId: string, incidentId: string): string {
  if (runId.endsWith(`#${incidentId}`)) return "AI fix for this outage";
  if (/#UPT-/.test(runId)) return "AI fix for an earlier outage";
  return "AI fix for a failed automated test run";
}

const EVENT_LABEL: Record<string, string> = {
  opened: "Incident detected",
  escalated: "Escalated",
  deescalated: "De-escalated",
  failure_changed: "Failure changed",
  resolved: "Recovered",
  acknowledged: "Acknowledged",
  link_added: "Link added",
  fix_confirmed: "Fix confirmed",
  fix_disputed: "Still broken after the fix",
  fix_merged: "Fix merged into main",
};

/** Lets a person say the fix is merged into main; the running app gets it and is checked again. */
function MarkFixMerged({ incident, onChange }: { incident: Incident; onChange: () => void }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const merged = incident.events.some((e) => e.type === "fix_merged");

  async function markMerged() {
    setPending(true);
    setError("");
    try {
      const response = await fetch(`/api/v1/incidents/${encodeURIComponent(incident.id)}/fix-merged`, { method: "POST" });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setError(body?.error?.message ?? "Could not save this. Try again.");
        return;
      }
      onChange();
    } catch {
      setError("Could not reach the uptime API.");
    } finally {
      setPending(false);
    }
  }

  if (merged) {
    return (
      <p className="mt-4 flex items-start gap-2 rounded-lg border border-ok/30 bg-ok/10 p-3 text-sm text-ink" role="status">
        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-ok" aria-hidden="true" />
        Fixed and merged into main. The status turns green after the next check.
      </p>
    );
  }
  if (incident.state !== "open") return null;
  return (
    <div className="mt-4">
      <button type="button" disabled={pending} onClick={markMerged}
        className="inline-flex min-h-10 w-full items-center justify-center gap-1.5 rounded-lg border border-ok/40 bg-ok/10 px-3 py-2 text-xs font-medium text-ink transition hover:bg-ok/20 disabled:cursor-wait disabled:opacity-50">
        <GitMerge className="h-3.5 w-3.5" aria-hidden="true" /> {pending ? "Saving…" : "Mark as fixed & merged into main"}
      </button>
      {error && <p className="mt-2 text-xs text-crit" role="alert">{error}</p>}
    </div>
  );
}

export default function IncidentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const inc = usePoll<Incident>(`/api/v1/incidents/${encodeURIComponent(id)}`);
  const handoff = usePoll<Handoff>(inc.data && !isDemoIncident(inc.data) ? `/api/v1/incidents/${encodeURIComponent(id)}/evidence` : null);
  const [showJson, setShowJson] = useState(false);
  const now = useNow();
  const i = inc.data;
  const demo = i ? isDemoIncident(i) : false;
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
                <p className="mt-1 text-sm text-muted">{demo ? `Service status: ${i.state === "open" ? i.severity : "recovered"}.` : i.summary}</p>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <span className={`rounded-md px-2 py-0.5 text-xs font-semibold ${i.state === "open" ? "bg-crit/15 text-crit" : "bg-ok/10 text-ok"}`}>
                    {i.state === "open" ? "Ongoing" : "Resolved"}
                  </span>
                  <StatusPill status={i.peak_severity} size="sm" />
                  {!demo && <RouteBadge route={i.investigation_route} />}
                </div>
              </div>
            </div>
            <div className="mt-6 grid grid-cols-2 gap-5 border-t border-line pt-5 md:grid-cols-4">
              <Stat label="Started" value={clock(i.started_at)} sub={new Date(i.started_at).toLocaleDateString()} />
              <Stat label={i.state === "open" ? "Ongoing for" : "Duration"} value={duration(live)} sub={i.resolved_at ? `recovered ${clock(i.resolved_at)}` : "until recovery"} />
              <Stat label={demo ? "Status" : "Failure type"} value={<span className="text-base">{demo ? (i.state === "open" ? i.severity : "recovered") : FAILURE_LABELS[i.failure_type ?? ""] ?? "—"}</span>} sub={`${i.failed_check_count} failed checks`} />
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
                    <p className="mt-1 text-sm text-muted">{demo ? "Service status update recorded." : e.message}</p>
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
              {!demo && <section className="panel p-6">
                <h2 className="flex items-center gap-2 text-sm font-semibold"><Bot className="h-4 w-4 text-muted" /> What happens next</h2>
                <p className="mt-2 text-sm text-muted">{NEXT_STEP[i.investigation_route]}</p>
                {handoff.data && (
                  <details className="mt-4 text-xs">
                    <summary className="cursor-pointer font-medium text-muted hover:text-ink">Technical notes</summary>
                    {i.evidence.route_reason && <p className="mt-3 leading-relaxed text-faint">{i.evidence.route_reason}</p>}
                    <p className="mt-3">
                      Code-repair candidate:{" "}
                      <span className={handoff.data.code_repair_candidate ? "font-semibold text-warn" : "text-muted"}>
                        {handoff.data.code_repair_candidate ? "yes, reproduce first" : "no"}
                      </span>
                    </p>
                    <p className="label mt-4">Next steps</p>
                    <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-muted">
                      {handoff.data.next_steps.map((s) => <li key={s}>{s}</li>)}
                    </ul>
                  </details>
                )}
              </section>}

              <section className="panel p-6">
                <h2 className="flex items-center gap-2 text-sm font-semibold"><Workflow className="h-4 w-4 text-muted" /> Fixes</h2>
                {i.links.length === 0 && i.related_ci.length === 0 ? (
                  <p className="mt-3 text-sm text-muted">
                    No fix has been started yet. The AI agent usually picks up a new problem within a minute.
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
                      <li key={c.id} className="rounded-xl border border-line p-3 text-xs" title={`${c.id} · ${c.status} · ${c.stage}`}>
                        <p className="text-sm font-medium text-ink">{agentRunLabel(c.id, i.id)}</p>
                        <p className="mt-0.5 text-muted">{AGENT_STATUS[c.status] ?? c.status}</p>
                        <div className="mt-1.5 flex flex-wrap gap-3">
                          {c.pr_url && <a className="text-accent hover:underline" href={c.pr_url} target="_blank" rel="noreferrer">See the fix (PR)</a>}
                          {c.run_url && <a className="text-accent hover:underline" href={c.run_url} target="_blank" rel="noreferrer">Test run on GitHub</a>}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
                <MarkFixMerged incident={i} onChange={inc.reload} />
              </section>
            </div>
          </div>

          {!demo && <section className="panel p-6">
            <h2 className="text-sm font-semibold">What's wrong</h2>
            {handoff.data && handoff.data.probe_failures.length > 0 ? (
              <ul className="mt-4 space-y-3">
                {handoff.data.probe_failures.map((f) => {
                  const plain = plainFailure(f);
                  return (
                    <li key={f.check} className="rounded-xl border border-crit/25 bg-crit/5 p-4 text-sm">
                      <p className="font-medium text-ink">{plain.title}</p>
                      <p className="mt-1 text-muted">{plain.explanation}</p>
                      {plain.rows.length > 0 && (
                        <div className="scroll-x mt-3">
                          <table className="w-full min-w-[320px] text-left text-xs">
                            <thead className="text-faint">
                              <tr>
                                <th className="py-1 pr-4 font-medium">Example</th>
                                <th className="py-1 pr-4 font-medium">Should be</th>
                                <th className="py-1 font-medium">Actually was</th>
                              </tr>
                            </thead>
                            <tbody>
                              {plain.rows.map((row) => (
                                <tr key={row.label} className="border-t border-line/60">
                                  <td className="py-1.5 pr-4 text-muted">{row.label}</td>
                                  <td className="py-1.5 pr-4 text-ok">{row.expected}</td>
                                  <td className="py-1.5 font-medium text-crit">{row.actual}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="mt-3 text-sm text-muted">{i.summary}</p>
            )}
            <button onClick={() => setShowJson((v) => !v)} className="mt-5 inline-flex items-center gap-1.5 text-xs font-medium text-muted hover:text-ink" aria-expanded={showJson}>
              <ChevronDown className={`h-3.5 w-3.5 transition ${showJson ? "rotate-180" : ""}`} /> Technical details
            </button>
            {showJson && handoff.data && (
              <div className="mt-3 space-y-3">
                {handoff.data.probe_failures.map((f) => (
                  <div key={f.check} className="rounded-xl border border-line p-3 text-xs">
                    <p className="font-mono font-medium">{f.check}</p>
                    <p className="mt-1 text-muted">{f.reason}</p>
                    {f.source && <p className="mt-1 font-mono text-accent">{f.source}</p>}
                    <div className="scroll-x mt-2 font-mono text-[11.5px]">
                      <p className="whitespace-nowrap text-ok">expected {JSON.stringify(f.expected)}</p>
                      <p className="whitespace-nowrap text-crit">actual&nbsp;&nbsp; {JSON.stringify(f.actual)}</p>
                    </div>
                  </div>
                ))}
                <pre className="scroll-x max-h-[480px] overflow-y-auto rounded-xl border border-line bg-page p-4 font-mono text-[11.5px] leading-relaxed text-muted">
                  {JSON.stringify(handoff.data, null, 2)}
                </pre>
              </div>
            )}
          </section>}
        </div>
      )}
    </Shell>
  );
}
