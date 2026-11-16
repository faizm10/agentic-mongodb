"use client";

import { DependenciesPanel } from "@/components/dependencies";
import { OutageControls } from "@/components/outage-controls";
import { ErrorPanel, PageHeader, Shell, Skeleton } from "@/components/ui";
import { type Summary, usePoll } from "@/lib/api";

export default function Demo() {
  const { data, meta, error, updatedAt, reload } = usePoll<Summary>("/api/v1/summary?window=24h");
  return (
    <Shell meta={meta} updatedAt={updatedAt} error={Boolean(error)}>
      <PageHeader title="Demo controls" sub="Break an app the way real outages happen, then restore it." />
      {!data && error ? <ErrorPanel message={error.message} onRetry={reload} />
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
