// Starts a real local mongod (mongodb-memory-server) when MONGODB_URI is unset.
// This is NOT Atlas: no Vector Search. OpsRelay labels that clearly.
import fs from "node:fs";
import { MongoMemoryServer } from "mongodb-memory-server";

export async function ensureMongo(opts: { persistent: boolean; port?: number }) {
  if (process.env.MONGODB_URI) return { uri: process.env.MONGODB_URI, stop: async () => {} };
  const instance: Record<string, unknown> = { port: opts.port, storageEngine: "wiredTiger" };
  if (opts.persistent) {
    fs.mkdirSync(".data/mongo", { recursive: true });
    instance.dbPath = ".data/mongo";
  }
  const server = await MongoMemoryServer.create({ instance });
  process.env.MONGODB_URI = server.getUri();
  console.log(`[mongo] MONGODB_URI not set → local mongod at ${process.env.MONGODB_URI} (not Atlas; Vector Search falls back)`);
  return { uri: process.env.MONGODB_URI, stop: () => server.stop({ doCleanup: !opts.persistent }) };
}
