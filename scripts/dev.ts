// One command for local dev: local mongod (if no MONGODB_URI) + sandbox API + Next.js.
import "../lib/load-env";
import { spawn } from "node:child_process";
import path from "node:path";
import { ensureMongo } from "./local-mongo";
import { startSandbox, waitFor } from "./proc";

const mongo = await ensureMongo({ persistent: true, port: 27027 });
const sandboxUrl = process.env.SANDBOX_URL || "http://localhost:4010";
const sb = startSandbox();
await waitFor(`${sandboxUrl}/health`);

const next = spawn(process.execPath, [path.join("node_modules", "next", "dist", "bin", "next"), "dev"], { env: process.env, stdio: "inherit" });

// Seed after Next is up so the seed's real portal check can reach /portal.
await waitFor(`${process.env.APP_URL || "http://localhost:3000"}/portal`, 60_000);
const { db, C, closeMongo } = await import("../lib/db");
if (!(await (await db()).collection(C.incidents).countDocuments())) {
  const { resetDemo } = await import("../lib/seed");
  console.log("[dev] empty database → seeding demo", await resetDemo());
}
await closeMongo();

const shutdown = async () => {
  next.kill("SIGTERM");
  sb.kill("SIGTERM");
  await mongo.stop();
  process.exit(0);
};
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
next.on("exit", shutdown);
