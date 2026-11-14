"use client";

import Link from "next/link";
import { ArrowUpRight, Bot, Clock3, GitPullRequest } from "lucide-react";
import type { AgentActivity } from "@/lib/api";
import { ago, clock, useNow } from "@/lib/format";

const STAGES: Record<string, string> = {
  detected: "Incident detected",
  memory_retrieved: "Repair context retrieved",
  patch_verified: "Fix tested",
  pr_intent_saved: "Pull request prepared",
  pr_confirmed: "Pull request opened",
  memory_saved: "Repair recorded",
};

const STATES: Record<AgentActivity["state"], { label: string; color: string; message: string }> = {
  unavailable: {
    label: "Not connected",
    color: "bg-warn",
    message: "Agent progress requires MongoDB and a running repair agent.",
  },
  waiting: {
    label: "Waiting",
    color: "bg-warn",
    message: "No active agent run is linked yet, or the agent is waiting to resume.",
  },
  working: {
    label: "Working",
    color: "bg-accent",
    message: "The agent is processing this incident.",
  },
  awaiting_review: {
    label: "Review needed",
    color: "bg-accent",
    message: "The agent opened a fix for human review.",
  },
  needs_attention: {
    label: "Needs attention",
    color: "bg-crit",
    message: "The agent stopped and needs a person to take over.",
  },
  finished: {
    label: "Finished",
    color: "bg-ok",
    message: "The agent finished its work on this incident.",
  },
};

function latestAction(activity: AgentActivity): string {
  const action = activity.latest_action;
  if (!action) return "No agent action recorded yet";
  const stage = STAGES[action.stage] ?? "Agent step";
  if (action.outcome === "completed") return stage;
  if (action.outcome === "started") return `${stage} started`;
  if (action.outcome === "failed") return `${stage} needs another attempt`;
  if (action.outcome === "recovered") return `${stage} resumed`;
  return "Agent paused";
}

export function AgentActivityPanel({ activity }: { activity: AgentActivity[] }) {
  const now = useNow();
  if (activity.length === 0) return null;

  return (
    <section className="panel p-5 sm:p-6" aria-labelledby="agent-activity-title">
      <div className="flex flex-wrap items-center gap-2">
        <Bot className="h-4 w-4 text-accent" />
        <h2 id="agent-activity-title" className="text-sm font-semibold">AI repair agent</h2>
        <span className="ml-auto text-xs text-faint">Updates every 10 seconds</span>
      </div>
      <p className="mt-1 text-xs text-muted">Progress for active incidents, as reported by the agent.</p>
      <div className="mt-5 grid gap-4 lg:grid-cols-2">
        {activity.map((item) => {
          const state = STATES[item.state];
          return (
            <article key={item.incident_id} className="rounded-xl border border-line-strong bg-raised/50 p-4" aria-live="polite">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="font-semibold text-ink">{item.service_name}</h3>
                <span className="ml-auto inline-flex items-center gap-1.5 rounded-full border border-line px-2.5 py-1 text-xs font-medium text-muted">
                  <span className={`h-1.5 w-1.5 rounded-full ${state.color}`} /> {state.label}
                </span>
              </div>
              <p className="mt-2 text-sm text-muted">{state.message}</p>
              {(item.stage || item.latest_action || item.started_at || item.updated_at) && (
                <dl className="mt-4 grid gap-3 border-t border-line pt-4 text-sm sm:grid-cols-2">
                  <div>
                    <dt className="label">Last verified stage</dt>
                    <dd className="mt-1 text-ink">{item.stage ? STAGES[item.stage] ?? "Agent step" : "Not started"}</dd>
                  </div>
                  <div>
                    <dt className="label">Latest action</dt>
                    <dd className="mt-1 text-ink">{latestAction(item)}</dd>
                    {item.latest_action && <dd className="mt-0.5 text-xs text-faint">{clock(item.latest_action.at, true)}</dd>}
                  </div>
                  <div>
                    <dt className="label">Started</dt>
                    <dd className="mt-1 text-ink">{clock(item.started_at, true)}</dd>
                  </div>
                  <div>
                    <dt className="label">Last update</dt>
                    <dd className="mt-1 inline-flex items-center gap-1.5 text-ink">
                      <Clock3 className="h-3.5 w-3.5 text-faint" /> {item.updated_at ? ago(item.updated_at, now) : "—"}
                    </dd>
                  </div>
                </dl>
              )}
              <div className="mt-4 flex flex-wrap items-center gap-4 border-t border-line pt-3 text-xs">
                <Link href={`/incidents/${encodeURIComponent(item.incident_id)}`} className="text-accent hover:underline">
                  View incident
                </Link>
                {item.pr_url && (
                  <a href={item.pr_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">
                    <GitPullRequest className="h-3.5 w-3.5" /> Repair PR <ArrowUpRight className="h-3 w-3" />
                  </a>
                )}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
