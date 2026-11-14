import { discoverRepos } from "../catalog";
import { envOf, type Plugin } from "./types";

export const githubPlugin: Plugin = {
  id: "github",
  name: "GitHub",
  vendor: "GitHub",
  description: "Read-only org discovery: lists repos and maps them to monitored services. Team membership changes go to the sandbox, never to GitHub.",
  capabilities: ["Repo discovery (read-only)"],
  envVars: [
    { name: "GITHUB_ORG", required: true, hint: "Organization login, e.g. acme" },
    { name: "GITHUB_TOKEN", required: true, hint: "Fine-grained token with read access to the org's repos" },
  ],
  configured: () => !!(envOf("GITHUB_ORG") && envOf("GITHUB_TOKEN")),
  async check() {
    const r = await discoverRepos();
    return r.source === "github" ? { ok: true, detail: r.detail } : { ok: false, detail: r.detail };
  },
};
