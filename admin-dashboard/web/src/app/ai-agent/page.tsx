"use client";

import { Bot } from "lucide-react";
import { AgentActivityPanel } from "@/components/agent-activity";
import { LearningPanel } from "@/components/learning";
import { ErrorPanel, PageHeader, Shell, Skeleton } from "@/components/ui";
import { type Summary, usePoll } from "@/lib/api";

export default function Agent() {
  const { data, meta, error, updatedAt, reload } = usePoll<Summary>("/api/v1/summary?window=24h");
  return (
    <Shell meta={meta} updatedAt={updatedAt} error={Boolean(error)}>
      <PageHeader title="AI agent" sub="What the repair agent is doing about current problems, and what it has learned." />
      {!data && error ? <ErrorPanel message={error.message} onRetry={reload} />
        : !data ? <Skeleton className="h-56" />
        : (
          <div className="space-y-6">
            {data.agent_activity.length ? <AgentActivityPanel activity={data.agent_activity} onChange={reload} /> : (
              <div className="panel flex items-center gap-3 p-6 text-sm text-muted">
                <Bot className="h-5 w-5 text-faint" /> No current problems for the agent to work on.
              </div>
            )}
            <LearningPanel />
          </div>
        )}
    </Shell>
  );
}
