import type { Tone } from "@/components/ui";
import type { Check, Incident, Status } from "./api";
import { STATUS_META } from "./format";

/** What each service's functional probe exercises. */
const FUNCTIONAL: Record<string, string> = { novamart: "Checkout", novaconnect: "Messaging" };
export const functionalLabel = (serviceId: string) => FUNCTIONAL[serviceId] ?? "Functional check";

export type Phase = { label: string; next: string; tone: Tone };

/** The repair run for this incident (not an earlier outage or a CI run). */
export function agentRun(i: Incident) {
  return i.related_ci.find((c) => c.id.endsWith(`#${i.id}`)) ?? null;
}

export function unsettled(i: Incident): number {
  return (i.recovery?.attempts ?? []).filter((a) => !a.final).length;
}

/** Incident progress in plain words, from the recorded incident, recovery and agent states. */
export function phase(i: Incident): Phase {
  if (i.state === "resolved") return { label: "Resolved", next: "No action needed", tone: "ok" };
  if (i.recovery_phase === "recovering") {
    if (i.recovery_status === "needs_review") {
      return { label: "Recovering interrupted checkouts · needs review", next: "Review the checkouts below that are missing evidence", tone: "warn" };
    }
    const left = unsettled(i);
    return { label: "Recovering interrupted checkouts", tone: "accent",
      next: i.recovery ? `The recovery worker is settling ${left} interrupted checkout${left === 1 ? "" : "s"}` : "Waiting for the recovery worker to pick this up" };
  }
  const run = agentRun(i);
  const merged = i.events.some((e) => e.type === "fix_merged");
  switch (run?.status) {
    case "awaiting_merge": return { label: "Awaiting PR review", next: "Review and merge the draft PR", tone: "warn" };
    case "awaiting_deployment": return { label: "Awaiting deployment", next: "Deploy the merged fix; a fresh check then confirms it", tone: "accent" };
    case "reported": return { label: "Proposed fix ready", next: "Review the verified patch (draft PRs are off for this repository)", tone: "warn" };
    case "needs_review":
    case "failed": return { label: "Needs an engineer", next: "Read the investigation and decide the next step", tone: "crit" };
    case "rejected": return { label: "PR closed without merge", next: "Fix it by hand or start a new investigation", tone: "crit" };
    case "resolved": return { label: "Awaiting a passing check", next: "The merged fix is waiting for a fresh check to pass", tone: "accent" };
    case "open": return { label: "Investigating", next: "Investigation in progress", tone: "accent" };
  }
  if (merged) return { label: "Awaiting deployment", next: "Waiting for a fresh check to pass on the merged fix", tone: "accent" };
  if (i.investigation_route === "service_health") {
    return { label: "Service unreachable", next: "Check the process, deployment and network; this is not a code fix", tone: "crit" };
  }
  return { label: "Awaiting investigation", next: "An investigation usually starts within a minute", tone: "neutral" };
}

/** "Checkout operational · 1 interrupted checkout to recover" */
export function healthAndProgress(i: Incident): { health: string; progress: string } {
  const source = i.evidence.source === "betterstack" ? "Better Stack monitor"
    : i.evidence.source === "dependabot" ? "Dependencies" : functionalLabel(i.service_id);
  const health = `${source} ${STATUS_META[i.service_health].label.toLowerCase()}`;
  const left = unsettled(i);
  const progress = i.state === "resolved" ? "Incident resolved"
    : i.recovery_phase === "recovering" ? (i.recovery ? `${left} interrupted checkout${left === 1 ? "" : "s"} to recover` : "Interrupted checkouts not collected yet")
    : phase(i).label;
  return { health, progress };
}

export type Fact = { label: string; status: Status; detail?: string };

/** The separate layers of the latest check: website, API and the functional probe. */
export function checkFacts(serviceId: string, c: Check | null | undefined): Fact[] {
  if (!c) return [];
  const facts: Fact[] = [];
  if (c.frontend) facts.push({ label: c.frontend.ok ? "Website reachable" : "Website unreachable", status: c.frontend.ok ? "operational" : "down" });
  if (c.availability) facts.push({ label: c.availability.ok ? "API reachable" : "API unreachable", status: c.availability.ok ? "operational" : "down" });
  const name = functionalLabel(serviceId);
  if (c.functional) {
    const failed = c.functional.details?.failed_checks ?? [];
    facts.push({ label: c.functional.ok ? `${name} passing` : `${name} failing`, status: c.functional.ok ? "operational" : "degraded",
      detail: c.functional.ok ? undefined : failed.length ? failed.join(", ") : c.functional.error ?? undefined });
  } else if (c.availability && !c.availability.ok) {
    facts.push({ label: `${name} not checked`, status: "unknown" });
  }
  return facts;
}
