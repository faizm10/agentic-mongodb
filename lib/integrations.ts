import { db, C } from "./db";
import { env, isAtlasUri } from "./env";
import { vectorIndexStatus } from "./vector";
import { sandbox } from "./sandbox-client";

export type Status = "verified" | "configured_unverified" | "fallback" | "missing" | "error";
export interface IntegrationRow { id: string; name: string; status: Status; detail: string }

export async function integrationStatus(): Promise<IntegrationRow[]> {
  const rows: IntegrationRow[] = [];
  const log = new Map<string, { ok: boolean; detail: string; at: Date }>();
  try {
    for (const r of await (await db()).collection<{ _id: string; ok: boolean; detail: string; at: Date }>(C.integrationLog).find().toArray()) log.set(r._id, r);
    await (await db()).command({ ping: 1 });
    rows.push(isAtlasUri(env.mongoUri)
      ? { id: "atlas", name: "MongoDB Atlas (checkpoints)", status: "verified", detail: `connected, db ${env.mongoDb}` }
      : { id: "atlas", name: "MongoDB Atlas (checkpoints)", status: "fallback", detail: `connected to LOCAL mongod (${env.mongoUri.replace(/\/\/.*@/, "//***@")}), not Atlas` });
  } catch (e) {
    rows.push({ id: "atlas", name: "MongoDB (checkpoints)", status: "error", detail: (e as Error).message });
  }

  const vs = await vectorIndexStatus().catch((e) => ({ ok: false, detail: (e as Error).message }));
  const vsLast = log.get("atlas_vector_search");
  rows.push({
    id: "vector",
    name: "Atlas Vector Search",
    status: vs.ok ? (vsLast?.ok ? "verified" : "configured_unverified") : "fallback",
    detail: vs.ok ? (vsLast?.ok ? vsLast.detail : `${vs.detail}; no successful query yet`) : `${vs.detail} → in-app cosine fallback (UNVERIFIED)`,
  });

  const oa = log.get("openai");
  rows.push(!env.openaiKey
    ? { id: "openai", name: "OpenAI (embeddings + explanation)", status: "fallback", detail: "OPENAI_API_KEY not set → hashed embeddings + template explanation" }
    : { id: "openai", name: "OpenAI (embeddings + explanation)", status: oa ? (oa.ok ? "verified" : "error") : "configured_unverified", detail: oa?.detail ?? `key set; models ${env.openaiModel} / ${env.embeddingModel}; not called yet` });

  const gh = log.get("github");
  rows.push(!(env.githubOrg && env.githubToken)
    ? { id: "github", name: "GitHub org discovery (read-only)", status: "missing", detail: "optional; GITHUB_ORG/GITHUB_TOKEN not set → seeded catalog" }
    : { id: "github", name: "GitHub org discovery (read-only)", status: gh ? (gh.ok ? "verified" : "error") : "configured_unverified", detail: gh?.detail ?? "configured; not called yet" });

  const sb = await sandbox.health().catch((e) => ({ status: 0, body: { message: (e as Error).message } }));
  rows.push({ id: "sandbox", name: "Membership sandbox API", status: sb.status === 200 ? "verified" : "error", detail: sb.status === 200 ? `${env.sandboxUrl} (db ${sb.body.db})` : `unreachable at ${env.sandboxUrl}: ${sb.body.message}` });
  return rows;
}
