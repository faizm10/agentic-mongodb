export type IncidentEventType = "opened" | "proposed" | "escalated" | "approved" | "crashed" | "resolved";

export interface IncidentNotice {
  type: IncidentEventType;
  incidentId: string;
  title: string;
  summary: string;
  url: string;
}

export interface Delivery {
  ref?: string; // e.g. Jira issue key
  detail: string;
}

export interface Plugin {
  id: string;
  name: string;
  vendor: string;
  description: string;
  capabilities: string[];
  envVars: { name: string; required: boolean; hint: string }[];
  configured(): boolean;
  /** Read-only call proving the credentials work. */
  check(): Promise<{ ok: boolean; detail: string }>;
  /** Outbound notification. Must be safe to call again for the same key (verify before re-sending). */
  deliver?(n: IncidentNotice, key: string): Promise<Delivery>;
  /** Optional explicit test the user triggers from the Plugins page. */
  sendTest?(): Promise<{ ok: boolean; detail: string }>;
}

export const envOf = (name: string) => process.env[name] || "";
