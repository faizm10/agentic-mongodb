"use client";

import Link from "next/link";
import { useState } from "react";
import { ArrowUpRight, Bot, Clock3, GitPullRequest } from "lucide-react";
import type { AgentActivity } from "@/lib/api";
import { ago, clock, useNow } from "@/lib/format";

const STAGES: Record<string, string> = {
  detected: "Noticed the problem",
  memory_retrieved: "Looked at past fixes",
  patch_verified: "Wrote and tested a fix",
  pr_intent_saved: "Preparing the fix for review",
  pr_confirmed: "Sent the fix for review",
  memory_saved: "Saved what it learned",
};

const STATES: Record<AgentActivity["state"], { label: string; color: string; message: string }> = {
  unavailable: {
    label: "Not connected",
    color: "bg-warn",
    message: "The AI agent isn't connected, so it can't help with this one.",
  },
  waiting: {
    label: "Waiting",
    color: "bg-warn",
    message: "The AI agent hasn't started on this yet. It usually picks it up within a minute.",
  },
  working: {
    label: "Working",
    color: "bg-accent",
    message: "The AI agent is looking into the problem and working on a fix.",
  },
  awaiting_review: {
    label: "Review needed",
    color: "bg-accent",
    message: "The AI agent made a fix. A person needs to review and merge it on GitHub.",
  },
  needs_attention: {
    label: "Needs attention",
    color: "bg-crit",
    message: "The AI agent couldn't fix this on its own. A person needs to take a look.",
  },
  finished: {
    label: "Fix merged",
    color: "bg-ok",
    message: "The fix was merged on GitHub.",
  },
};

function latestAction(activity: AgentActivity): string {
  const action = activity.latest_action;
  if (!action) return "Nothing yet";
  const stage = STAGES[action.stage] ?? "Working";
  if (action.outcome === "completed") return stage;
  if (action.outcome === "started") return `${stage} (in progress)`;
  if (action.outcome === "failed") return `${stage} (didn't work, needs another try)`;
  if (action.outcome === "recovered") return `${stage} (picked up again)`;
  return "Paused";
}

/** Once the agent's PR is merged, ask a person whether it actually fixed the problem. */
function FixConfirmation({ item, onChange }: { item: AgentActivity; onChange: () => void }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  async function answer(resolved: boolean) {
    setPending(true);
    setError("");
    try {
      const response = await fetch(`/api/v1/incidents/${encodeURIComponent(item.incident_id)}/fix-confirmation`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resolved }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setError(body?.error?.message ?? "Could not save your answer. Try again.");
        return;
      }
      onChange();
    } catch {
      setError("Could not reach the uptime API.");
    } finally {
      setPending(false);
    }
  }

  const buttons = (yes: string, no?: string) => (
    <div className="mt-3 flex flex-wrap gap-2">
      <button type="button" disabled={pending} onClick={() => answer(true)}
        className="btn btn-ok">
        {yes}
      </button>
      {no && (
        <button type="button" disabled={pending} onClick={() => answer(false)}
          className="btn btn-danger">
          {no}
        </button>
      )}
    </div>
  );

  return (
    <div className="mt-4 rounded-lg border border-line bg-surface/60 p-3 text-sm" role="group" aria-label="Confirm the fix">
      {item.fix_confirmation === "confirmed" ? (
        <p className="text-muted">Thanks! The fix is being applied to the running app. The status turns green after the next check.</p>
      ) : item.fix_confirmation === "disputed" ? (
        <>
          <p className="text-muted">Noted: the problem is still happening. Ask the assistant what to try next, or check the PR.</p>
          {buttons("It's fixed now")}
        </>
      ) : (
        <>
          <p className="font-medium text-ink">Did this fix the problem?</p>
          <p className="mt-0.5 text-xs text-muted">Choosing Yes applies the merged fix to the running app and checks it again.</p>
          {buttons("Yes, it's fixed", "No, still broken")}
        </>
      )}
      {error && <p className="mt-2 text-xs text-crit" role="alert">{error}</p>}
    </div>
  );
}

export function AgentActivityPanel({ activity, onChange }: { activity: AgentActivity[]; onChange: () => void }) {
  const now = useNow();
  if (activity.length === 0) return null;

  return (
    <section className="panel p-5 sm:p-6" aria-labelledby="agent-activity-title">
      <div className="flex flex-wrap items-center gap-2">
        <Bot className="h-4 w-4 text-accent" />
        <h2 id="agent-activity-title" className="text-sm font-semibold">AI repair agent</h2>
        <span className="ml-auto text-xs text-faint">Updates every 10 seconds</span>
      </div>
      <p className="mt-1 text-xs text-muted">What the AI agent is doing about each current problem.</p>
      <div className="mt-5 grid gap-4 lg:grid-cols-2">
        {activity.map((item) => {
          const state = STATES[item.state];
          return (
            <article key={item.incident_id} className="card p-4" aria-live="polite">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="font-semibold text-ink">{item.service_name}</h3>
                <span className="ml-auto badge tone-neutral">
                  <span className={`h-1.5 w-1.5 rounded-full ${state.color}`} /> {state.label}
                </span>
              </div>
              <p className="mt-2 text-sm text-muted">{state.message}</p>
              {item.state === "finished" && <FixConfirmation item={item} onChange={onChange} />}
              {(item.stage || item.latest_action || item.started_at || item.updated_at) && (
                <dl className="mt-4 grid gap-3 border-t border-line pt-4 text-sm sm:grid-cols-2">
                  <div>
                    <dt className="label">Progress so far</dt>
                    <dd className="mt-1 text-ink">{item.stage ? STAGES[item.stage] ?? "Working" : "Not started"}</dd>
                  </div>
                  <div>
                    <dt className="label">Latest step</dt>
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
                <Link href={`/ai-agent/${encodeURIComponent(item.incident_id)}`} className="text-accent hover:underline">
                  Watch walkthrough
                </Link>
                <Link href={`/incidents/${encodeURIComponent(item.incident_id)}`} className="text-accent hover:underline">
                  View incident
                </Link>
                {item.pr_url && (
                  <a href={item.pr_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">
                    <GitPullRequest className="h-3.5 w-3.5" /> See the fix (PR) <ArrowUpRight className="h-3 w-3" />
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
