import type { Summary } from "./api";

/** `detail` is for the checklist; `nudge` is the mascot's first-person version. */
export type SetupStep = { key: string; title: string; detail: string; nudge: string; label: string; href: string; done: boolean };

/** Workspace setup in order, worked out from the summary the dashboard already polls. */
export function setupSteps(s: Summary): SetupStep[] {
  return [
    { key: "site", title: "Add a site to monitor", detail: "OpsRelay checks it every few minutes and opens an incident when it fails.",
      nudge: "Add a site so I can start checking it.", label: "Add a site", href: "/sites", done: s.services.length > 0 },
    { key: "github", title: "Connect GitHub", detail: "Lets OpsRelay see the repositories your services run from.",
      nudge: "Connect GitHub so I know which repositories your services run.", label: "Connect GitHub", href: "/connectors",
      done: s.setup.github_connected },
    { key: "repo", title: "Map a repository to a service", detail: "Incidents then point at the right code.",
      nudge: "Map a repository to a service so incidents point at the right code.", label: "Add a repository", href: "/repositories",
      done: s.repositories.length > 0 },
    { key: "betterstack", title: "Connect Better Stack", detail: "Its alerts open incidents here.",
      nudge: "Connect Better Stack so its alerts open incidents here.", label: "Connect Better Stack", href: "/connectors",
      done: s.setup.betterstack_configured },
    { key: "agent", title: "Turn on the repair agent", detail: "It investigates incidents and starts in report-only mode.",
      nudge: "Turn on the repair agent for a repository. It starts in report-only mode.", label: "Enable the agent", href: "/repositories",
      done: s.repositories.some((r) => r.agent.enabled) },
    { key: "notifications", title: "Get alerted", detail: "Send incidents to Slack, Discord, PagerDuty or a webhook.",
      nudge: "Tell me where to send alerts: Slack, Discord, PagerDuty or a webhook.", label: "Set up alerts", href: "/connectors",
      done: s.setup.notifications_configured },
  ];
}
