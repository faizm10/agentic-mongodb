import { MongoClient, type Db } from "mongodb";
import { env } from "./env";

const g = globalThis as unknown as { __opsrelayMongo?: Promise<MongoClient> };

export function mongoClient(): Promise<MongoClient> {
  if (!env.mongoUri) {
    throw new Error("MONGODB_URI is not set. Run `npm run dev` (starts a local mongod) or set an Atlas URI in .env.local.");
  }
  if (!g.__opsrelayMongo) {
    g.__opsrelayMongo = new MongoClient(env.mongoUri, {
      appName: "opsrelay",
      writeConcern: { w: "majority" },
      readConcern: { level: "majority" },
      serverSelectionTimeoutMS: 8000,
    }).connect();
  }
  return g.__opsrelayMongo;
}

export async function db(): Promise<Db> {
  return (await mongoClient()).db(env.mongoDb);
}

export async function closeMongo() {
  if (g.__opsrelayMongo) {
    await (await g.__opsrelayMongo).close();
    g.__opsrelayMongo = undefined;
  }
}

// Collection names in the OpsRelay database. Sandbox collections live in a
// different database and are only touched by sandbox/server.ts.
export const C = {
  hires: "hires",
  config: "config",
  approvedMappings: "approved_mappings",
  configChanges: "config_changes",
  provisioningAttempts: "provisioning_attempts",
  workerHeartbeats: "worker_heartbeats",
  incidents: "incidents",
  events: "incident_events",
  checkpoints: "recovery_checkpoints",
  workers: "recovery_workers",
  pastIncidents: "past_incidents",
  checks: "checks",
  controls: "controls",
  integrationLog: "integration_log",
  deliveries: "plugin_deliveries",
} as const;
