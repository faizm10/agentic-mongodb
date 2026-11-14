import OpenAI from "openai";
import { env } from "./env";
import { recordIntegration } from "./events";

export interface Embedding { vector: number[]; source: string }

const FALLBACK_DIMS = 256;

export function embeddingSource() {
  return env.openaiKey ? `openai:${env.embeddingModel}` : `fallback:hashed-bow-${FALLBACK_DIMS}`;
}

// Deterministic bag-of-words hashing. Only used when OPENAI_API_KEY is missing,
// and always labeled as a fallback in the UI.
function hashedEmbedding(text: string): number[] {
  const v = new Array(FALLBACK_DIMS).fill(0);
  for (const tok of text.toLowerCase().match(/[a-z0-9]+/g) || []) {
    let h = 2166136261;
    for (const ch of tok) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
    v[Math.abs(h) % FALLBACK_DIMS] += 1;
  }
  const n = Math.hypot(...v) || 1;
  return v.map((x) => x / n);
}

export async function embed(texts: string[]): Promise<Embedding[]> {
  if (!env.openaiKey) return texts.map((t) => ({ vector: hashedEmbedding(t), source: embeddingSource() }));
  try {
    const client = new OpenAI({ apiKey: env.openaiKey });
    const r = await client.embeddings.create({ model: env.embeddingModel, input: texts });
    await recordIntegration("openai", true, `embeddings ok (${env.embeddingModel}, ${r.data[0]?.embedding.length} dims)`);
    return r.data.map((d) => ({ vector: d.embedding, source: embeddingSource() }));
  } catch (e) {
    await recordIntegration("openai", false, `embeddings failed: ${(e as Error).message}`);
    throw e;
  }
}

export function cosine(a: number[], b: number[]) {
  let dot = 0, na = 0, nb = 0;
  for (let i = 0; i < a.length; i++) { dot += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i]; }
  return dot / (Math.sqrt(na) * Math.sqrt(nb) || 1);
}
