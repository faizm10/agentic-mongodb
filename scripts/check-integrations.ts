// Exercises each configured integration for real and prints what is verified.
import "../lib/load-env";
const { env } = await import("../lib/env");
const { closeMongo } = await import("../lib/db");
const { integrationStatus } = await import("../lib/integrations");
const { findSimilarIncidents } = await import("../lib/vector");
const { discoverRepos } = await import("../lib/catalog");
const { embed } = await import("../lib/embeddings");

if (!env.mongoUri) {
  console.error("MONGODB_URI not set. Set an Atlas URI in .env.local (or run checks while `npm run dev` is up with MONGODB_URI=mongodb://127.0.0.1:27027/).");
  process.exit(2);
}
if (env.openaiKey) await embed(["opsrelay integration probe"]).catch((e) => console.log("openai embed error:", e.message));
await findSimilarIncidents("onboarding stuck pending github provisioning").catch((e) => console.log("vector error:", e.message));
const gh = await discoverRepos();
console.log(`github catalog: ${gh.detail}`);
for (const row of await integrationStatus()) console.log(`${row.status.padEnd(22)} ${row.name.padEnd(36)} ${row.detail}`);
await closeMongo();
