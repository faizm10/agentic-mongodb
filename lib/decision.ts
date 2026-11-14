import type { ProposedAction, Signals } from "./types";

// Deterministic rules decide; the LLM only explains (and can force human review by disagreeing).
export function decide(s: Signals): { action: ProposedAction; rule: string; reasons: string[] } {
  const reasons: string[] = [];
  if (s.teamNotFoundErrors > 0) reasons.push(`${s.teamNotFoundErrors} provisioning call(s) failed with 404 team not found for team ${s.currentTeamId}`);
  if (s.timeoutErrors > 0) reasons.push(`${s.timeoutErrors} provisioning call(s) timed out`);
  if (s.mappingDiffersFromApproved) reasons.push(`config points at team ${s.currentTeamId}; the approved mapping is ${s.approvedTeamId}`);
  if (s.recentMappingChange) reasons.push("the team mapping changed in the last 24h");
  reasons.push(s.workerHealthy ? `provisioning worker heartbeat is fresh (${s.workerHeartbeatAgeSec}s)` : `provisioning worker heartbeat is stale (${s.workerHeartbeatAgeSec ?? "none"}s)`);

  if (s.teamNotFoundErrors > 0 && s.mappingDiffersFromApproved && s.recentMappingChange && s.workerHealthy) {
    return { action: "restore_mapping", rule: "R1: healthy worker + team-not-found + recent change away from approved mapping", reasons };
  }
  if (!s.workerHealthy && !s.mappingDiffersFromApproved && s.teamNotFoundErrors === 0) {
    return { action: "restart_worker", rule: "R2: stale worker, mapping matches approved, no team errors", reasons };
  }
  return { action: "human_review", rule: "R3: evidence does not fit a single known cause", reasons };
}

export class GuardError extends Error {}

// Application-level enforcement: the recovery worker may only grant the approved team
// to the hires named in the approval, no matter what the config or the LLM says.
export function assertGrantAllowed(teamId: string, hireId: string, approval: { approvedTeamId: string; hireIds: string[] }) {
  if (teamId !== approval.approvedTeamId) throw new GuardError(`blocked: team ${teamId} is not the approved team ${approval.approvedTeamId}`);
  if (!approval.hireIds.includes(hireId)) throw new GuardError(`blocked: hire ${hireId} is not in the approved hire list`);
}
