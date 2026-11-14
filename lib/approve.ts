import { db, C } from "./db";
import { logEvent } from "./events";
import { notify } from "./plugins";
import { MAPPING_KEY } from "./provisioning";
import type { Approval, Hire, Incident } from "./types";

export async function approveRestore(incidentId: string, approvedBy: string): Promise<Approval> {
  const d = await db();
  const incident = await d.collection<Incident>(C.incidents).findOne({ _id: incidentId });
  const inv = incident?.investigation;
  if (!inv || inv.decision.action !== "restore_mapping") throw new Error("no restore_mapping proposal to approve");
  if (incident.approval) return incident.approval;
  const approved = await d.collection<{ _id: string; teamId: string }>(C.approvedMappings).findOne({ _id: MAPPING_KEY });
  const hires = await d.collection<Hire>(C.hires).find({ status: "pending", synthetic: { $ne: true } }).sort({ _id: 1 }).toArray();
  const approval: Approval = {
    action: "restore_mapping",
    mappingKey: MAPPING_KEY,
    fromTeamId: inv.signals.currentTeamId,
    approvedTeamId: approved!.teamId,
    hireIds: hires.map((h) => h._id),
    approvedBy,
    approvedAt: new Date(),
  };
  await d.collection<Incident>(C.incidents).updateOne({ _id: incidentId, approval: { $exists: false } }, { $set: { approval, status: "recovering" } });
  await notify(incidentId, "approved", `${approvedBy} approved recovery: restore team ${approval.approvedTeamId} and reconcile ${approval.hireIds.length} hires.`);
  await logEvent(incidentId, "approval", `${approvedBy} approved: restore team ${approval.approvedTeamId}, reconcile ${approval.hireIds.join(", ")}`, { approval });
  return approval;
}
