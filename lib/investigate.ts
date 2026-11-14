import { db, C } from "./db";
import { logEvent } from "./events";
import { notify } from "./plugins";
import { decide } from "./decision";
import { explainDecision } from "./llm";
import { findSimilarIncidents } from "./vector";
import { MAPPING_KEY, currentTeamId } from "./provisioning";
import type { Incident, Investigation, Signals } from "./types";

const HEALTHY_HEARTBEAT_SEC = 300;

export async function investigate(incidentId: string): Promise<Investigation> {
  const d = await db();
  const incidents = d.collection<Incident>(C.incidents);
  const incident = await incidents.findOne({ _id: incidentId });
  if (!incident) throw new Error(`incident ${incidentId} not found`);
  await incidents.updateOne({ _id: incidentId }, { $set: { status: "investigating" } });

  const since = new Date(Date.now() - 24 * 3600_000);
  const attempts = await d.collection(C.provisioningAttempts).find({ ok: false, at: { $gte: since } }).sort({ at: -1 }).limit(20).toArray();
  const errors = attempts.map((a) => ({ hireId: a.hireId, teamId: a.teamId, status: a.status, error: a.error, at: a.at }));
  await logEvent(incidentId, "evidence", `Read ${errors.length} failed provisioning attempts from the last 24h`, { sample: errors.slice(0, 3) });

  const hb = await d.collection<{ _id: string; lastBeatAt: Date; queueDepth: number; seeded?: boolean }>(C.workerHeartbeats).findOne({ _id: "provisioning-worker" });
  const ageSec = hb ? Math.round((Date.now() - hb.lastBeatAt.getTime()) / 1000) : null;
  await logEvent(incidentId, "evidence", hb ? `Provisioning worker heartbeat ${ageSec}s ago, queue depth ${hb.queueDepth}` : "No provisioning worker heartbeat found", { heartbeat: hb });

  const changes = await d.collection(C.configChanges).find({ key: MAPPING_KEY, at: { $gte: since } }).sort({ at: -1 }).toArray();
  const approved = await d.collection<{ _id: string; teamId: string }>(C.approvedMappings).findOne({ _id: MAPPING_KEY });
  const teamId = await currentTeamId();
  await logEvent(incidentId, "evidence", changes.length ? `Config change: ${MAPPING_KEY} ${changes[0].from} → ${changes[0].to} by ${changes[0].by}` : `No recent changes to ${MAPPING_KEY}`, { changes, approvedTeamId: approved?.teamId, currentTeamId: teamId });

  const portal = await d.collection(C.checks).find({ checkId: "portal-availability" }).sort({ at: -1 }).limit(1).next();

  const signals: Signals = {
    teamNotFoundErrors: errors.filter((e) => e.status === 404 && /team/i.test(e.error || "")).length,
    timeoutErrors: errors.filter((e) => /timeout/i.test(e.error || "")).length,
    recentMappingChange: changes.length > 0,
    currentTeamId: teamId,
    approvedTeamId: approved?.teamId ?? "unknown",
    mappingDiffersFromApproved: !!approved && approved.teamId !== teamId,
    workerHeartbeatAgeSec: ageSec,
    workerHealthy: ageSec !== null && ageSec < HEALTHY_HEARTBEAT_SEC,
    portalUp: portal ? !!portal.ok : true,
  };

  const query =
    `Employee onboarding records stuck Pending. New hires can sign in to the portal. GitHub team provisioning failing. ` +
    `Errors: ${[...new Set(errors.map((e) => e.error))].join("; ") || "none"}. Worker ${signals.workerHealthy ? "healthy" : "stale heartbeat"}.`;
  const similar = await findSimilarIncidents(query);
  await logEvent(
    incidentId,
    "memory",
    `${similar.mode === "atlas_vector_search" ? "Atlas Vector Search" : "UNVERIFIED fallback cosine search"}: top match "${similar.results[0]?.title}" (${similar.results[0]?.score.toFixed(3)})`,
    { similar },
  );

  let decision = decide(signals);
  const partial = { signals, evidence: { errors, heartbeat: hb ? { workerId: hb._id, lastBeatAt: hb.lastBeatAt, queueDepth: hb.queueDepth, seeded: !!hb.seeded } : null, configChanges: changes as any }, similar, decision };
  const explanation = await explainDecision(partial);
  if (explanation.llmRecommendedAction && explanation.llmRecommendedAction !== decision.action) {
    decision = { action: "human_review", rule: `R0: model recommended ${explanation.llmRecommendedAction} but rules chose ${decision.action}`, reasons: decision.reasons };
  }
  const inv: Investigation = { ...partial, decision, explanation, at: new Date() };

  const status = decision.action === "restore_mapping" ? "awaiting_approval" : "needs_human";
  await incidents.updateOne({ _id: incidentId }, { $set: { investigation: inv, status } });
  await notify(
    incidentId,
    status === "awaiting_approval" ? "proposed" : "escalated",
    status === "awaiting_approval"
      ? `Agent proposes restoring ${MAPPING_KEY} ${signals.currentTeamId} → ${signals.approvedTeamId}. Waiting for approval.`
      : `Agent escalated to a human (${decision.rule}). No automatic action.`,
  );
  await logEvent(incidentId, "decision", decision.action === "restore_mapping" ? `Proposed: restore ${MAPPING_KEY} ${signals.currentTeamId} → ${signals.approvedTeamId} (needs approval)` : `Escalated to human review: ${decision.rule}`, { decision, explanationSource: explanation.source });
  return inv;
}
