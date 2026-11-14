import { db, C } from "./db";
import { sandbox, type Mutation } from "./sandbox-client";
import { MAPPING_KEY } from "./provisioning";
import type { Hire, Incident } from "./types";

export async function buildProof(incidentId: string) {
  const d = await db();
  const incident = await d.collection<Incident>(C.incidents).findOne({ _id: incidentId });
  if (!incident) throw new Error("incident not found");
  const workers = await d.collection(C.workers).find({ incidentId }).sort({ launchedAt: 1, startedAt: 1 }).toArray();
  let mutations: Mutation[] = [];
  let sandboxError: string | undefined;
  try {
    mutations = await sandbox.mutations(incident.openedAt);
  } catch (e) {
    sandboxError = (e as Error).message;
  }
  // Before approval, show the stuck (non-synthetic) hires against the approved team.
  const approvedTeamId =
    incident.approval?.approvedTeamId ??
    (await d.collection<{ _id: string; teamId: string }>(C.approvedMappings).findOne({ _id: MAPPING_KEY }))?.teamId;
  const hires = await d
    .collection<Hire>(C.hires)
    .find(incident.approval ? { _id: { $in: incident.approval.hireIds } } : { synthetic: { $ne: true } })
    .sort({ _id: 1 })
    .toArray();
  const perHire = await Promise.all(
    hires.map(async (h) => {
      const puts = mutations.filter((m) => m.login === h.githubLogin && m.effect !== "rejected_team_not_found");
      const membership = approvedTeamId ? await sandbox.getMembership(approvedTeamId, h.githubLogin).catch(() => null) : null;
      return {
        hireId: h._id,
        name: h.name,
        login: h.githubLogin,
        appStatus: h.status,
        completedVia: h.completedVia,
        sandboxActive: membership?.active ?? null,
        grantsCreatedSinceIncident: puts.filter((m) => m.effect === "created").length,
        putCallsSinceIncident: puts.length,
      };
    }),
  );
  return { incidentId, workers, mutations, perHire, sandboxError };
}
