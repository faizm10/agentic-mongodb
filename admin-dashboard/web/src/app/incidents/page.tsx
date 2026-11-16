"use client";

import { IncidentTimeline } from "@/components/incidents";
import { ErrorPanel, PageHeader, Shell, Skeleton } from "@/components/ui";
import { type Incident, usePoll } from "@/lib/api";

export default function Incidents() {
  const { data, meta, error, updatedAt, reload } = usePoll<Incident[]>("/api/v1/incidents?limit=100");
  return (
    <Shell meta={meta} updatedAt={updatedAt} error={Boolean(error)}>
      <PageHeader title="Incidents" sub="Every outage the uptime monitor recorded, newest first." />
      {!data && error ? <ErrorPanel message={error.message} onRetry={reload} />
        : !data ? <Skeleton className="h-72" />
        : <IncidentTimeline active={data.filter((i) => i.state === "open")} recent={data.filter((i) => i.state !== "open")} />}
    </Shell>
  );
}
