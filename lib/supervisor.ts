// Launches recovery workers as separate OS processes so a crash kills only the
// worker, never the web app. Records each worker's exit in Atlas.
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { db, C } from "./db";
import { logEvent } from "./events";
import { notify } from "./plugins";

export function newWorkerId() {
  return `w-${randomBytes(3).toString("hex")}`;
}

export async function launchWorker(incidentId: string, launchedBy: string) {
  const workerId = newWorkerId();
  const root = process.cwd();
  const logDir = path.join(root, ".data", "logs");
  fs.mkdirSync(logDir, { recursive: true });
  const out = fs.openSync(path.join(logDir, `${workerId}.log`), "a");
  const child = spawn(process.execPath, ["--import", "tsx", path.join(root, "worker", "recovery.ts"), "--incident", incidentId, "--worker", workerId], {
    cwd: root,
    env: process.env,
    stdio: ["ignore", out, out],
  });
  await (await db()).collection<{ _id: string }>(C.workers).insertOne({ _id: workerId, incidentId, pid: child.pid, launchedBy, launchedAt: new Date() } as any);
  await logEvent(incidentId, "worker", `Launched worker ${workerId} (pid ${child.pid}) by ${launchedBy}`, undefined, workerId);

  const exited = new Promise<{ code: number | null; signal: NodeJS.Signals | null }>((resolve) => {
    child.on("exit", async (code, signal) => {
      fs.closeSync(out);
      try {
        await (await db()).collection<{ _id: string }>(C.workers).updateOne({ _id: workerId, exitedAt: { $exists: false } }, { $set: { exitedAt: new Date(), exitCode: code, signal } });
        if (signal || code) {
          const cp = await (await db()).collection(C.checkpoints).findOne({ _id: incidentId as any });
          const states = Object.fromEntries(Object.entries(cp?.steps || {}).map(([k, v]: any) => [k, v.state]));
          const summary = Object.entries(states).map(([k, v]) => `${k.replace("hire:", "")} ${v}`).join(", ");
          await notify(incidentId, "crashed", `Recovery worker ${workerId} crashed (${signal ?? `exit ${code}`}). Progress is saved in Atlas: ${summary || "nothing yet"}.`);
          await logEvent(incidentId, "crash", `Worker ${workerId} died (${signal ?? `exit ${code}`}). Web app still running. Atlas checkpoint: ${summary || "empty"}`, { states }, workerId);
        }
      } finally {
        resolve({ code, signal });
      }
    });
  });
  return { workerId, pid: child.pid, exited };
}
