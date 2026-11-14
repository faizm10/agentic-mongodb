// Recovery worker process: `npm run worker -- --incident <id> [--worker <id>]`
import "../lib/load-env";
import { db, C, closeMongo } from "../lib/db";
import { logEvent } from "../lib/events";
import { runRecovery } from "../lib/recovery";
import { newWorkerId } from "../lib/supervisor";

function arg(name: string) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

async function main() {
  const incidentId = arg("incident");
  if (!incidentId) throw new Error("--incident is required");
  const workerId = arg("worker") ?? newWorkerId();
  const d = await db();
  await d.collection<{ _id: string }>(C.workers).updateOne({ _id: workerId }, { $set: { incidentId, pid: process.pid, startedAt: new Date() } }, { upsert: true });

  // Consume the fault toggle atomically so only this worker crashes.
  const fault = await d
    .collection<{ _id: string; armed: boolean }>(C.controls)
    .findOneAndUpdate({ _id: "fault_crash_after_grant", armed: true }, { $set: { armed: false, consumedBy: workerId, consumedAt: new Date() } });
  if (fault) await logEvent(incidentId, "worker", `Fault injection armed for ${workerId}: will SIGKILL right after a sandbox grant commits`, undefined, workerId);

  const result = await runRecovery(incidentId, workerId, {
    afterGrantCommitted: (hireId) => {
      if (!fault) return;
      console.log(`[${workerId}] FAULT: grant for ${hireId} committed in sandbox; SIGKILL before recording success`);
      process.kill(process.pid, "SIGKILL");
    },
  });
  await d.collection<{ _id: string }>(C.workers).updateOne({ _id: workerId }, { $set: { exitedAt: new Date(), exitCode: 0, outcome: result.resolved ? "resolved" : "incomplete" } });
  console.log(`[${workerId}] done`, result);
}

main()
  .then(() => closeMongo())
  .then(() => process.exit(0))
  .catch(async (e) => {
    console.error(e);
    await closeMongo().catch(() => {});
    process.exit(1);
  });
