// Stateful sandbox that stands in for GitHub's team-membership API.
// It owns its own database (`<MONGODB_DB>_sandbox`), separate from OpsRelay's
// recovery checkpoints, and keeps an append-only mutation log of every write call.
// Shapes loosely follow GitHub:
//   GET /orgs/:org/teams/:teamId
//   GET /orgs/:org/teams/:teamId/memberships/:login
//   PUT /orgs/:org/teams/:teamId/memberships/:login
import "../lib/load-env";
import http from "node:http";
import { MongoClient, type Db } from "mongodb";
import { env, sandboxDbName } from "../lib/env";

const PORT = Number(new URL(env.sandboxUrl).port || 4010);

let sdb: Db;

async function nextSeq(): Promise<number> {
  const r = await sdb
    .collection<{ _id: string; seq: number }>("counters")
    .findOneAndUpdate({ _id: "mutations" }, { $inc: { seq: 1 } }, { upsert: true, returnDocument: "after" });
  return r!.seq;
}

async function logMutation(m: Record<string, unknown>) {
  await sdb.collection("mutation_log").insertOne({ seq: await nextSeq(), at: new Date(), ...m });
}

function send(res: http.ServerResponse, status: number, body: unknown) {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

async function readBody(req: http.IncomingMessage): Promise<any> {
  const chunks: Buffer[] = [];
  for await (const c of req) chunks.push(c as Buffer);
  const raw = Buffer.concat(chunks).toString("utf8");
  return raw ? JSON.parse(raw) : {};
}

async function handle(req: http.IncomingMessage, res: http.ServerResponse) {
  const url = new URL(req.url || "/", `http://localhost:${PORT}`);
  const parts = url.pathname.split("/").filter(Boolean);
  const actor = String(req.headers["x-actor"] || "unknown");
  const requestId = req.headers["x-request-id"] ? String(req.headers["x-request-id"]) : undefined;

  if (req.method === "GET" && url.pathname === "/health") return send(res, 200, { ok: true, db: sandboxDbName() });

  if (req.method === "GET" && url.pathname === "/mutations") {
    const since = url.searchParams.get("since");
    const q = since ? { at: { $gte: new Date(since) } } : {};
    const rows = await sdb.collection("mutation_log").find(q, { projection: { _id: 0 } }).sort({ seq: 1 }).toArray();
    return send(res, 200, rows);
  }

  if (req.method === "POST" && url.pathname === "/admin/reset") {
    const body = await readBody(req);
    await Promise.all(["teams", "memberships", "mutation_log", "counters"].map((c) => sdb.collection(c).deleteMany({})));
    await sdb.collection("memberships").createIndex({ teamId: 1, login: 1 }, { unique: true });
    if (body.teams?.length) await sdb.collection("teams").insertMany(body.teams);
    for (const m of body.memberships || []) {
      await sdb.collection("memberships").insertOne({ teamId: m.teamId, login: m.login, state: "active", createdAt: new Date(m.at || Date.now()), via: m.via });
      await logMutation({ op: "add_membership", teamId: m.teamId, login: m.login, effect: "created", actor: m.via, phase: "seed", at: new Date(m.at || Date.now()) });
    }
    return send(res, 200, { ok: true });
  }

  // /orgs/:org/teams/:teamId[/memberships/:login]
  if (parts[0] === "orgs" && parts[2] === "teams" && parts[3]) {
    const teamId = parts[3];
    const team = await sdb.collection("teams").findOne({ id: teamId }, { projection: { _id: 0 } });

    if (parts.length === 4 && req.method === "GET") {
      return team ? send(res, 200, team) : send(res, 404, { message: "Not Found", detail: `team ${teamId} does not exist` });
    }

    if (parts[4] === "memberships" && parts[5]) {
      const login = parts[5];
      if (!team) {
        if (req.method === "PUT") await logMutation({ op: "add_membership", teamId, login, effect: "rejected_team_not_found", actor, requestId });
        return send(res, 404, { message: "Not Found", detail: `team ${teamId} does not exist` });
      }
      if (req.method === "GET") {
        const m = await sdb.collection("memberships").findOne({ teamId, login });
        return m ? send(res, 200, { state: m.state, role: "member", teamId, login }) : send(res, 404, { message: "Not Found" });
      }
      if (req.method === "PUT") {
        const existing = await sdb.collection("memberships").findOne({ teamId, login });
        if (existing) {
          await logMutation({ op: "add_membership", teamId, login, effect: "already_member", actor, requestId });
          return send(res, 200, { state: existing.state, role: "member", teamId, login });
        }
        await sdb.collection("memberships").insertOne({ teamId, login, state: "active", createdAt: new Date(), via: actor, requestId });
        await logMutation({ op: "add_membership", teamId, login, effect: "created", actor, requestId });
        return send(res, 200, { state: "active", role: "member", teamId, login });
      }
    }
  }
  send(res, 404, { message: "Not Found" });
}

async function main() {
  if (!env.mongoUri) throw new Error("sandbox: MONGODB_URI is not set");
  const client = await new MongoClient(env.mongoUri, { appName: "opsrelay-sandbox", writeConcern: { w: "majority" } }).connect();
  sdb = client.db(sandboxDbName());
  http
    .createServer((req, res) => {
      handle(req, res).catch((e) => send(res, 500, { message: String(e?.message || e) }));
    })
    .listen(PORT, () => console.log(`[sandbox] GitHub-membership sandbox on :${PORT} (db ${sandboxDbName()})`));
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
