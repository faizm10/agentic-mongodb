import type { Summary } from "./api";

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
  const setup = (key: string, text: string, label: string, href = "/repositories"): MascotMessage =>
    ({ key: `${ws}:${key}`, tone: "accent", text, links: [{ href, label }] });
  if (!s.services.length) {
    return { key: `${ws}:site`, tone: "accent", text: "Add a site so I can start checking it.", links: [{ href: "/sites", label: "Add a site" }] };
  }
  if (!s.setup.github_connected) return setup("github", "Connect GitHub so I know which repositories your services run.", "Connect GitHub", "/connectors");
  if (!s.repositories.length) return setup("repo", "Map a repository to a service so incidents point at the right code.", "Add a repository");
  if (!s.setup.betterstack_configured) return setup("betterstack", "Connect Better Stack so its alerts open incidents here.", "Connect Better Stack", "/connectors");
  if (!s.repositories.some((r) => r.agent.enabled)) {
    return setup("agent", "Turn on the repair agent for a repository. It starts in report-only mode.", "Enable the agent");
  }
  return { key: `${ws}:quiet`, tone: "ok", text: "All quiet. No open incidents in this workspace.", links: [] };
}
