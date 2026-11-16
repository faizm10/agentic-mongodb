import { CheckCircle2, CircleAlert, Clock3, PackageCheck, RefreshCcw, XCircle } from "lucide-react";
import type { Incident, Recovery, RecoveryAttempt, RecoveryOutcome, RecoveryStatus } from "@/lib/api";
import { clock } from "@/lib/format";
import { Badge, type Tone } from "./ui";

export const RECOVERY_LABEL: Record<RecoveryStatus, string> = {
  not_needed: "No interrupted checkouts",
  waiting: "Recovering interrupted checkouts: waiting for the recovery worker",
  reconciling: "Recovering interrupted checkouts",
  needs_review: "Recovering interrupted checkouts: some need review",
  complete: "Every interrupted checkout settled",
};

const OUTCOME: Record<RecoveryOutcome, { label: string; tone: Tone; Icon: typeof CheckCircle2 }> = {
  completed: { label: "Complete", tone: "ok", Icon: CheckCircle2 },
  failed: { label: "Failed, customer can retry", tone: "neutral", Icon: XCircle },
  needs_review: { label: "Needs review", tone: "warn", Icon: CircleAlert },
  awaiting_delivery: { label: "Waiting for delivery", tone: "accent", Icon: Clock3 },
  pending: { label: "Reconciling", tone: "accent", Icon: RefreshCcw },
};

/** The incident's own status, separate from the service's health. */
export function IncidentBadge({ incident }: { incident: Incident }) {
  if (incident.state === "resolved") return <Badge tone="ok">Resolved</Badge>;
  if (incident.recovery_phase === "recovering") {
    const review = incident.recovery_status === "needs_review";
    return <Badge tone={review ? "warn" : "accent"}>{review ? "Recovering: needs review" : "Recovering interrupted checkouts"}</Badge>;
  }
  return <Badge tone="crit">Ongoing</Badge>;
}

function customerSaw(a: RecoveryAttempt): string {
  if (!a.customer_saw) return "no response recorded";
  const { status_code, error_code } = a.customer_saw;
  return status_code < 400 ? `order confirmed (HTTP ${status_code})` : `checkout failed (HTTP ${status_code}${error_code ? `, ${error_code}` : ""})`;
}

function evidenceLine(name: string, state: string | null, extra?: string | null): string {
  if (!state) return `${name}: not checked yet`;
  const text = { saved: "saved", absent: "confirmed absent", found: "found", lookup_failed: "lookup failed" }[state] ?? state;
  return `${name}: ${text}${extra ? ` (${extra})` : ""}`;
}

export function AttemptRow({ a }: { a: RecoveryAttempt }) {
  const outcome = OUTCOME[a.outcome];
  return (
    <li className="rounded-lg border border-line p-4 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-mono text-xs text-ink">{a.checkout_id}</p>
        <Badge tone={outcome.tone}>
          <outcome.Icon className="h-3.5 w-3.5" aria-hidden="true" /> {outcome.label}
        </Badge>
      </div>
      <p className="mt-2 text-muted">{a.reason}</p>
      <dl className="mt-3 grid gap-x-4 gap-y-1 text-xs text-muted sm:grid-cols-2">
        <div><dt className="inline text-faint">Customer saw: </dt><dd className="inline">{customerSaw(a)}</dd></div>
        <div><dt className="inline text-faint">Order: </dt><dd className="inline font-mono">{a.order_id ?? "none"}</dd></div>
        <div><dd>{evidenceLine("NovaMart order", a.order.state)}</dd></div>
        <div><dd>{evidenceLine("NovaConnect confirmation", a.confirmation.state, a.confirmation.status)}</dd></div>
        {a.retried && <div><dd>Confirmation resend intent saved before sending</dd></div>}
        {a.verified_result && (
          <div>
            <dd>
              Read back as delivered · {a.verified_result.confirmations_for_order ?? "?"} confirmation(s) for this order
              {a.verified_result.found_after_restart ? " · found after a worker restart, not resent" : ""}
            </dd>
          </div>
        )}
      </dl>
      {a.missing.length > 0 && (
        <div className="mt-3 rounded-lg border border-warn/30 bg-warn/10 p-3 text-xs text-ink">
          <p className="font-medium">Missing evidence</p>
          <ul className="mt-1 list-disc pl-4 text-muted">{a.missing.map((m) => <li key={m}>{m}</li>)}</ul>
        </div>
      )}
    </li>
  );
}

export function RecoveryPanel({ incident }: { incident: Incident }) {
  const recovery: Recovery | null = incident.recovery;
  return (
    <section className="panel p-6" aria-labelledby="recovery-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 id="recovery-title" className="flex items-center gap-2 text-sm font-semibold">
          <PackageCheck className="h-4 w-4 text-muted" aria-hidden="true" /> Interrupted checkouts
        </h2>
        <p className="text-xs text-muted">{RECOVERY_LABEL[incident.recovery_status]}</p>
      </div>
      {!recovery ? (
        <p className="mt-3 text-sm text-muted">
          {incident.recovery_phase === "recovering"
            ? "A fresh checkout passed. The recovery worker has not picked this incident up yet."
            : "Recovery starts once a fresh checkout passes."}
        </p>
      ) : (
        <>
          <p className="mt-2 text-xs text-muted">
            {recovery.attempts.length} affected checkout(s)
            {recovery.fresh_checkout ? ` · fresh checkout passed ${clock(recovery.fresh_checkout.at, true)}` : ""}
            {recovery.completed_at ? ` · settled ${clock(recovery.completed_at, true)}` : ""}
          </p>
          {recovery.attempts.length > 0 && (
            <ul className="mt-4 space-y-3">{recovery.attempts.map((a) => <AttemptRow key={a.checkout_id} a={a} />)}</ul>
          )}
          <details className="mt-4 text-xs">
            <summary className="cursor-pointer font-medium text-muted hover:text-ink">
              Recovery log · {recovery.workers.length} worker(s)
            </summary>
            <ul className="mt-2 space-y-1 font-mono text-[11px] text-faint">
              {recovery.workers.map((w) => <li key={w}>worker {w}</li>)}
            </ul>
            <ol className="mt-3 space-y-1.5 text-muted">
              {recovery.events.map((e, n) => (
                <li key={n}><span className="text-faint">{clock(e.at, true)}</span> {e.message}</li>
              ))}
            </ol>
          </details>
        </>
      )}
    </section>
  );
}
