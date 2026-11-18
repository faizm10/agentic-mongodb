import type { Summary } from "./api";
import { setupSteps } from "./setup";

export type MascotTone = "ok" | "warn" | "crit" | "accent";
export type MascotLink = { href: string; label: string; external?: boolean };
/** `key` names the situation, so a poll that finds the same situation doesn't reopen a closed bubble. */
export type MascotMessage = { key: string; tone: MascotTone; text: string; links: MascotLink[] };

const incidentHref = (id: string) => `/incidents/${encodeURIComponent(id)}`;

/** The most useful next step for this workspace, from the summary the dashboard already polls. */
export function mascotMessage(s: Summary): MascotMessage {
  const ws = s.setup.workspace_id;
  const incident = s.active_incidents[0];
  if (incident) {
    const run = s.agent_activity.find((a) => a.incident_id === incident.id);
    const more = s.active_incidents.length > 1 ? ` (${s.active_incidents.length - 1} more open)` : "";
    if (run?.pr_url) {
      return {
        key: `${ws}:pr:${incident.id}`, tone: "accent",
        text: `A draft fix for ${incident.service_name} is ready for your review${more}.`,
        links: [{ href: run.pr_url, label: "Review the draft PR", external: true }, { href: incidentHref(incident.id), label: "Open incident" }],
      };
    }
    return {
      key: `${ws}:incident:${incident.id}`, tone: incident.severity === "down" ? "crit" : "warn",
      text: `${incident.title}${more}.`,
      links: [{ href: incidentHref(incident.id), label: "Open incident" }],
    };
  }
  const next = setupSteps(s).find((step) => !step.done);
  if (next) return { key: `${ws}:${next.key}`, tone: "accent", text: next.nudge, links: [{ href: next.href, label: next.label }] };
  return { key: `${ws}:quiet`, tone: "ok", text: "All quiet. No open incidents.", links: [] };
}
