export type HireStatus = "pending" | "complete";

export interface Hire {
  _id: string;
  name: string;
  email: string;
  githubLogin: string;
  status: HireStatus;
  synthetic?: boolean;
  seeded?: boolean;
  completedAt?: Date;
  completedVia?: string;
}

export type ProposedAction = "restore_mapping" | "restart_worker" | "human_review";

export interface Approval {
  action: "restore_mapping";
  mappingKey: string;
  fromTeamId: string;
  approvedTeamId: string;
  hireIds: string[];
  approvedBy: string;
  approvedAt: Date;
}

export interface Incident {
  _id: string;
  title: string;
  serviceId: string;
  status: "open" | "investigating" | "awaiting_approval" | "needs_human" | "recovering" | "monitoring" | "resolved";
  openedAt: Date;
  closedAt?: Date;
  investigation?: Investigation;
  approval?: Approval;
  lease?: { workerId: string; expiresAt: Date };
}

export interface Signals {
  teamNotFoundErrors: number;
  timeoutErrors: number;
  recentMappingChange: boolean;
  currentTeamId: string;
  approvedTeamId: string;
  mappingDiffersFromApproved: boolean;
  workerHeartbeatAgeSec: number | null;
  workerHealthy: boolean;
  portalUp: boolean;
}

export interface SimilarIncident {
  _id: string;
  title: string;
  resolution: string;
  score: number;
}

export interface Investigation {
  at: Date;
  signals: Signals;
  evidence: {
    errors: { hireId: string; teamId: string; status: number; error: string; at: Date }[];
    heartbeat: { workerId: string; lastBeatAt: Date; queueDepth: number; seeded: boolean } | null;
    configChanges: { key: string; from: string; to: string; by: string; at: Date; note?: string }[];
  };
  similar: { mode: "atlas_vector_search" | "fallback_cosine"; reason?: string; embeddingSource: string; query: string; results: SimilarIncident[] };
  decision: { action: ProposedAction; rule: string; reasons: string[] };
  explanation: { source: "openai" | "fallback_template"; model?: string; text: string; llmRecommendedAction?: ProposedAction; error?: string };
}

export type CheckpointState = "pending" | "intent" | "sent" | "verified" | "complete" | "failed";
