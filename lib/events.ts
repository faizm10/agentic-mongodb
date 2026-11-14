import { db, C } from "./db";

export type EventKind =
  | "evidence" | "memory" | "decision" | "approval" | "worker" | "checkpoint"
  | "sent" | "verified" | "crash" | "resume" | "check" | "incident" | "guard" | "error" | "notify";

export async function logEvent(incidentId: string, kind: EventKind, message: string, data?: Record<string, unknown>, workerId?: string) {
  await (await db()).collection(C.events).insertOne({ incidentId, kind, message, data, workerId, at: new Date() });
}

export async function recordIntegration(id: "openai" | "atlas_vector_search" | "github", ok: boolean, detail: string) {
  await (await db()).collection<{ _id: string }>(C.integrationLog).updateOne(
    { _id: id },
    { $set: { ok, detail, at: new Date() } },
    { upsert: true },
  );
}
