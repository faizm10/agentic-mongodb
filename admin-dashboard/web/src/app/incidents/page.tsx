"use client";

import { IncidentList } from "@/components/incidents";
import { ErrorPanel, PageHeader, Shell, Skeleton } from "@/components/ui";
import { type Incident, usePoll } from "@/lib/api";

export default function Incidents() {
  const { data, meta, error, updatedAt, reload } = usePoll<Incident[]>("/api/v1/incidents?limit=100");
  return (
    <Shell meta={meta} updatedAt={updatedAt} error={error}>
      <PageHeader title="Incidents" sub="Every incident the uptime monitor recorded, newest first." />
      {!data && error ? <ErrorPanel error={error} onRetry={reload} />
        : !data ? <Skeleton className="h-72" />
        : (
          <div className="space-y-8">
            <IncidentList title="Active" incidents={data.filter((i) => i.state === "open")} empty="No active incidents." />
            <IncidentList title="Resolved" incidents={data.filter((i) => i.state !== "open")} empty="No resolved incidents yet." />
          </div>
        )}
    </Shell>
  );
}
