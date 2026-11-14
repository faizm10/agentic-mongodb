import catalog from "@/config/service-catalog.json";
import { env } from "./env";
import { recordIntegration } from "./events";

export type CatalogService = (typeof catalog.services)[number];
export const services: CatalogService[] = catalog.services;

export interface RepoRow { name: string; service?: string; checks: string[]; monitored: boolean; source: "github" | "seeded" }

// Read-only: GET /orgs/{org}/repos. Never writes to GitHub.
export async function discoverRepos(): Promise<{ source: "github" | "seeded"; detail: string; repos: RepoRow[] }> {
  const map = catalog.repoMap as Record<string, { service: string; checks: string[] }>;
  const row = (name: string, source: RepoRow["source"]): RepoRow => ({ name, service: map[name]?.service, checks: map[name]?.checks ?? [], monitored: !!map[name], source });

  if (env.githubOrg && env.githubToken) {
    try {
      const r = await fetch(`https://api.github.com/orgs/${encodeURIComponent(env.githubOrg)}/repos?per_page=100&sort=full_name`, {
        headers: { authorization: `Bearer ${env.githubToken}`, accept: "application/vnd.github+json", "x-github-api-version": "2022-11-28" },
        cache: "no-store",
        signal: AbortSignal.timeout(8000),
      });
      if (!r.ok) throw new Error(`GitHub ${r.status} ${await r.text().then((t) => t.slice(0, 120))}`);
      const repos = (await r.json()) as { name: string }[];
      await recordIntegration("github", true, `listed ${repos.length} repos in ${env.githubOrg} (read-only)`);
      return { source: "github", detail: `${repos.length} repos from github.com/${env.githubOrg}`, repos: repos.map((x) => row(x.name, "github")) };
    } catch (e) {
      await recordIntegration("github", false, (e as Error).message);
      return { source: "seeded", detail: `GitHub discovery failed (${(e as Error).message}); showing seeded catalog`, repos: catalog.seededRepos.map((n) => row(n, "seeded")) };
    }
  }
  return { source: "seeded", detail: "GITHUB_ORG/GITHUB_TOKEN not set; showing seeded catalog", repos: catalog.seededRepos.map((n) => row(n, "seeded")) };
}
