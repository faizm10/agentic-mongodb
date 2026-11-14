import { envOf, type IncidentNotice, type Plugin } from "./types";

const ICON: Record<IncidentNotice["type"], string> = {
  opened: ":rotating_light:",
  proposed: ":mag:",
  escalated: ":raising_hand:",
  approved: ":white_check_mark:",
  crashed: ":boom:",
  resolved: ":large_green_circle:",
};

async function post(text: string) {
  const r = await fetch(envOf("SLACK_WEBHOOK_URL"), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text }),
    signal: AbortSignal.timeout(8000),
  });
  const body = await r.text();
  if (!r.ok) throw new Error(`Slack ${r.status}: ${body.slice(0, 120)}`);
  return body;
}

export const slackPlugin: Plugin = {
  id: "slack",
  name: "Slack",
  vendor: "Slack",
  description: "Posts incident updates (opened, proposed fix, approved, worker crash, resolved) to one channel through an incoming webhook.",
  capabilities: ["Incident notifications"],
  envVars: [{ name: "SLACK_WEBHOOK_URL", required: true, hint: "Incoming webhook URL for the on-call channel" }],
  configured: () => !!envOf("SLACK_WEBHOOK_URL"),
  async check() {
    // Incoming webhooks have no read endpoint; validate shape only. Use "Send test message" to prove delivery.
    const ok = /^https:\/\/hooks\.slack\.com\/services\//.test(envOf("SLACK_WEBHOOK_URL")) || /^http:\/\/(localhost|127\.0\.0\.1)/.test(envOf("SLACK_WEBHOOK_URL"));
    return ok ? { ok: true, detail: "Webhook URL looks valid. Delivery is confirmed only by a sent message." } : { ok: false, detail: "SLACK_WEBHOOK_URL is not a hooks.slack.com URL" };
  },
  async deliver(n) {
    // A webhook can't be queried afterwards, so the Atlas outbox is the only dedupe: delivered keys are never re-sent.
    await post(`${ICON[n.type]} *${n.incidentId}*: ${n.summary}\n<${n.url}|Open in OpsRelay>`);
    return { detail: "posted to Slack" };
  },
  async sendTest() {
    try {
      await post(":wave: OpsRelay test message. The Slack plugin is connected.");
      return { ok: true, detail: "Test message posted" };
    } catch (e) {
      return { ok: false, detail: (e as Error).message };
    }
  },
};
