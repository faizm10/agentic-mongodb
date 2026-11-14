// Server-only configuration. Never import this from a client component.
export const env = {
  mongoUri: process.env.MONGODB_URI || "",
  mongoDb: process.env.MONGODB_DB || "opsrelay",
  openaiKey: process.env.OPENAI_API_KEY || "",
  openaiModel: process.env.OPENAI_MODEL || "gpt-4.1-mini",
  embeddingModel: process.env.OPENAI_EMBEDDING_MODEL || "text-embedding-3-small",
  githubOrg: process.env.GITHUB_ORG || "",
  githubToken: process.env.GITHUB_TOKEN || "",
  appUrl: process.env.APP_URL || "http://localhost:3000",
  sandboxUrl: process.env.SANDBOX_URL || "http://localhost:4010",
};

export const isAtlasUri = (uri: string) => /mongodb\+srv:\/\/|\.mongodb\.net/.test(uri);

// The sandbox (stand-in for GitHub team membership) keeps its own database,
// separate from OpsRelay's recovery checkpoints.
export const sandboxDbName = () => `${env.mongoDb}_sandbox`;
