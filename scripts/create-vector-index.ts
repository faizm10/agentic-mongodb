// Creates (or recreates) the Atlas Vector Search index over past incidents and
// proves it with a real $vectorSearch query.
import "../lib/load-env";
const { env, isAtlasUri } = await import("../lib/env");
const { db, C, closeMongo } = await import("../lib/db");
const { seedPastIncidents } = await import("../lib/seed");
const { VECTOR_INDEX, findSimilarIncidents } = await import("../lib/vector");

if (!isAtlasUri(env.mongoUri)) {
  console.error("MONGODB_URI is not an Atlas cluster (mongodb+srv://… / *.mongodb.net). Vector Search requires Atlas.");
  process.exit(2);
}
const seeded = await seedPastIncidents();
console.log(`embedded ${seeded.count} past incidents with ${seeded.source} (${seeded.dims} dims)`);

const col = (await db()).collection(C.pastIncidents);
const definition = {
  fields: [
    { type: "vector", path: "embedding", numDimensions: seeded.dims, similarity: "cosine" },
    { type: "filter", path: "embeddingSource" },
  ],
};
const existing = (await col.listSearchIndexes(VECTOR_INDEX).toArray())[0] as any;
const existingDims = existing?.latestDefinition?.fields?.find((f: any) => f.type === "vector")?.numDimensions;
if (existing && existingDims !== seeded.dims) {
  console.log(`index exists with ${existingDims} dims; dropping to recreate with ${seeded.dims}`);
  await col.dropSearchIndex(VECTOR_INDEX);
  while ((await col.listSearchIndexes(VECTOR_INDEX).toArray()).length) await new Promise((r) => setTimeout(r, 2000));
}
if (!existing || existingDims !== seeded.dims) {
  await col.createSearchIndex({ name: VECTOR_INDEX, type: "vectorSearch", definition });
  console.log(`created ${VECTOR_INDEX}; waiting for it to become queryable…`);
}
for (let i = 0; i < 90; i++) {
  const idx = (await col.listSearchIndexes(VECTOR_INDEX).toArray())[0] as any;
  if (idx?.queryable) break;
  process.stdout.write(`  status ${idx?.status ?? "?"}\r`);
  await new Promise((r) => setTimeout(r, 2000));
}
const r = await findSimilarIncidents("Onboarding records stuck Pending; GitHub team provisioning failing; portal sign-in works");
console.log(`\nquery mode: ${r.mode}${r.reason ? ` (${r.reason})` : ""}`);
for (const x of r.results) console.log(`  ${x.score.toFixed(4)}  ${x._id}  ${x.title}`);
await closeMongo();
process.exit(r.mode === "atlas_vector_search" ? 0 : 1);
