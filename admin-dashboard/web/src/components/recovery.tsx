import type { Recovery, RecoveryAttempt, RecoveryOutcome } from "@/lib/api";
import { clock } from "@/lib/format";
import { Badge, type Tone } from "./ui";

const OUTCOME: Record<RecoveryOutcome, { label: string; tone: Tone }> = {
  completed: { label: "Complete", tone: "ok" },
  failed: { label: "Failed · customer can retry", tone: "neutral" },
  needs_review: { label: "Needs review", tone: "warn" },
  awaiting_delivery: { label: "Waiting for delivery", tone: "accent" },
  pending: { label: "Reconciling", tone: "accent" },
};

/** The most recent fact the recovery worker verified against the services that own the records. */
function lastVerified(a: RecoveryAttempt): string {
  if (a.verified_result) {
    const n = a.verified_result.confirmations_for_order;
    return `Confirmation ${a.verified_result.resent ? "resent and " : ""}read back as delivered${n != null ? ` · ${n} for this order` : ""}`
      + (a.verified_result.found_after_restart ? " · found after a worker restart, not resent" : "");
  }
  const c = a.confirmation;
  if (c.state === "found") return `NovaConnect confirmation found${c.status ? ` (${c.status})` : ""}`;
  if (c.state === "absent") return "NovaConnect has no confirmation";
  if (c.state === "lookup_failed") return "NovaConnect lookup failed";
  if (a.order.state === "lookup_failed") return "NovaMart order lookup failed";
  if (a.order.state) return `NovaMart order ${a.order.state}${a.order_id ? ` (${a.order_id})` : ""}`;
  return "Not checked yet";
}

export function AttemptRow({ a }: { a: RecoveryAttempt }) {
  const outcome = OUTCOME[a.outcome];
  const checkedAt = a.confirmation.checked_at ?? a.order.checked_at;
  return (
    <li className="px-4 py-3 text-sm sm:px-5 md:grid md:grid-cols-[11rem_minmax(0,1fr)_auto] md:items-start md:gap-5">
      <p className="font-mono text-xs text-ink">{a.checkout_id}</p>
      <div className="mt-1 min-w-0 md:mt-0">
        <p className="text-ink">{lastVerified(a)}</p>
        <p className="tabular text-xs text-muted">
          {a.order_id ?? "No order"}{checkedAt ? ` · checked ${clock(checkedAt)}` : ""}
          {a.customer_saw && a.customer_saw.status_code >= 400 ? ` · customer saw HTTP ${a.customer_saw.status_code}${a.customer_saw.error_code ? ` ${a.customer_saw.error_code}` : ""}` : ""}
        </p>
        {a.outcome !== "completed" && a.reason && <p className="mt-1 text-xs text-muted">{a.reason}</p>}
        {a.missing.length > 0 && (
          <p className="mt-1 text-xs text-warn">Missing evidence: {a.missing.join("; ")}</p>
        )}
      </div>
      <div className="mt-1.5 md:mt-0"><Badge tone={outcome.tone}>{outcome.label}</Badge></div>
    </li>
  );
}

/** Review cases first so they stay visible, then unsettled, then settled checkouts. */
const ORDER: Record<RecoveryOutcome, number> = { needs_review: 0, pending: 1, awaiting_delivery: 1, failed: 2, completed: 3 };

export function AffectedCheckouts({ recovery }: { recovery: Recovery }) {
  const attempts = [...recovery.attempts].sort((x, y) => ORDER[x.outcome] - ORDER[y.outcome]);
  if (attempts.length === 0) return <p className="px-4 py-3 text-sm text-muted sm:px-5">No interrupted checkouts in the incident window.</p>;
  return <ul className="divide-y divide-line">{attempts.map((a) => <AttemptRow key={a.checkout_id} a={a} />)}</ul>;
}
