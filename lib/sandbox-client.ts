import { env } from "./env";

export const ORG = "acme";

async function call(method: string, path: string, opts: { actor?: string; requestId?: string; body?: unknown } = {}) {
  const res = await fetch(`${env.sandboxUrl}${path}`, {
    method,
    headers: {
      "content-type": "application/json",
      ...(opts.actor ? { "x-actor": opts.actor } : {}),
      ...(opts.requestId ? { "x-request-id": opts.requestId } : {}),
    },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    cache: "no-store",
  });
  const body = await res.json().catch(() => ({}));
  return { status: res.status, body };
}

export const sandbox = {
  health: () => call("GET", "/health"),
  getTeam: (teamId: string) => call("GET", `/orgs/${ORG}/teams/${teamId}`),
  async getMembership(teamId: string, login: string) {
    const r = await call("GET", `/orgs/${ORG}/teams/${teamId}/memberships/${login}`);
    return { ...r, active: r.status === 200 && r.body?.state === "active" };
  },
  putMembership: (teamId: string, login: string, actor: string, requestId?: string) =>
    call("PUT", `/orgs/${ORG}/teams/${teamId}/memberships/${login}`, { actor, requestId }),
  async mutations(since?: Date): Promise<Mutation[]> {
    const r = await call("GET", `/mutations${since ? `?since=${since.toISOString()}` : ""}`);
    return r.body as Mutation[];
  },
  reset: (body: unknown) => call("POST", "/admin/reset", { body }),
};

export interface Mutation {
  seq: number;
  at: string;
  op: "add_membership";
  teamId: string;
  login: string;
  effect: "created" | "already_member" | "rejected_team_not_found";
  actor: string;
  requestId?: string;
  phase?: "seed";
}
