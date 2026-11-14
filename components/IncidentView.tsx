"use client";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

type Any = any; // API payload; shapes come from lib/types.ts on the server

const STATUS_LABEL: Record<string, string> = {
  open: "Open",
  investigating: "Investigating",
  awaiting_approval: "Awaiting approval",
  needs_human: "Needs human review",
  recovering: "Recovering",
  monitoring: "Verifying",
  resolved: "Resolved",
};

const KIND_LABEL: Record<string, [string, string]> = {
  incident: ["Incident", "bg-ink text-white"],
  evidence: ["Evidence", "bg-canvas text-muted"],
  memory: ["Memory", "bg-violet-50 text-violet-800"],
  decision: ["Decision", "bg-blue-50 text-accent"],
  approval: ["Approved", "bg-blue-50 text-accent"],
  worker: ["Worker", "bg-canvas text-muted"],
  checkpoint: ["Saved", "bg-cyan-50 text-cyan-800"],
  sent: ["Sent", "bg-warn-soft text-warn"],
  verified: ["Verified", "bg-ok-soft text-ok"],
  crash: ["Crash", "bg-bad text-white"],
  resume: ["Resume", "bg-violet-50 text-violet-800"],
  check: ["Check", "bg-ok-soft text-ok"],
  guard: ["Blocked", "bg-bad-soft text-bad"],
  error: ["Error", "bg-bad-soft text-bad"],
  notify: ["Notified", "bg-canvas text-muted"],
};

const WORKER_COLORS = ["bg-sky-700", "bg-rose-700", "bg-teal-700", "bg-amber-700"];

const RESOLUTION_TEXT: Record<string, string> = {
  "opsrelay:pre_existing_membership": "Already had access from an earlier request. No grant needed.",
  "opsrelay:grant_took_effect_before_crash": "The crashed worker's grant had already landed. Confirmed in the sandbox and not sent again.",
  "opsrelay:granted_and_verified": "Granted, then read back from the sandbox.",
};

const time = (d: string) => new Date(d).toLocaleTimeString([], { hour12: false });

function Panel({ title, hint, children }: { title: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-line bg-surface p-4">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-medium">{title}</h2>
        {hint && <span className="text-xs text-muted">{hint}</span>}
      </div>
      {children}
    </section>
  );
}

function WorkerChip({ id, color }: { id?: string; color: string }) {
  if (!id) return null;
  return <span className={`whitespace-nowrap rounded px-1.5 py-0.5 font-mono text-[11px] text-white ${color}`}>{id}</span>;
}

// ---------- stage logic ----------
type Stage = 0 | 1 | 2 | 3 | 4;
const STAGES = ["Detect", "Investigate", "Approve", "Recover", "Resolve"];

function stageOf(incident: Any): Stage {
  if (incident.status === "resolved") return 4;
  if (incident.approval) return 3;
  if (incident.investigation) return 2;
  return 1;
}

function Stepper({ stage, crashed }: { stage: Stage; crashed: boolean }) {
  return (
    <ol className="flex flex-wrap items-center gap-x-2 gap-y-2 text-sm">
      {STAGES.map((s, i) => {
        const done = i < stage || (i === 4 && stage === 4);
        const current = i === stage && stage !== 4;
        const bad = current && crashed;
        return (
          <li key={s} className="flex items-center gap-2">
            <span
              className={`flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold ${
                bad ? "bg-bad text-white" : done ? "bg-ok text-white" : current ? "bg-accent text-white" : "border border-line bg-surface text-muted"
              }`}
            >
              {bad ? "!" : done ? "✓" : i + 1}
            </span>
            <span className={current ? "font-medium" : done ? "text-ink" : "text-muted"}>{s}</span>
            {i < STAGES.length - 1 && <span className="mx-1 hidden h-px w-8 bg-line sm:block" />}
          </li>
        );
      })}
    </ol>
  );
}

// ---------- hire lifecycle ----------
const TRACK = ["Pending", "Intent saved", "Sent", "Verified", "Complete"];

function trackIndex(step: Any, hire: Any): { at: number; skipped: number[] } {
  if (hire.appStatus === "complete") {
    const skipped = step?.resolution === "pre_existing_membership" ? [1, 2] : [];
    return { at: 4, skipped };
  }
  if (!step) return { at: 0, skipped: [] };
  return { at: { intent: 1, sent: 2, verified: 3, complete: 4, failed: 2 }[step.state as string] ?? 0, skipped: [] };
}

function HireCard({ hire, step, wColor }: { hire: Any; step: Any; wColor: (w?: string) => string }) {
  const { at, skipped } = trackIndex(step, hire);
  const mismatch = hire.sandboxActive && hire.appStatus !== "complete" && step?.state === "intent";
  return (
    <div className={`rounded-lg border bg-surface p-4 ${mismatch ? "border-bad ring-2 ring-bad-soft" : "border-line"}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="font-medium">{hire.name}</div>
          <div className="font-mono text-xs text-muted">@{hire.login}</div>
        </div>
        <div className="text-right">
          <div className="tabular text-3xl font-semibold leading-none">{hire.grantsCreatedSinceIncident}</div>
          <div className="mt-1 text-xs text-muted">grants this incident</div>
        </div>
      </div>

      <ol className="mt-4 grid grid-cols-5 gap-1" aria-label="Onboarding progress">
        {TRACK.map((t, i) => {
          const skip = skipped.includes(i);
          const reached = i <= at && !skip;
          return (
            <li key={t} className="text-center">
              <div className={`h-1.5 rounded-full ${skip ? "bg-line" : reached ? (i === 4 ? "bg-ok" : i === 0 ? "bg-muted/40" : "bg-accent") : "bg-line"}`} />
              <div className={`mt-1 text-[11px] leading-tight ${i === at && !skip ? "font-medium text-ink" : "text-muted"} ${skip ? "line-through" : ""}`}>{t}</div>
            </li>
          );
        })}
      </ol>

      <dl className="mt-4 grid grid-cols-2 gap-2 text-sm">
        <div className="rounded-md bg-canvas p-2">
          <dt className="text-xs text-muted">Atlas checkpoint</dt>
          <dd className="font-medium">{step ? step.state : "nothing yet"}</dd>
          {step?.workerId && <dd className="mt-1"><WorkerChip id={step.workerId} color={wColor(step.workerId)} /></dd>}
        </div>
        <div className="rounded-md bg-canvas p-2">
          <dt className="text-xs text-muted">Sandbox (reality)</dt>
          <dd className={`font-medium ${hire.sandboxActive ? "text-ok" : "text-muted"}`}>{hire.sandboxActive === null ? "unknown" : hire.sandboxActive ? "Member ✓" : "Not a member"}</dd>
          <dd className="mt-1 text-xs text-muted">{hire.putCallsSinceIncident} PUT call{hire.putCallsSinceIncident === 1 ? "" : "s"}</dd>
        </div>
      </dl>
      {mismatch && <p className="mt-3 text-sm text-bad">Atlas has only <b>intent</b>, but the grant is live in the sandbox. A fresh worker must check before it resends.</p>}
      {hire.completedVia && <p className="mt-3 text-sm text-ok">{RESOLUTION_TEXT[hire.completedVia] ?? hire.completedVia}</p>}
    </div>
  );
}

// ---------- main ----------
export function IncidentView({ id }: { id: string }) {
  const [data, setData] = useState<Any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [gone, setGone] = useState<{ currentIncidentId: string | null } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const timelineRef = useRef<HTMLOListElement>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`/api/incidents/${id}`, { cache: "no-store" });
      const j = await r.json();
      if (r.status === 404) setGone({ currentIncidentId: j.currentIncidentId });
      else if (j.error) setErr(j.error);
      else {
        setErr(null);
        setData(j);
      }
    } catch (e) {
      setErr((e as Error).message);
    }
  }, [id]);

  useEffect(() => {
    if (gone) return;
    load();
    const t = setInterval(load, 1000);
    return () => clearInterval(t);
  }, [load, gone]);

  const eventCount = data?.events.length ?? 0;
  useEffect(() => {
    const el = timelineRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [eventCount]);

  async function post(label: string, url: string, body?: unknown) {
    setBusy(label);
    try {
      const j = await (await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body ?? {}) })).json();
      if (j.error) setErr(j.error);
    } finally {
      setBusy(null);
      load();
    }
  }

  if (gone)
    return (
      <div className="rounded-lg border border-line bg-surface p-6">
        <p className="font-medium">This incident no longer exists. The demo was reset.</p>
        <div className="mt-3 flex gap-3 text-sm">
          {gone.currentIncidentId && <Link href={`/incidents/${gone.currentIncidentId}`} className="rounded-md bg-accent px-4 py-2 font-medium text-white">Open current incident →</Link>}
          <Link href="/" className="rounded-md border border-line px-4 py-2">Back to status</Link>
        </div>
      </div>
    );
  if (err && !data) return <div className="rounded-lg border border-bad bg-bad-soft p-4 text-sm text-bad">Couldn&apos;t load incident: {err}</div>;
  if (!data) return <div className="text-sm text-muted">Loading incident…</div>;

  const { incident, events, checkpoint, proof, fault, liveWorker } = data;
  const inv = incident.investigation;
  const workerIds: string[] = proof.workers.map((w: Any) => w._id);
  const wColor = (w?: string) => (w ? WORKER_COLORS[workerIds.indexOf(w) % WORKER_COLORS.length] ?? "bg-muted" : "");
  const lastWorker = proof.workers.at(-1);
  const crashed = !!incident.approval && incident.status !== "resolved" && !liveWorker && !!lastWorker?.exitedAt && (!!lastWorker.signal || lastWorker.exitCode !== 0);
  const stage = stageOf(incident);
  const steps = checkpoint?.steps ?? {};
  const pendingHire = proof.perHire.find((h: Any) => h.appStatus !== "complete");

  // ---------- the one next-step bar ----------
  let bar: { tone: "info" | "warn" | "bad" | "ok"; title: string; body?: React.ReactNode; action?: React.ReactNode };
  const primary = "rounded-md px-4 py-2 font-medium text-white disabled:opacity-50 whitespace-nowrap";
  if (incident.status === "resolved") {
    const a = proof.perHire.find((h: Any) => h.hireId === "hire-a");
    const b = proof.perHire.find((h: Any) => h.hireId === "hire-b");
    bar = {
      tone: "ok",
      title: "Resolved. Work done before the crash was kept, and nothing ran twice.",
      body: (
        <>
          {b?.name} was granted <b>exactly {b?.grantsCreatedSinceIncident}</b> time{b?.grantsCreatedSinceIncident === 1 ? "" : "s"}. {a?.name} needed <b>{a?.grantsCreatedSinceIncident}</b> new grants. A fresh synthetic onboarding check passed, and {workerIds.length} worker{workerIds.length === 1 ? "" : "s"} handled this one incident.
        </>
      ),
    };
  } else if (crashed) {
    bar = {
      tone: "bad",
      title: `Worker ${lastWorker._id} crashed mid-recovery. The web app is still up.`,
      body: <>Atlas remembers what finished: {Object.entries(steps).map(([k, s]: Any) => `${k.replace("hire:", "")} → ${s.state}`).join(" · ")}. A fresh worker will resume from here and check reality before acting.</>,
      action: (
        <button disabled={!!busy} onClick={() => post("launch", `/api/incidents/${id}/workers`)} className={`${primary} bg-bad`}>
          {busy === "launch" ? "Launching…" : "Launch fresh worker"}
        </button>
      ),
    };
  } else if (liveWorker) {
    bar = { tone: "info", title: `Worker ${liveWorker} is recovering…`, body: "Each step is saved to Atlas before and after it touches the sandbox." };
  } else if (incident.approval && pendingHire) {
    bar = {
      tone: "warn",
      title: "Recovery paused",
      body: "No worker is running.",
      action: <button disabled={!!busy} onClick={() => post("launch", `/api/incidents/${id}/workers`)} className={`${primary} bg-accent`}>Launch worker</button>,
    };
  } else if (inv?.decision.action === "restore_mapping") {
    bar = {
      tone: "info",
      title: `Proposed fix: restore team ${inv.signals.currentTeamId} → ${inv.signals.approvedTeamId}, then reconcile ${proof.perHire.length || "pending"} hires`,
      body: "The agent will only grant the approved team to the hires named here.",
      action: (
        <button disabled={!!busy} onClick={() => post("approve", `/api/incidents/${id}/approve`, { approvedBy: "on-call engineer" })} className={`${primary} bg-ok`}>
          {busy === "approve" ? "Approving…" : "Approve & start recovery"}
        </button>
      ),
    };
  } else if (inv) {
    bar = {
      tone: "warn",
      title: inv.decision.action === "restart_worker" ? "Recommendation: restart the provisioning worker" : "Escalated: needs a human",
      body: "The evidence doesn't match a fix the agent may apply on its own, so it won't act.",
    };
  } else {
    bar = {
      tone: "info",
      title: "Provisioning is failing and two new hires are stuck on Pending",
      body: "The agent will read live errors, worker health, and recent config changes, then check past incidents.",
      action: (
        <button disabled={!!busy} onClick={() => post("inv", `/api/incidents/${id}/investigate`)} className={`${primary} bg-accent`}>
          {busy === "inv" ? "Investigating…" : "Investigate"}
        </button>
      ),
    };
  }
  const toneCls = { info: "border-accent/30 bg-blue-50/60", warn: "border-warn/30 bg-warn-soft", bad: "border-bad bg-bad-soft", ok: "border-ok/30 bg-ok-soft" }[bar.tone];

  return (
    <div className="space-y-5">
      {/* header */}
      <div className="space-y-1">
        <Link href="/" className="text-sm text-accent hover:underline">← All services</Link>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h1 className="text-xl font-medium">{incident.title}</h1>
          <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${incident.status === "resolved" ? "bg-ok text-white" : crashed ? "bg-bad text-white" : "bg-warn-soft text-warn"}`}>
            {crashed ? "Worker crashed" : STATUS_LABEL[incident.status] ?? incident.status}
          </span>
        </div>
        <div className="font-mono text-xs text-muted">{incident._id} · Onboarding Provisioning</div>
      </div>

      <Stepper stage={stage} crashed={crashed} />

      {/* next step */}
      <div className={`flex flex-col gap-3 rounded-lg border p-4 sm:flex-row sm:items-center ${toneCls}`}>
        <div className="flex-1">
          <div className="font-medium">{bar.title}</div>
          {bar.body && <div className="mt-1 text-sm text-ink/80">{bar.body}</div>}
        </div>
        {bar.action}
      </div>
      {err && <div className="text-sm text-bad">{err}</div>}

      {/* hires: the payoff */}
      {proof.perHire.length > 0 && (
        <div className="grid gap-4 md:grid-cols-2">
          {proof.perHire.map((h: Any) => <HireCard key={h.hireId} hire={h} step={steps[`hire:${h.hireId}`]} wColor={wColor} />)}
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-5">
        {/* timeline */}
        <div className="lg:col-span-3">
          <Panel title="What the agent did" hint={`${events.length} events · live`}>
            <ol ref={timelineRef} className="max-h-[28rem] space-y-1 overflow-y-auto pr-1 text-sm">
              {events.map((e: Any) => {
                const [label, cls] = KIND_LABEL[e.kind] ?? [e.kind, "bg-canvas text-muted"];
                return (
                  <li key={e._id} className={`grid grid-cols-[4.25rem_5.5rem_1fr] items-start gap-2 rounded-md px-1 py-1 ${e.kind === "crash" ? "bg-bad-soft" : ""}`}>
                    <span className="tabular pt-0.5 font-mono text-[11px] text-muted">{time(e.at)}</span>
                    <span className={`rounded px-1.5 py-0.5 text-center text-[11px] font-medium ${cls}`}>{label}</span>
                    <span className="min-w-0 break-words">
                      {e.workerId && <span className="mr-1.5"><WorkerChip id={e.workerId} color={wColor(e.workerId)} /></span>}
                      {e.message}
                    </span>
                  </li>
                );
              })}
            </ol>
            <p className="mt-3 border-t border-line pt-2 text-xs text-muted">
              <b className="text-warn">Sent</b> means the API call returned. <b className="text-ok">Verified</b> means the agent read the result back from the sandbox. Only verified work counts as done.
            </p>
          </Panel>
        </div>

        <div className="space-y-5 lg:col-span-2">
          {/* why */}
          <Panel title="Why this fix" hint={inv && (inv.explanation.source === "openai" ? `Explained by OpenAI ${inv.explanation.model}` : "Template explanation (OpenAI not configured)")}>
            {!inv ? (
              <p className="text-sm text-muted">Appears after the agent investigates.</p>
            ) : (
              <div className="space-y-3 text-sm">
                <p>{inv.explanation.text}</p>
                {inv.explanation.error && <p className="text-xs text-bad">OpenAI error: {inv.explanation.error}</p>}
                <ul className="space-y-1 text-muted">
                  {inv.decision.reasons.map((r: string) => <li key={r}>· {r}</li>)}
                </ul>
                <div>
                  <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-muted">
                    Similar past incidents
                    <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${inv.similar.mode === "atlas_vector_search" ? "bg-ok-soft text-ok" : "bg-warn-soft text-warn"}`}>
                      {inv.similar.mode === "atlas_vector_search" ? "Atlas Vector Search" : "Unverified fallback, not Atlas"}
                    </span>
                  </div>
                  {inv.similar.results.slice(0, 2).map((s: Any, i: number) => (
                    <div key={s._id} className={`mb-1 rounded-md p-2 text-xs ${i === 0 ? "bg-canvas" : "text-muted"}`}>
                      <span className="tabular font-mono">{s.score.toFixed(2)}</span> <b>{s._id}</b> {s.title}
                      {i === 0 && <div className="mt-0.5 text-muted">Fixed by: {s.resolution}</div>}
                    </div>
                  ))}
                </div>
                <p className="text-xs text-muted">Rule {inv.decision.rule}</p>
              </div>
            )}
          </Panel>

          {/* demo controls */}
          <section className="rounded-lg border border-dashed border-muted/50 p-4">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">Demo control</h2>
            <label className="mt-2 flex cursor-pointer items-start gap-2 text-sm">
              <input type="checkbox" className="mt-1 h-4 w-4" checked={!!fault?.armed} disabled={!!busy} onChange={(e) => post("fault", "/api/fault", { armed: e.target.checked })} />
              <span>
                Simulate a crash: kill the next worker right after Jordan&apos;s grant commits, before Atlas records it.
                {fault?.consumedBy && <span className="block text-xs text-bad">Used by {fault.consumedBy}</span>}
              </span>
            </label>
          </section>

          {/* checkpoint + workers */}
          <Panel title="Atlas checkpoint" hint={<span className="font-mono">recovery_checkpoints</span>}>
            {!checkpoint ? (
              <p className="text-sm text-muted">Created when recovery starts.</p>
            ) : (
              <table className="w-full text-sm">
                <tbody>
                  {Object.entries(steps).map(([k, s]: Any) => (
                    <tr key={k} className="border-t border-line first:border-0">
                      <td className="py-1.5 font-mono text-xs">{k}</td>
                      <td className="py-1.5">
                        <span className={`rounded px-1.5 py-0.5 text-xs ${s.state === "complete" || s.state === "verified" ? "bg-ok-soft text-ok" : s.state === "failed" ? "bg-bad-soft text-bad" : "bg-warn-soft text-warn"}`}>{s.state}</span>
                      </td>
                      <td className="py-1.5 text-right"><WorkerChip id={s.workerId} color={wColor(s.workerId)} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {proof.workers.length > 0 && (
              <div className="mt-3 space-y-1 border-t border-line pt-3 text-sm">
                {proof.workers.map((w: Any) => (
                  <div key={w._id} className="flex items-center justify-between gap-2">
                    <WorkerChip id={w._id} color={wColor(w._id)} />
                    <span className="text-xs text-muted">pid {w.pid}</span>
                    <span className="text-xs">
                      {!w.exitedAt ? <b className="text-accent">running</b> : w.signal ? <b className="text-bad">killed ({w.signal})</b> : <span className="text-ok">finished</span>}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel title="Sandbox write log" hint="every PUT since the incident opened">
            {proof.mutations.length === 0 ? (
              <p className="text-sm text-muted">No writes yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full font-mono text-[11px]">
                  <tbody>
                    {proof.mutations.map((m: Any) => (
                      <tr key={m.seq} className="border-t border-line first:border-0">
                        <td className="py-1 pr-2 text-muted">#{m.seq}</td>
                        <td className="py-1 pr-2">{m.login}</td>
                        <td className={`py-1 pr-2 ${m.effect === "created" ? "text-ok" : m.effect === "already_member" ? "text-warn" : "text-bad"}`}>{m.effect}</td>
                        <td className="py-1 text-muted">{m.actor.replace("opsrelay:", "")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {proof.sandboxError && <p className="text-xs text-bad">Sandbox unreachable: {proof.sandboxError}</p>}
          </Panel>
        </div>
      </div>
    </div>
  );
}
