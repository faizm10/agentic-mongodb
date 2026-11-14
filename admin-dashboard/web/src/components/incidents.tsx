"use client";

import Link from "next/link";
import { ArrowUpRight, CheckCircle2, GitPullRequest, Siren } from "lucide-react";
import type { Incident } from "@/lib/api";
import { FAILURE_LABELS, ROUTE_LABELS, clock, duration, useNow } from "@/lib/format";
import { StatusIcon } from "./ui";

export function RouteBadge({ route }: { route: Incident["investigation_route"] }) {
  const r = ROUTE_LABELS[route];
  return (
    <span title={r.hint} className="inline-flex items-center rounded-md border border-line-strong bg-raised px-1.5 py-0.5 text-[11px] font-medium text-muted">
      {r.label}
    </span>
  );
}

export function IncidentRow({ incident }: { incident: Incident }) {
  const now = useNow();
  const open = incident.state === "open";
  const live = open ? (now - new Date(incident.started_at).getTime()) / 1000 : incident.duration_seconds;
  const pr = incident.links.find((l) => l.kind === "repair_pr");
  return (
    <li className="relative pl-7">
      <span className="absolute left-0 top-1 flex h-4 w-4 items-center justify-center">
        {open ? <StatusIcon status={incident.severity} className="h-4 w-4" /> : <CheckCircle2 className="h-4 w-4 text-ok" />}
      </span>
      <Link href={`/incidents/${encodeURIComponent(incident.id)}`} className="group block rounded-xl p-3 -m-3 transition hover:bg-hover">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-medium text-ink">{incident.title}</span>
          <span className={`rounded-md px-1.5 py-0.5 text-[11px] font-semibold ${open ? "bg-crit/15 text-crit" : "bg-ok/10 text-ok"}`}>
            {open ? "Ongoing" : "Resolved"}
          </span>
          <RouteBadge route={incident.investigation_route} />
          <ArrowUpRight className="ml-auto h-4 w-4 text-faint opacity-0 transition group-hover:opacity-100" />
        </div>
        <p className="mt-1 line-clamp-2 text-sm text-muted">{incident.summary}</p>
        <p className="tabular mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-faint">
          <span>{FAILURE_LABELS[incident.failure_type ?? ""] ?? incident.failure_type}</span>
          <span>Started {clock(incident.started_at, true)}</span>
          {incident.resolved_at && <span>Recovered {clock(incident.resolved_at, true)}</span>}
          <span className={open ? "font-semibold text-ink" : ""}>{open ? `Ongoing for ${duration(live)}` : `Lasted ${duration(live)}`}</span>
          <span>{incident.failed_check_count} failed check{incident.failed_check_count === 1 ? "" : "s"}</span>
          {pr && (
            <span className="inline-flex items-center gap-1 text-accent">
              <GitPullRequest className="h-3 w-3" /> Repair PR
            </span>
          )}
          <span className="font-mono text-faint">{incident.id}</span>
        </p>
      </Link>
    </li>
  );
}

export function IncidentTimeline({ active, recent }: { active: Incident[]; recent: Incident[] }) {
  return (
    <section className="panel p-5 sm:p-6" aria-labelledby="incidents-title">
      <div className="flex items-center gap-2">
        <Siren className="h-4 w-4 text-muted" />
        <h2 id="incidents-title" className="text-sm font-semibold">Incidents</h2>
        <span className="ml-auto text-xs text-faint">Detected by the uptime monitor</span>
      </div>
      {active.length === 0 && recent.length === 0 ? (
        <div className="mt-6 flex items-center gap-3 rounded-xl border border-dashed border-line-strong p-5 text-sm text-muted">
          <CheckCircle2 className="h-5 w-5 text-ok" /> No incidents recorded yet.
        </div>
      ) : (
        <>
          {active.length > 0 && (
            <>
              <p className="label mt-5">Active</p>
              <ul className="mt-3 space-y-5 border-l border-line pl-0">{active.map((i) => <IncidentRow key={i.id} incident={i} />)}</ul>
            </>
          )}
          {recent.length > 0 && (
            <>
              <p className="label mt-6">Recently resolved</p>
              <ul className="mt-3 space-y-5">{recent.map((i) => <IncidentRow key={i.id} incident={i} />)}</ul>
            </>
          )}
        </>
      )}
    </section>
  );
}
