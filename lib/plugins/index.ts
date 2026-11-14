// Plugin registry + Atlas-backed outbox. Every outbound notification gets a
// deterministic key (incident:event:plugin). A delivered key is never sent again,
// and an interrupted one ("sending") is retried only through the plugin's own
// verify-before-send path. Notification failures never break recovery.
import { db, C } from "../db";
import { env } from "../env";
import { logEvent } from "../events";
import { githubPlugin } from "./github";
import { jiraPlugin } from "./jira";
import { slackPlugin } from "./slack";
import type { IncidentEventType, IncidentNotice, Plugin } from "./types";

export const plugins: Plugin[] = [githubPlugin, slackPlugin, jiraPlugin];
export const getPlugin = (id: string) => plugins.find((p) => p.id === id);

export interface DeliveryDoc {
  _id: string;
  incidentId: string;
  event: IncidentEventType;
  pluginId: string;
  state: "sending" | "delivered" | "skipped" | "failed";
  attempts: number;
  ref?: string;
  summary: string;
  detail: string;
  updatedAt: Date;
}

export async function notify(incidentId: string, type: IncidentEventType, summary: string) {
  try {
    const d = await db();
    const incident = await d.collection(C.incidents).findOne({ _id: incidentId as any });
    const notice: IncidentNotice = { type, incidentId, title: incident?.title ?? incidentId, summary, url: `${env.appUrl}/incidents/${incidentId}` };
    const col = d.collection<DeliveryDoc>(C.deliveries);
    for (const p of plugins.filter((x) => x.deliver)) {
      const key = `${incidentId}:${type}:${p.id}`;
      const prior = await col.findOne({ _id: key });
      if (prior?.state === "delivered") continue;
      if (!p.configured()) {
        await col.updateOne({ _id: key }, { $set: { incidentId, event: type, pluginId: p.id, summary, state: "skipped", detail: "not connected", updatedAt: new Date() }, $setOnInsert: { attempts: 0 } }, { upsert: true });
        continue;
      }
      await col.updateOne({ _id: key }, { $set: { incidentId, event: type, pluginId: p.id, summary, state: "sending", detail: prior ? `retrying after ${prior.state}` : "sending", updatedAt: new Date() }, $inc: { attempts: 1 } }, { upsert: true });
      try {
        const r = await p.deliver!(notice, key);
        await col.updateOne({ _id: key }, { $set: { state: "delivered", ref: r.ref, detail: r.detail, updatedAt: new Date() } });
        await logEvent(incidentId, "notify", `${p.name}: ${r.detail}`, { plugin: p.id, event: type });
      } catch (e) {
        await col.updateOne({ _id: key }, { $set: { state: "failed", detail: (e as Error).message, updatedAt: new Date() } });
        await logEvent(incidentId, "notify", `${p.name} delivery failed: ${(e as Error).message}`, { plugin: p.id, event: type });
      }
    }
  } catch (e) {
    console.error("[plugins] notify failed", e);
  }
}

/** Re-attempt every non-delivered notification for an incident (used by the Plugins page). */
export async function retryDeliveries(incidentId: string) {
  const d = await db();
  const pending = await d.collection<DeliveryDoc>(C.deliveries).find({ incidentId, state: { $ne: "delivered" } }).toArray();
  const seen = new Set<string>();
  for (const f of pending) {
    if (seen.has(f.event)) continue;
    seen.add(f.event);
    await notify(incidentId, f.event, f.summary);
  }
  return { retried: [...seen] };
}

export async function pluginStatuses() {
  const d = await db();
  const recent = await d.collection<DeliveryDoc>(C.deliveries).find().sort({ updatedAt: -1 }).limit(50).toArray();
  return Promise.all(
    plugins.map(async (p) => {
      const configured = p.configured();
      const check = configured ? await p.check().catch((e) => ({ ok: false, detail: (e as Error).message })) : null;
      return {
        id: p.id,
        name: p.name,
        vendor: p.vendor,
        description: p.description,
        capabilities: p.capabilities,
        envVars: p.envVars.map((v) => ({ ...v, set: !!process.env[v.name] })),
        status: !configured ? "not_connected" : check!.ok ? "connected" : "error",
        detail: check?.detail ?? "Add the environment variables below to .env.local, then restart.",
        canSendTest: !!p.sendTest && configured,
        deliveries: recent.filter((r) => r.pluginId === p.id).slice(0, 8),
      };
    }),
  );
}
