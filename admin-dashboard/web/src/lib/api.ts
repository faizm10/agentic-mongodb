"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type Status = "operational" | "degraded" | "down" | "unknown";
export type Window = "24h" | "7d" | "30d";

export type StorageInfo = { backend: "sqlite" | "mongodb"; label: string; persistent: boolean; atlas: boolean; location: string };
export type Meta = { generated_at: string; storage: StorageInfo; version: string };

export type UptimeStats = {
  window: Window;
  window_start: string;
  window_end: string;
  window_seconds: number;
  observed_seconds: number;
  operational_seconds: number;
  degraded_seconds: number;
  down_seconds: number;
  unknown_seconds: number;
  coverage_pct: number;
  sufficient: boolean;
  uptime_pct: number | null;
  availability_pct: number | null;
  check_count: number;
  failed_check_count: number;
  avg_latency_ms: number | null;
  p95_latency_ms: number | null;
  avg_functional_latency_ms: number | null;
  monitoring_started_at: string | null;
};

export type Bar = {
  start: string;
  end: string;
  status: Status;
  uptime_pct: number | null;
  coverage_pct: number;
  operational_seconds: number;
  degraded_seconds: number;
  down_seconds: number;
  checks: number;
  failed_checks: number;
};

export type Current = {
  status: Status;
  summary: string;
  since: string | null;
  last_checked_at: string | null;
  last_success_at: string | null;
  stale: boolean;
  failure_type: string | null;
  open_incident_id: string | null;
};

export type ServiceOverview = { id: string; name: string; app: string; description: string; current: Current; uptime: UptimeStats; bars: Bar[] | null };

export type IncidentEvent = { at: string; type: string; status: Status | null; message: string; check_id: string | null };
export type Incident = {
  id: string;
  service_id: string;
  service_name: string;
  app: string;
  state: "open" | "resolved";
  severity: "degraded" | "down";
  peak_severity: "degraded" | "down";
  failure_type: string | null;
  investigation_route: "service_health" | "investigate";
  title: string;
  summary: string;
  started_at: string;
  last_failure_at: string;
  resolved_at: string | null;
  duration_seconds: number;
  failed_check_count: number;
  first_check_id: string;
  last_check_id: string;
  recovery_check_id: string | null;
  evidence: Record<string, unknown> & { route_reason?: string };
  events: IncidentEvent[];
  links: { kind: string; url: string; label: string; source: string }[];
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  related_ci: { id: string; status: string; stage: string; run_url: string | null; pr_url: string | null; commit_sha: string | null; created_at: string }[];
};

export type Worker = {
  owner: string | null;
  lease_expires_at: string | null;
  started_at: string | null;
  heartbeat_at: string | null;
  last_cycle_started_at: string | null;
  last_cycle_finished_at: string | null;
  last_cycle_duration_ms: number | null;
  cycles_completed: number;
  skipped_slots: number;
  duplicate_slots: number;
  interval_seconds: number;
};

export type Monitoring = {
  status: "running" | "stale" | "not_started";
  message: string;
  interval_seconds: number;
  worker: Worker | null;
  lease_held: boolean;
  next_cycle_expected_at: string | null;
  total_checks: number;
  storage: StorageInfo;
};

export type AgentActivity = {
  incident_id: string;
  service_name: string;
  state: "unavailable" | "waiting" | "working" | "awaiting_review" | "needs_attention" | "finished";
  stage: string | null;
  started_at: string | null;
  updated_at: string | null;
  latest_action: { stage: string; outcome: string; at: string } | null;
  pr_url: string | null;
};

export type Summary = {
  overall: { status: Status; label: string; affected_services: string[] };
  window: Window;
  services: ServiceOverview[];
  active_incidents: Incident[];
  recent_incidents: Incident[];
  monitoring: Monitoring;
  agent_activity: AgentActivity[];
};

export type Probe = {
  kind: "frontend" | "availability" | "functional";
  url: string;
  ok: boolean;
  http_status: number | null;
  latency_ms: number | null;
  error_kind: string | null;
  error: string | null;
  details: {
    status?: string;
    demo_outage?: boolean;
    summary?: string;
    failed_checks?: string[];
    failures?: { check: string; component?: string; reason: string; source?: string | null; expected?: unknown; actual?: unknown }[];
  } & Record<string, unknown>;
};

export type Check = {
  id: string;
  service_id: string;
  checked_at: string;
  scheduled: boolean;
  status: Status;
  failure_type: string | null;
  summary: string;
  latency_ms: number | null;
  functional_latency_ms: number | null;
  frontend: Probe | null;
  availability: Probe | null;
  functional: Probe | null;
  interval_seconds: number;
  worker_id: string;
};

export type ServiceConfig = {
  id: string;
  name: string;
  app: string;
  description: string;
  frontend_url: string;
  backend_url: string;
  availability_path: string;
  functional_path: string;
  functional_method: string;
  interval_seconds: number;
  timeout_seconds: number;
  functional_timeout_seconds: number;
  failure_threshold: number;
  recovery_threshold: number;
  enabled: boolean;
};

export type ServiceDetail = { service: ServiceConfig; current: Current; uptime: Record<Window, UptimeStats>; incidents: Incident[]; incident_ids: string[] };

export function isDemoIncident(incident: Incident): boolean {
  const latest = incident.evidence.latest_failure as { probes?: { functional?: { details?: { demo_outage?: boolean } } } } | undefined;
  return latest?.probes?.functional?.details?.demo_outage === true;
}
export type LatencyPoint = { start: string; availability_avg_ms: number | null; availability_max_ms: number | null; functional_avg_ms: number | null; functional_max_ms: number | null; samples: number };

type Envelope<T> = { data: T; meta: Meta };

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function getJson<T>(path: string): Promise<Envelope<T>> {
  let res: Response;
  try {
    res = await fetch(path, { cache: "no-store" });
  } catch {
    throw new ApiError(0, "Cannot reach the OpsRelay uptime API.");
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, body?.error?.message ?? `HTTP ${res.status}`);
  return body as Envelope<T>;
}

export const POLL_MS = 10_000;

/** Poll an endpoint without page reloads. Keeps the last good data while a refresh is in flight or failing. */
export function usePoll<T>(path: string | null, intervalMs = POLL_MS) {
  const [data, setData] = useState<T | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const pathRef = useRef(path);
  pathRef.current = path;

  const load = useCallback(async () => {
    const current = pathRef.current;
    if (!current) return;
    try {
      const env = await getJson<T>(current);
      if (pathRef.current !== current) return;
      setData(env.data);
      setMeta(env.meta);
      setError(null);
      setUpdatedAt(Date.now());
    } catch (e) {
      if (pathRef.current === current) setError(e as ApiError);
    } finally {
      if (pathRef.current === current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!path) return;
    setLoading(true);
    load();
    const t = window.setInterval(load, intervalMs);
    const onVisible = () => document.visibilityState === "visible" && load();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(t);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [path, intervalMs, load]);

  return { data, meta, error, updatedAt, loading, reload: load };
}
