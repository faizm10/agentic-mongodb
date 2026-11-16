"use client";

import { Database, GitPullRequest, RefreshCw, Sparkles } from "lucide-react";
import { type DependencyCheck, usePoll } from "@/lib/api";
import { ago, useNow } from "@/lib/format";

const STATE = {
  operational: { label: "Connected", dot: "bg-ok" },
  degraded: { label: "Needs attention", dot: "bg-warn" },
  down: { label: "Unavailable", dot: "bg-crit" },
  not_configured: { label: "Not configured", dot: "bg-faint" },
} as const;

const ICON = { github: GitPullRequest, mongodb: Database, openai: Sparkles };

export function DependenciesPanel() {
  const { data, error, reload } = usePoll<DependencyCheck[]>("/api/v1/dependencies", 30_000);
  const now = useNow();

  return (
    <section className="panel p-5 sm:p-6" aria-labelledby="dependencies-title">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="dependencies-title" className="text-sm font-semibold">Agent dependencies</h2>
        <span className="ml-auto text-xs text-faint">Checked every 30 seconds</span>
      </div>
      <p className="mt-1 text-xs text-muted">Connectivity and access checks for services the repair agent needs.</p>
      {error && (
        <div className="mt-4 flex items-center gap-2 rounded-lg border border-crit/25 bg-crit/5 p-3 text-xs text-crit" role="status">
          <span>Dependency checks could not be refreshed.</span>
          <button type="button" onClick={reload} className="ml-auto inline-flex items-center gap-1 hover:underline">
            <RefreshCw className="h-3 w-3" /> Retry
          </button>
        </div>
      )}
      {!data ? (
        <p className="mt-4 text-sm text-muted">Checking dependencies…</p>
      ) : (
        <div className="mt-5 grid gap-3 lg:grid-cols-3">
          {data.map((item) => {
            const state = STATE[item.status];
            const Icon = ICON[item.id];
            return (
              <article key={item.id} className="card p-4">
                <div className="flex items-center gap-2">
                  <Icon className="h-4 w-4 text-muted" aria-hidden="true" />
                  <h3 className="font-medium text-ink">{item.name}</h3>
                </div>
                <p className="mt-3 inline-flex items-center gap-1.5 text-xs font-medium text-muted">
                  <span className={`h-1.5 w-1.5 rounded-full ${state.dot}`} /> {state.label}
                </p>
                <p className="mt-2 text-sm text-ink">{item.summary}</p>
                {item.action && <p className="mt-1 text-xs leading-relaxed text-muted">{item.action}</p>}
                <p className="tabular mt-4 border-t border-line pt-3 text-[11px] text-faint">
                  Checked {ago(item.checked_at, now)}
                  {item.latency_ms !== null && ` · ${Math.round(item.latency_ms)} ms`}
                </p>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
