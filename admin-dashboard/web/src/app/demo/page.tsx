"use client";

import { DependenciesPanel } from "@/components/dependencies";
import { OutageControls } from "@/components/outage-controls";
import { ErrorPanel, PageHeader, Shell, Skeleton } from "@/components/ui";
import { DEV, type Summary, usePoll } from "@/lib/api";

export default function Development() {
  const { data, meta, error, updatedAt, reload } = usePoll<Summary>(DEV ? "/api/v1/summary?window=24h" : null);
  return (
    <Shell meta={meta} updatedAt={updatedAt} error={error}>
      <PageHeader title="Development" sub="Bug injection, outage simulation and demo checkouts. Development builds only." />
      {!DEV ? <p className="panel px-5 py-4 text-sm text-muted">Development controls are not available in production builds.</p>
        : !data && error ? <ErrorPanel error={error} onRetry={reload} />
        : !data ? <Skeleton className="h-56" />
        : (
          <div className="space-y-6">
            <OutageControls services={data.services} onChange={reload} />
            <DependenciesPanel />
          </div>
        )}
    </Shell>
  );
}
