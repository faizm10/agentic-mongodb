import { randomBytes } from "node:crypto";
import { db, C } from "./db";
import { embed, embeddingSource } from "./embeddings";
import { logEvent } from "./events";
import { notify } from "./plugins";
import { sandbox } from "./sandbox-client";
import { MAPPING_KEY, provisionHire, runOnboardingCheck, runPortalCheck } from "./provisioning";
import type { Hire, Incident } from "./types";

export type Variant = "config-drift" | "stalled-worker";

const APPROVED_TEAM = "4821";
const BAD_TEAM = "4812";
const min = (n: number) => new Date(Date.now() - n * 60_000);

export const PAST_INCIDENTS = [
  {
    _id: "INC-2291",
    title: "New-hire GitHub access stuck Pending; provisioning worker hung",
    summary: "Onboarding records stuck in Pending for 6 new hires. Portal sign-in worked. GitHub team provisioning failing; provisioning worker heartbeat stale for 40 minutes and queue backed up.",
    resolution: "Restarted the provisioning worker; queue drained and pending hires completed within 10 minutes.",
  },
  {
    _id: "INC-1874",
    title: "Contractor SSO login loop",
    summary: "Contractors redirected in a loop at sign-in after IdP certificate rotation. Portal unavailable for contractor accounts.",
    resolution: "Re-uploaded the rotated SAML signing certificate to the portal.",
  },
  {
    _id: "INC-2050",
    title: "Payroll export delayed by expired API token",
    summary: "Nightly payroll export job failing with 401 Unauthorized after a service token expired.",
    resolution: "Rotated the payroll service token and re-ran the export.",
  },
  {
    _id: "INC-1660",
    title: "Laptop shipment requests not syncing to vendor",
    summary: "Hardware requests for new hires stuck in Submitted; vendor webhook returning 500.",
    resolution: "Vendor fixed their webhook; replayed failed requests.",
  },
];

export async function seedPastIncidents() {
  const col = (await db()).collection<{ _id: string }>(C.pastIncidents);
  const vectors = await embed(PAST_INCIDENTS.map((p) => `${p.title}. ${p.summary} Resolution: ${p.resolution}`));
  await col.deleteMany({});
  await col.insertMany(PAST_INCIDENTS.map((p, i) => ({ ...p, embedding: vectors[i].vector, embeddingSource: vectors[i].source, seeded: true })));
  return { count: PAST_INCIDENTS.length, source: embeddingSource(), dims: vectors[0].vector.length };
}

export async function resetDemo(variant: Variant = "config-drift") {
  const d = await db();
  const keep = new Set<string>([C.pastIncidents, C.integrationLog]);
  await Promise.all(Object.values(C).filter((c) => !keep.has(c)).map((c) => d.collection(c).deleteMany({})));

  await sandbox.reset({
    teams: [
      { id: APPROVED_TEAM, slug: "eng-new-hires", name: "Engineering New Hires" },
      { id: "1203", slug: "platform", name: "Platform" },
    ],
    // Hire A already received the correct membership from an earlier access request.
    memberships: [{ teamId: APPROVED_TEAM, login: "maya-chen", via: "access-request AR-1182", at: min(60 * 26) }],
  });

  const hires: Hire[] = [
    { _id: "hire-a", name: "Maya Chen", email: "maya.chen@example.com", githubLogin: "maya-chen", status: "pending", seeded: true },
    { _id: "hire-b", name: "Jordan Ellis", email: "jordan.ellis@example.com", githubLogin: "jordan-ellis", status: "pending", seeded: true },
  ];
  await d.collection<Hire>(C.hires).insertMany(hires);
  await d.collection<{ _id: string }>(C.approvedMappings).insertOne({ _id: MAPPING_KEY, teamId: APPROVED_TEAM, teamSlug: "eng-new-hires", approvedBy: "it-governance", ticket: "CHG-0419", approvedAt: min(60 * 24 * 30), seeded: true } as any);

  const drift = variant === "config-drift";
  await d.collection<{ _id: string }>(C.config).insertOne({ _id: MAPPING_KEY, teamId: drift ? BAD_TEAM : APPROVED_TEAM, updatedAt: drift ? min(38) : min(60 * 24 * 30), updatedBy: drift ? "d.okafor" : "it-governance" } as any);
  await d.collection(C.configChanges).insertMany([
    { key: "portal.banner", from: "", to: "Welcome, autumn cohort!", by: "comms-team", at: min(60 * 5), seeded: true },
    ...(drift ? [{ key: MAPPING_KEY, from: APPROVED_TEAM, to: BAD_TEAM, by: "d.okafor", at: min(38), note: "Consolidate onboarding team IDs", seeded: true }] : []),
  ]);
  await d.collection<{ _id: string }>(C.controls).insertMany([
    { _id: "fault_crash_after_grant", armed: false },
    { _id: "variant", value: variant },
  ] as any);
  await d.collection<{ _id: string }>(C.workerHeartbeats).insertOne({ _id: "provisioning-worker", lastBeatAt: drift ? new Date() : min(45), queueDepth: 2, seeded: true } as any);

  // Seeded check history (labeled), followed by real checks below.
  const history = [];
  for (let i = 12; i >= 1; i--) {
    const at = new Date(Date.now() - i * 5 * 60_000);
    history.push({ checkId: "portal-availability", serviceId: "employee-portal", ok: true, detail: "seeded history", seeded: true, at });
    history.push({ checkId: "onboarding-provisioning", serviceId: "onboarding-provisioning", ok: i > 7, detail: i > 7 ? "seeded history" : "seeded history: synthetic hire stuck pending", seeded: true, at });
    history.push({ checkId: "expense-api", serviceId: "expense-reports", ok: true, detail: "seeded (not a live check)", seeded: true, at });
    history.push({ checkId: "helpdesk-api", serviceId: "it-helpdesk", ok: i !== 9, detail: "seeded (not a live check)", seeded: true, at });
  }
  await d.collection(C.checks).insertMany(history);

  const past = (await d.collection(C.pastIncidents).countDocuments({ embeddingSource: embeddingSource() })) === PAST_INCIDENTS.length ? null : await seedPastIncidents();

  // Real pipeline attempts against the sandbox produce today's live errors.
  if (drift) {
    for (const h of hires) await provisionHire(h._id);
  } else {
    await d.collection(C.provisioningAttempts).insertMany(hires.map((h) => ({ hireId: h._id, teamId: APPROVED_TEAM, status: 504, ok: false, error: "timeout waiting for provisioning worker", actor: "onboarding-pipeline", at: min(30), seeded: true })));
  }
  const check = await runOnboardingCheck();
  await runPortalCheck();

  const incidentId = `INC-${new Date().toISOString().slice(0, 10).replace(/-/g, "")}-${randomBytes(2).toString("hex").toUpperCase()}`;
  await d.collection<Incident>(C.incidents).insertOne({
    _id: incidentId,
    title: "New hires stuck Pending: GitHub team provisioning failing",
    serviceId: "onboarding-provisioning",
    status: "open",
    openedAt: new Date(),
  });
  await d.collection<{ _id: string }>(C.controls).insertOne({ _id: "current_incident", value: incidentId } as any);
  await logEvent(incidentId, "incident", `Opened by failing check: ${check.detail}`);
  await notify(incidentId, "opened", `Incident opened: new hires stuck Pending. ${check.detail}`);
  return { incidentId, variant, pastIncidents: past ?? "unchanged" };
}
