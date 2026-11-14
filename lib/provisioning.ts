// The company's normal onboarding pipeline (not the agent). It reads the team
// mapping from config and provisions through the sandbox API.
import { db, C } from "./db";
import { env } from "./env";
import { sandbox } from "./sandbox-client";
import type { Hire } from "./types";

export const MAPPING_KEY = "onboarding.github_team";

export async function currentTeamId(): Promise<string> {
  const doc = await (await db()).collection<{ _id: string; teamId: string }>(C.config).findOne({ _id: MAPPING_KEY });
  if (!doc) throw new Error(`config ${MAPPING_KEY} missing`);
  return doc.teamId;
}

// Returns false when the simulated provisioning worker is hung (stalled-worker variant).
async function touchHeartbeat(): Promise<boolean> {
  const d = await db();
  const variant = await d.collection<{ _id: string; value?: string }>(C.controls).findOne({ _id: "variant" });
  if (variant?.value === "stalled-worker") return false;
  await d.collection<{ _id: string }>(C.workerHeartbeats).updateOne(
    { _id: "provisioning-worker" },
    { $set: { lastBeatAt: new Date(), source: "pipeline run (local simulation)" } },
    { upsert: true },
  );
  return true;
}

export async function provisionHire(hireId: string, actor = "onboarding-pipeline") {
  const d = await db();
  const hire = await d.collection<Hire>(C.hires).findOne({ _id: hireId });
  if (!hire) throw new Error(`hire ${hireId} not found`);
  const teamId = await currentTeamId();
  if (!(await touchHeartbeat())) {
    const error = "timeout waiting for provisioning worker (simulated hung worker)";
    await d.collection(C.provisioningAttempts).insertOne({ hireId, teamId, status: 504, ok: false, error, actor, at: new Date() });
    return { ok: false, teamId, status: 504, error };
  }

  const existing = await sandbox.getMembership(teamId, hire.githubLogin);
  let status = existing.status;
  let error = existing.body?.detail as string | undefined;
  if (!existing.active && existing.status !== 404) error = `unexpected ${existing.status}`;
  if (!existing.active) {
    const team = await sandbox.getTeam(teamId);
    if (team.status === 404) {
      status = 404;
      error = `Not Found: team ${teamId} does not exist`;
    } else {
      const put = await sandbox.putMembership(teamId, hire.githubLogin, actor);
      const after = await sandbox.getMembership(teamId, hire.githubLogin);
      status = after.active ? 200 : put.status;
      error = after.active ? undefined : `grant not visible after PUT (${put.status})`;
    }
  }
  const ok = status === 200 && !error;
  await d.collection(C.provisioningAttempts).insertOne({ hireId, teamId, status, ok, error, actor, at: new Date() });
  if (ok) await d.collection<Hire>(C.hires).updateOne({ _id: hireId }, { $set: { status: "complete", completedAt: new Date(), completedVia: actor } });
  return { ok, teamId, status, error };
}

export async function recordCheck(checkId: string, serviceId: string, ok: boolean, detail: string, extra: Record<string, unknown> = {}) {
  await (await db()).collection(C.checks).insertOne({ checkId, serviceId, ok, detail, at: new Date(), ...extra });
}

// Fresh end-to-end onboarding check with a brand-new synthetic hire.
export async function runOnboardingCheck(actor = "onboarding-check") {
  const d = await db();
  const n = Date.now().toString(36);
  const hire: Hire = { _id: `synthetic-${n}`, name: `Synthetic check ${n}`, email: `synthetic+${n}@example.invalid`, githubLogin: `opsrelay-synthetic-${n}`, status: "pending", synthetic: true };
  await d.collection<Hire>(C.hires).insertOne(hire);
  const r = await provisionHire(hire._id, actor);
  const verified = r.ok && (await sandbox.getMembership(r.teamId, hire.githubLogin)).active;
  const detail = verified ? `synthetic hire ${hire._id} provisioned into team ${r.teamId} and verified via sandbox GET` : `synthetic hire ${hire._id} failed: ${r.error ?? r.status}`;
  await recordCheck("onboarding-provisioning", "onboarding-provisioning", verified, detail, { syntheticHireId: hire._id });
  return { ok: verified, detail, hireId: hire._id };
}

export async function runPortalCheck() {
  let ok = false, detail = "";
  try {
    const r = await fetch(`${env.appUrl}/portal`, { cache: "no-store", signal: AbortSignal.timeout(4000) });
    ok = r.status === 200;
    detail = `GET /portal → ${r.status}`;
  } catch (e) {
    detail = `GET /portal failed: ${(e as Error).message}`;
  }
  await recordCheck("portal-availability", "employee-portal", ok, detail);
  return { ok, detail };
}
