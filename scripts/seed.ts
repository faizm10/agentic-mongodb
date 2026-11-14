// Re-seed the demo (requires the sandbox to be running: `npm run dev` does this).
import "../lib/load-env";
const variant = (process.argv[2] as "config-drift" | "stalled-worker") || "config-drift";
const { resetDemo } = await import("../lib/seed");
const { closeMongo } = await import("../lib/db");
console.log(await resetDemo(variant));
await closeMongo();
