// End-to-end crash/resume smoke test.
//   1. seed → investigate → approve
//   2. worker #1 with fault armed: SIGKILLed right after Hire B's sandbox grant commits
//   3. worker #2 (fresh process, same incident): resumes from the Atlas checkpoint
//   4. assert: both hires complete, Hire B granted exactly once, Hire A zero new grants,
//      synthetic onboarding check green, incident resolved
// Uses MONGODB_URI if set (database `<MONGODB_DB>_smoke`), otherwise a throwaway local mongod.
import "../lib/load-env";
import { ensureMongo } from "./local-mongo";
import { startSandbox, waitFor } from "./proc";
import { startFakePlugins } from "./fake-plugins";

process.env.MONGODB_DB = `${process.env.MONGODB_DB || "opsrelay"}_smoke`;
process.env.SANDBOX_URL = "http://localhost:4011";
process.env.APP_URL = process.env.SMOKE_APP_URL || "http://localhost:3999"; // nothing listens: portal check is not part of this test
// Plugins always point at a local test double here, so the smoke test never posts to a real Slack or Jira.
const FAKE_PORT = 4012;
Object.assign(process.env, {
  SLACK_WEBHOOK_URL: `http://127.0.0.1:${FAKE_PORT}/slack`,
  JIRA_BASE_URL: `http://127.0.0.1:${FAKE_PORT}/jira`,
  JIRA_EMAIL: "smoke@example.invalid",
  JIRA_API_TOKEN: "fake",
  JIRA_PROJECT_KEY: "OPS",
});

let failures = 0;
const results: string[] = [];
function check(name: string, ok: boolean, detail = "") {
  results.push(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
  console.log(results.at(-1));
  if (!ok) failures++;
}

const mongo = await ensureMongo({ persistent: false });
const sb = startSandbox("ignore");
const fake = startFakePlugins(FAKE_PORT);
const cleanup = async () => {
  sb.kill("SIGTERM");
  fake.close();
  const { closeMongo } = await import("../lib/db");
  await closeMongo();
  await mongo.stop();
};

try {
  await waitFor(`${process.env.SANDBOX_URL}/health`);
  const { db, C } = await import("../lib/db");
  const { env, isAtlasUri } = await import("../lib/env");
  const { resetDemo } = await import("../lib/seed");
  const { investigate } = await import("../lib/investigate");
  const { approveRestore } = await import("../lib/approve");
  const { launchWorker } = await import("../lib/supervisor");
  const { readCheckpoint } = await import("../lib/recovery");
  const { buildProof } = await import("../lib/proof");
  const { sandbox } = await import("../lib/sandbox-client");
  const { decide, assertGrantAllowed, GuardError } = await import("../lib/decision");
  const { currentTeamId } = await import("../lib/provisioning");
  console.log(`mongo: ${isAtlasUri(env.mongoUri) ? "Atlas" : "local mongod"} db=${env.mongoDb}; openai: ${env.openaiKey ? "key set" : "not set (fallbacks)"}\n`);

  // --- decision branches (same evidence types, different situations) ---
  const base = { teamNotFoundErrors: 0, timeoutErrors: 0, recentMappingChange: false, currentTeamId: "4821", approvedTeamId: "4821", mappingDiffersFromApproved: false, workerHeartbeatAgeSec: 10, workerHealthy: true, portalUp: true };
  check("decide: config drift + healthy worker → restore_mapping", decide({ ...base, teamNotFoundErrors: 2, recentMappingChange: true, currentTeamId: "4812", mappingDiffersFromApproved: true }).action === "restore_mapping");
  check("decide: stale worker + correct mapping → restart_worker", decide({ ...base, timeoutErrors: 2, workerHeartbeatAgeSec: 2700, workerHealthy: false }).action === "restart_worker");
  check("decide: drift AND stale worker → human_review", decide({ ...base, teamNotFoundErrors: 2, recentMappingChange: true, currentTeamId: "4812", mappingDiffersFromApproved: true, workerHeartbeatAgeSec: 2700, workerHealthy: false }).action === "human_review");
  const approvalShape = { approvedTeamId: "4821", hireIds: ["hire-a", "hire-b"] };
  const throws = (f: () => void) => { try { f(); return false; } catch (e) { return e instanceof GuardError; } };
  check("guard: blocks unapproved team", throws(() => assertGrantAllowed("4812", "hire-b", approvalShape)));
  check("guard: blocks unapproved hire", throws(() => assertGrantAllowed("4821", "hire-z", approvalShape)));

  // --- stalled-worker variant takes a different path from the same investigation code ---
  const stalled = await resetDemo("stalled-worker");
  const invS = await investigate(stalled.incidentId);
  check("investigate(stalled-worker) does not propose restore_mapping", invS.decision.action !== "restore_mapping", `${invS.decision.action} via ${invS.decision.rule}`);

  // --- main scenario ---
  fake.state.slack.length = 0;
  fake.state.issues.length = 0;
  fake.state.creates = 0;
  const { incidentId } = await resetDemo("config-drift");
  const d = await db();
  check("seed: both hires pending", (await d.collection(C.hires).countDocuments({ _id: { $in: ["hire-a", "hire-b"] } as any, status: "pending" })) === 2);
  check("seed: Hire A already in approved team (sandbox)", (await sandbox.getMembership("4821", "maya-chen")).active);
  check("seed: config points at invalid team 4812", (await currentTeamId()) === "4812");

  const inv = await investigate(incidentId);
  check("investigate: proposes restore_mapping", inv.decision.action === "restore_mapping", inv.decision.rule);
  check("investigate: past incident recalled (worker restart)", inv.similar.results[0]?._id === "INC-2291", `${inv.similar.mode}, top=${inv.similar.results[0]?._id} ${inv.similar.results[0]?.score.toFixed(3)}`);
  check("investigate: explanation produced", inv.explanation.text.length > 40, `source=${inv.explanation.source}${inv.explanation.error ? ` error=${inv.explanation.error}` : ""}`);
  if (inv.decision.action !== "restore_mapping") throw new Error("cannot continue without restore_mapping proposal");

  await approveRestore(incidentId, "smoke-test");
  await d.collection<{ _id: string }>(C.controls).updateOne({ _id: "fault_crash_after_grant" }, { $set: { armed: true } });

  const w1 = await launchWorker(incidentId, "smoke-test");
  const e1 = await w1.exited;
  check("worker #1 killed by fault (SIGKILL)", e1.signal === "SIGKILL", `${w1.workerId} ${JSON.stringify(e1)}`);
  const cp1 = await readCheckpoint(incidentId);
  check("after crash: restore_mapping verified in checkpoint", cp1?.steps["restore_mapping"]?.state === "verified");
  check("after crash: Hire A complete in checkpoint", cp1?.steps["hire:hire-a"]?.state === "complete", cp1?.steps["hire:hire-a"]?.resolution);
  check("after crash: Hire B checkpoint stuck at intent (success never recorded)", cp1?.steps["hire:hire-b"]?.state === "intent");
  check("after crash: Hire B grant DID commit in sandbox", (await sandbox.getMembership("4821", "jordan-ellis")).active);
  check("after crash: Hire B still pending in app", (await d.collection(C.hires).findOne({ _id: "hire-b" as any }))?.status === "pending");

  const w2 = await launchWorker(incidentId, "smoke-test");
  const e2 = await w2.exited;
  check("worker #2 exited cleanly", e2.code === 0 && !e2.signal, `${w2.workerId} ${JSON.stringify(e2)}`);
  check("different worker ID, same incident", w1.workerId !== w2.workerId, `${w1.workerId} → ${w2.workerId} on ${incidentId}`);

  const proof = await buildProof(incidentId);
  const a = proof.perHire.find((h) => h.hireId === "hire-a")!;
  const b = proof.perHire.find((h) => h.hireId === "hire-b")!;
  check("Hire A complete via existing membership", a.appStatus === "complete" && a.completedVia === "opsrelay:pre_existing_membership", a.completedVia);
  check("Hire A: zero new grants since incident", a.grantsCreatedSinceIncident === 0 && a.putCallsSinceIncident === 0, `created=${a.grantsCreatedSinceIncident} puts=${a.putCallsSinceIncident}`);
  check("Hire B complete, recognized grant from crashed worker", b.appStatus === "complete" && b.completedVia === "opsrelay:grant_took_effect_before_crash", b.completedVia);
  check("Hire B: exactly one grant, one PUT", b.grantsCreatedSinceIncident === 1 && b.putCallsSinceIncident === 1, `created=${b.grantsCreatedSinceIncident} puts=${b.putCallsSinceIncident}`);
  const bPut = proof.mutations.find((m) => m.login === "jordan-ellis" && m.effect === "created");
  check("Hire B's single grant was made by worker #1", bPut?.actor === `opsrelay:${w1.workerId}`, bPut?.actor);
  check("config read back = approved team 4821", (await currentTeamId()) === "4821");
  const cp2 = await readCheckpoint(incidentId);
  check("fresh synthetic onboarding check passed", cp2?.steps["synthetic_check"]?.state === "verified");
  check("checkpoint lists both workers", JSON.stringify(cp2?.workers) === JSON.stringify([w1.workerId, w2.workerId]), JSON.stringify(cp2?.workers));
  const incident = await d.collection(C.incidents).findOne({ _id: incidentId as any });
  check("incident resolved", incident?.status === "resolved");
  const latest = await d.collection(C.checks).find({ checkId: "onboarding-provisioning" }).sort({ at: -1 }).limit(1).next();
  check("provisioning health check is green", !!latest?.ok, latest?.detail);

  // --- plugins: one notification per lifecycle event, never duplicated ---
  const { notify } = await import("../lib/plugins");
  check("slack: 5 lifecycle messages (opened, proposed, approved, crashed, resolved)", fake.state.slack.length === 5, fake.state.slack.map((t) => t.split(" ")[0]).join(" "));
  check("jira: exactly one issue for the incident", fake.state.creates === 1 && fake.state.issues.length === 1, fake.state.issues[0]?.key);
  check("jira: 4 stage comments", fake.state.issues[0]?.comments.length === 4, String(fake.state.issues[0]?.comments.length));
  await notify(incidentId, "resolved", "duplicate resolved notice");
  check("re-notify delivered event: no new Slack message or Jira comment", fake.state.slack.length === 5 && fake.state.issues[0]?.comments.length === 4);
  // Simulate a crash mid-send: Atlas says "sending" but Jira already has the issue/comment.
  await d.collection(C.deliveries).updateMany({ incidentId, pluginId: "jira", event: { $in: ["opened", "approved"] } }, { $set: { state: "sending" } });
  await notify(incidentId, "opened", "retry opened");
  await notify(incidentId, "approved", "retry approved");
  check("interrupted Jira sends: verified in Jira, nothing duplicated", fake.state.creates === 1 && fake.state.issues[0]?.comments.length === 4);
  const del = await d.collection(C.deliveries).find({ incidentId, pluginId: "jira", event: { $in: ["opened", "approved"] } }).toArray();
  check("interrupted Jira sends marked delivered after verification", del.every((x) => x.state === "delivered"), del.map((x) => x.detail).join(" | "));

  console.log(`\nIntegrations in this run: vector=${inv.similar.mode}${inv.similar.reason ? ` (${inv.similar.reason})` : ""}; explanation=${inv.explanation.source}; embeddings=${inv.similar.embeddingSource}`);
} catch (e) {
  failures++;
  console.error("SMOKE ERROR:", e);
} finally {
  await cleanup();
}
console.log(`\n${failures ? `SMOKE FAILED (${failures})` : `SMOKE PASSED (${results.length} checks)`}`);
process.exit(failures ? 1 : 0);
