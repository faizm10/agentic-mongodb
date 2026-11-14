// The recovery agent. Every step is checkpointed in Atlas *before* and *after*
// it touches the outside world, and every outside effect is verified by reading
// it back from the sandbox API — a 200 on the PUT only means "sent".
import { randomUUID } from "node:crypto";
import { db, C } from "./db";
import { logEvent } from "./events";
import { notify } from "./plugins";
import { assertGrantAllowed, GuardError } from "./decision";
import { sandbox } from "./sandbox-client";
import { runOnboardingCheck } from "./provisioning";
import type { Approval, CheckpointState, Hire, Incident } from "./types";

const LEASE_MS = 15_000;

interface StepCheckpoint {
  state: CheckpointState;
  workerId: string;
  at: Date;
  requestId?: string;
  teamId?: string;
  resolution?: string;
  evidence?: unknown;
}
interface CheckpointDoc {
  _id: string;
  steps: Record<string, StepCheckpoint>;
  workers: string[];
  updatedAt: Date;
}

async function checkpoints() {
  return (await db()).collection<CheckpointDoc>(C.checkpoints);
}

async function setStep(incidentId: string, key: string, cp: StepCheckpoint) {
  await (await checkpoints()).updateOne({ _id: incidentId }, { $set: { [`steps.${key}`]: cp, updatedAt: new Date() } }, { upsert: true });
}

export async function readCheckpoint(incidentId: string) {
  return (await checkpoints()).findOne({ _id: incidentId });
}

async function claimLease(incidentId: string, workerId: string) {
  const d = await db();
  const incidents = d.collection<Incident>(C.incidents);
  const inc = await incidents.findOne({ _id: incidentId });
  if (!inc) throw new Error(`incident ${incidentId} not found`);
  const prev = inc.lease;
  if (prev && prev.workerId !== workerId) {
    const prevWorker = await d.collection<{ _id: string; exitedAt?: Date; signal?: string; exitCode?: number }>(C.workers).findOne({ _id: prev.workerId });
    const expired = prev.expiresAt.getTime() < Date.now();
    if (!prevWorker?.exitedAt && !expired) throw new Error(`incident ${incidentId} is leased by live worker ${prev.workerId}`);
    await logEvent(incidentId, "resume", `Took over lease from ${prev.workerId} (${prevWorker?.exitedAt ? `exited: ${prevWorker.signal ?? `code ${prevWorker.exitCode}`}` : "lease expired"})`, { previousWorker: prev.workerId }, workerId);
  }
  const r = await incidents.updateOne(
    { _id: incidentId, ...(prev ? { "lease.workerId": prev.workerId } : { lease: { $exists: false } }) },
    { $set: { lease: { workerId, expiresAt: new Date(Date.now() + LEASE_MS) } } },
  );
  if (!r.matchedCount) throw new Error("lost lease race");
  await (await checkpoints()).updateOne({ _id: incidentId }, { $addToSet: { workers: workerId }, $setOnInsert: { steps: {} }, $set: { updatedAt: new Date() } }, { upsert: true });
  const timer = setInterval(() => {
    incidents.updateOne({ _id: incidentId, "lease.workerId": workerId }, { $set: { "lease.expiresAt": new Date(Date.now() + LEASE_MS) } }).catch(() => {});
  }, 5000);
  timer.unref();
  return () => clearInterval(timer);
}

export interface RecoveryHooks {
  /** Called right after a sandbox grant commits (PUT returned), before Atlas records it. */
  afterGrantCommitted?: (hireId: string) => void | Promise<void>;
}

export async function runRecovery(incidentId: string, workerId: string, hooks: RecoveryHooks = {}) {
  const d = await db();
  const incident = await d.collection<Incident>(C.incidents).findOne({ _id: incidentId });
  if (!incident?.approval) throw new Error(`incident ${incidentId} has no approval; refusing to act`);
  const approval: Approval = incident.approval;
  const release = await claimLease(incidentId, workerId);

  const existing = await readCheckpoint(incidentId);
  const done = Object.entries(existing?.steps || {}).filter(([, s]) => s.state === "complete" || s.state === "verified").map(([k]) => k);
  await logEvent(incidentId, "worker", `Worker ${workerId} started; Atlas checkpoint has ${done.length ? done.join(", ") : "no completed steps"}`, { steps: existing?.steps }, workerId);

  try {
    // Step 1: restore the approved mapping, then read it back.
    const ms = existing?.steps["restore_mapping"];
    if (ms?.state === "verified") {
      await logEvent(incidentId, "checkpoint", "restore_mapping already verified per checkpoint; skipping", undefined, workerId);
    } else {
      await setStep(incidentId, "restore_mapping", { state: "intent", workerId, at: new Date(), teamId: approval.approvedTeamId });
      const cfg = d.collection<{ _id: string; teamId: string }>(C.config);
      await cfg.updateOne({ _id: approval.mappingKey }, { $set: { teamId: approval.approvedTeamId, updatedAt: new Date(), updatedBy: `opsrelay:${workerId}` } });
      await d.collection(C.configChanges).insertOne({ key: approval.mappingKey, from: approval.fromTeamId, to: approval.approvedTeamId, by: `opsrelay:${workerId}`, at: new Date(), note: `restore approved mapping (${incidentId}, approved by ${approval.approvedBy})` });
      await logEvent(incidentId, "sent", `Wrote ${approval.mappingKey} = ${approval.approvedTeamId}`, undefined, workerId);
      const readBack = await cfg.findOne({ _id: approval.mappingKey });
      const team = await sandbox.getTeam(approval.approvedTeamId);
      if (readBack?.teamId !== approval.approvedTeamId || team.status !== 200) throw new Error(`mapping read-back failed: config=${readBack?.teamId}, team lookup ${team.status}`);
      await setStep(incidentId, "restore_mapping", { state: "verified", workerId, at: new Date(), teamId: readBack.teamId, evidence: { readBack: readBack.teamId, team: team.body } });
      await logEvent(incidentId, "verified", `Read back ${approval.mappingKey} = ${readBack.teamId}; sandbox confirms team ${team.body.slug} exists`, undefined, workerId);
    }

    // Step 2: reconcile each approved hire against actual membership.
    for (const hireId of approval.hireIds) {
      const key = `hire:${hireId}`;
      const cp = (await readCheckpoint(incidentId))?.steps[key];
      if (cp?.state === "complete") {
        await logEvent(incidentId, "checkpoint", `${hireId} already complete per checkpoint (${cp.resolution}); skipping`, undefined, workerId);
        continue;
      }
      const hire = await d.collection<Hire>(C.hires).findOne({ _id: hireId });
      if (!hire) throw new Error(`hire ${hireId} missing`);
      const teamId = approval.approvedTeamId;
      assertGrantAllowed(teamId, hireId, approval);

      let m = await sandbox.getMembership(teamId, hire.githubLogin);
      let resolution: string;
      let requestId = cp?.requestId;
      if (m.active) {
        resolution = cp?.state === "intent" || cp?.state === "sent" ? "grant_took_effect_before_crash" : "pre_existing_membership";
        await logEvent(
          incidentId,
          "verified",
          resolution === "pre_existing_membership"
            ? `${hire.name}: sandbox already shows ${hire.githubLogin} in team ${teamId} — no grant needed`
            : `${hire.name}: checkpoint says "${cp!.state}" by ${cp!.workerId}, sandbox shows membership active → grant took effect; NOT re-sending`,
          { membership: m.body, priorCheckpoint: cp },
          workerId,
        );
      } else {
        requestId ??= randomUUID();
        if (cp?.state === "intent") await logEvent(incidentId, "resume", `${hire.name}: intent recorded by ${cp.workerId} but sandbox shows no membership → grant never landed; re-applying with same request ID`, undefined, workerId);
        await setStep(incidentId, key, { state: "intent", workerId, at: new Date(), requestId, teamId });
        await logEvent(incidentId, "checkpoint", `${hire.name}: recorded grant intent in Atlas (request ${requestId.slice(0, 8)})`, { requestId }, workerId);
        assertGrantAllowed(teamId, hireId, approval);
        const put = await sandbox.putMembership(teamId, hire.githubLogin, `opsrelay:${workerId}`, requestId);
        await hooks.afterGrantCommitted?.(hireId);
        await setStep(incidentId, key, { state: "sent", workerId, at: new Date(), requestId, teamId, evidence: { putStatus: put.status } });
        await logEvent(incidentId, "sent", `${hire.name}: PUT membership → ${put.status} (sent, not yet verified)`, { response: put.body }, workerId);
        m = await sandbox.getMembership(teamId, hire.githubLogin);
        if (!m.active) {
          await setStep(incidentId, key, { state: "failed", workerId, at: new Date(), requestId, teamId, evidence: { get: m.status } });
          throw new Error(`${hireId}: PUT returned ${put.status} but GET shows no membership`);
        }
        resolution = "granted_and_verified";
        await logEvent(incidentId, "verified", `${hire.name}: GET membership → active (took effect)`, { membership: m.body }, workerId);
      }
      await d.collection<Hire>(C.hires).updateOne({ _id: hireId }, { $set: { status: "complete", completedAt: new Date(), completedVia: `opsrelay:${resolution}` } });
      await setStep(incidentId, key, { state: "complete", workerId, at: new Date(), teamId, resolution, requestId });
      await logEvent(incidentId, "checkpoint", `${hire.name}: onboarding marked complete (${resolution})`, undefined, workerId);
    }

    // Step 3: prove the pipeline itself works again with a fresh synthetic hire.
    await d.collection<Incident>(C.incidents).updateOne({ _id: incidentId }, { $set: { status: "monitoring" } });
    const check = await runOnboardingCheck(`onboarding-check:${workerId}`);
    await setStep(incidentId, "synthetic_check", { state: check.ok ? "verified" : "failed", workerId, at: new Date(), evidence: check });
    await logEvent(incidentId, "check", check.ok ? `Fresh onboarding check passed: ${check.detail}` : `Fresh onboarding check FAILED: ${check.detail}`, undefined, workerId);

    const hires = await d.collection<Hire>(C.hires).find({ _id: { $in: approval.hireIds } }).toArray();
    const allDone = hires.every((h) => h.status === "complete");
    if (check.ok && allDone) {
      await d.collection<Incident>(C.incidents).updateOne({ _id: incidentId }, { $set: { status: "resolved", closedAt: new Date() }, $unset: { lease: "" } });
      await notify(incidentId, "resolved", `Resolved: provisioning check green, ${hires.length}/${hires.length} hires reconciled, no duplicate grants.`);
      await logEvent(incidentId, "incident", `Incident resolved: provisioning check green, ${hires.length}/${hires.length} hires reconciled`, undefined, workerId);
    }
    return { resolved: check.ok && allDone };
  } catch (e) {
    await logEvent(incidentId, e instanceof GuardError ? "guard" : "error", (e as Error).message, undefined, workerId);
    throw e;
  } finally {
    release();
  }
}
