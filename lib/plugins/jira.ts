// Atlassian Jira Cloud via REST v3 with an API token (basic auth).
import { envOf, type IncidentNotice, type Plugin } from "./types";

const base = () => envOf("JIRA_BASE_URL").replace(/\/$/, "");
const auth = () => `Basic ${Buffer.from(`${envOf("JIRA_EMAIL")}:${envOf("JIRA_API_TOKEN")}`).toString("base64")}`;
const label = (incidentId: string) => `opsrelay-${incidentId}`.toLowerCase();

async function jira(method: string, path: string, body?: unknown) {
  const r = await fetch(`${base()}${path}`, {
    method,
    headers: { authorization: auth(), accept: "application/json", "content-type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(10000),
  });
  const text = await r.text();
  if (!r.ok) throw new Error(`Jira ${method} ${path} → ${r.status}: ${text.slice(0, 160)}`);
  return text ? JSON.parse(text) : {};
}

const adf = (text: string) => ({ type: "doc", version: 1, content: [{ type: "paragraph", content: [{ type: "text", text }] }] });

async function findIssue(incidentId: string): Promise<string | null> {
  const r = await jira("POST", "/rest/api/3/search/jql", { jql: `labels = "${label(incidentId)}"`, fields: ["key"], maxResults: 1 });
  return r.issues?.[0]?.key ?? null;
}

async function ensureIssue(n: IncidentNotice): Promise<{ key: string; created: boolean }> {
  const existing = await findIssue(n.incidentId);
  if (existing) return { key: existing, created: false };
  const r = await jira("POST", "/rest/api/3/issue", {
    fields: {
      project: { key: envOf("JIRA_PROJECT_KEY") },
      issuetype: { name: envOf("JIRA_ISSUE_TYPE") || "Task" },
      summary: `[${n.incidentId}] ${n.title}`,
      labels: ["opsrelay", label(n.incidentId)],
      description: adf(`${n.summary}\n\nOpsRelay: ${n.url}`),
    },
  });
  return { key: r.key, created: true };
}

export const jiraPlugin: Plugin = {
  id: "jira",
  name: "Jira",
  vendor: "Atlassian",
  description: "Opens one Jira issue per incident and comments at each stage. Before creating or commenting, it checks Jira for an existing issue or comment, so a retry after a crash never duplicates.",
  capabilities: ["Incident ticket", "Stage comments"],
  envVars: [
    { name: "JIRA_BASE_URL", required: true, hint: "https://your-site.atlassian.net" },
    { name: "JIRA_EMAIL", required: true, hint: "Atlassian account email" },
    { name: "JIRA_API_TOKEN", required: true, hint: "id.atlassian.com → Security → API tokens" },
    { name: "JIRA_PROJECT_KEY", required: true, hint: "e.g. OPS" },
    { name: "JIRA_ISSUE_TYPE", required: false, hint: "Defaults to Task" },
  ],
  configured: () => !!(envOf("JIRA_BASE_URL") && envOf("JIRA_EMAIL") && envOf("JIRA_API_TOKEN") && envOf("JIRA_PROJECT_KEY")),
  async check() {
    try {
      const me = await jira("GET", "/rest/api/3/myself");
      return { ok: true, detail: `Authenticated as ${me.displayName ?? me.emailAddress ?? "user"} on ${base()}` };
    } catch (e) {
      return { ok: false, detail: (e as Error).message };
    }
  },
  async deliver(n, key) {
    const issue = await ensureIssue(n);
    if (n.type === "opened") return { ref: issue.key, detail: issue.created ? `created ${issue.key}` : `found existing ${issue.key}; not re-created` };
    const marker = `[opsrelay:${key}]`;
    const comments = await jira("GET", `/rest/api/3/issue/${issue.key}/comment?maxResults=100`);
    if (JSON.stringify(comments.comments ?? []).includes(marker)) return { ref: issue.key, detail: `comment already on ${issue.key}; not re-sent` };
    await jira("POST", `/rest/api/3/issue/${issue.key}/comment`, { body: adf(`${n.summary} ${marker}`) });
    return { ref: issue.key, detail: `commented on ${issue.key}` };
  },
};
