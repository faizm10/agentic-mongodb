import { db, C } from "./db";
import { env, isAtlasUri } from "./env";
import { cosine, embed } from "./embeddings";
import { recordIntegration } from "./events";
import type { Investigation } from "./types";

export const VECTOR_INDEX = "past_incidents_vector";

export async function vectorIndexStatus(): Promise<{ ok: boolean; detail: string }> {
  if (!isAtlasUri(env.mongoUri)) return { ok: false, detail: "MONGODB_URI is not an Atlas cluster; $vectorSearch unavailable" };
  try {
    const idx = await (await db()).collection(C.pastIncidents).listSearchIndexes(VECTOR_INDEX).toArray();
    if (!idx.length) return { ok: false, detail: `search index "${VECTOR_INDEX}" not found — run npm run atlas:index` };
    const i = idx[0] as { status?: string; queryable?: boolean };
    return i.queryable ? { ok: true, detail: `index ${VECTOR_INDEX} ${i.status}` } : { ok: false, detail: `index ${VECTOR_INDEX} status ${i.status}, not queryable yet` };
  } catch (e) {
    return { ok: false, detail: `listSearchIndexes failed: ${(e as Error).message}` };
  }
}

export async function findSimilarIncidents(query: string): Promise<Investigation["similar"]> {
  const [q] = await embed([query]);
  const col = (await db()).collection(C.pastIncidents);
  const status = await vectorIndexStatus();

  if (status.ok) {
    try {
      const results = await col
        .aggregate([
          { $vectorSearch: { index: VECTOR_INDEX, path: "embedding", queryVector: q.vector, numCandidates: 50, limit: 3, filter: { embeddingSource: q.source } } },
          { $project: { _id: 1, title: 1, resolution: 1, score: { $meta: "vectorSearchScore" } } },
        ])
        .toArray();
      if (results.length) {
        await recordIntegration("atlas_vector_search", true, `$vectorSearch returned ${results.length} results (${status.detail})`);
        return { mode: "atlas_vector_search", embeddingSource: q.source, query, results: results as any };
      }
      status.detail = "$vectorSearch returned 0 results (embedding source/dims may not match the index; re-seed then rerun atlas:index)";
    } catch (e) {
      status.detail = `$vectorSearch failed: ${(e as Error).message}`;
    }
  }

  await recordIntegration("atlas_vector_search", false, status.detail);
  const docs = await col.find({ embeddingSource: q.source }).toArray();
  const results = docs
    .map((d) => ({ _id: String(d._id), title: d.title, resolution: d.resolution, score: cosine(q.vector, d.embedding) }))
    .sort((a, b) => b.score - a.score)
    .slice(0, 3);
  return { mode: "fallback_cosine", reason: status.detail, embeddingSource: q.source, query, results };
}
