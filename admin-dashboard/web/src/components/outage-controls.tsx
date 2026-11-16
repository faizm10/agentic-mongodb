"use client";

import { useState } from "react";
import { RotateCcw, ShoppingCart, Zap } from "lucide-react";
import type { ServiceOverview } from "@/lib/api";
import { StatusPill } from "@/components/ui";

type ServiceId = "novamart" | "novaconnect";

// Each control pushes a real code bug to main (see uptime/bad_deploys.py). Labels name
// what customers notice, and only the scenario number is sent, never the bug.
// Scenario 4 also slows NovaConnect's order confirmations; "Restore NovaConnect" ends that.
const GROUPS: { id: ServiceId; name: string; actions: { scenario: 1 | 2 | 3 | 4; label: string }[] }[] = [
  {
    id: "novamart",
    name: "NovaMart",
    actions: [
      { scenario: 1, label: "Images not loading" },
      { scenario: 2, label: "Payments failing (Stripe down)" },
      { scenario: 3, label: "Everything out of stock" },
      { scenario: 4, label: "Checkout errors, orders still placed" },
    ],
  },
  {
    id: "novaconnect",
    name: "NovaConnect",
    actions: [
      { scenario: 1, label: "Can't send messages" },
      { scenario: 2, label: "Not receiving messages" },
      { scenario: 3, label: "Sending and receiving down" },
    ],
  },
];

async function errorMessage(response: Response): Promise<string | null> {
  try {
    const body = await response.json();
    return typeof body?.error?.message === "string" ? body.error.message : null;
  } catch {
    return null;
  }
}

export function OutageControls({ services, onChange }: { services: ServiceOverview[]; onChange: () => void }) {
  const [pending, setPending] = useState<ServiceId | null>(null);
  const [notice, setNotice] = useState("");

  async function sendCheckouts() {
    setPending("novamart");
    setNotice("Placing 4 customer checkouts…");
    try {
      const response = await fetch("/api/v1/demo/checkouts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ count: 4 }),
      });
      const body = await response.json().catch(() => null);
      const results: { tries: { status: number | null }[] }[] = body?.data ?? [];
      const failed = results.filter((r) => (r.tries.at(-1)?.status ?? 500) >= 500).length;
      setNotice(response.ok ? `Placed ${results.length} checkouts; ${failed} reported a failure to the customer.` : "Could not place checkouts.");
      onChange();
    } catch {
      setNotice("Could not reach the uptime API.");
    } finally {
      setPending(null);
    }
  }

  async function change(id: ServiceId, scenario?: number) {
    setPending(id);
    setNotice(scenario ? "Deploying outage…" : "Restoring…");
    try {
      const response = await fetch(`/api/v1/demo/bad-deploys/${id}`, {
        method: scenario ? "POST" : "DELETE",
        headers: scenario ? { "Content-Type": "application/json" } : undefined,
        body: scenario ? JSON.stringify({ scenario }) : undefined,
      });
      if (!response.ok) {
        setNotice(scenario ? "Could not deploy the outage." : (await errorMessage(response)) ?? "Could not restore. Check the uptime API log.");
        return;
      }
      if (!scenario) {
        // Also clear any outage left by the earlier flag-file controls.
        await fetch(`/api/v1/demo/outages/${id}`, { method: "DELETE" }).catch(() => undefined);
      }
      setNotice(
        scenario
          ? "Outage confirmed."
          : "Restored. Waiting for the next status check.",
      );
      onChange();
      window.setTimeout(onChange, 1800);
    } catch {
      setNotice("Could not reach the uptime API.");
    } finally {
      setPending(null);
    }
  }

  return (
    <section className="panel overflow-hidden" aria-labelledby="outage-controls-title">
      <div className="flex flex-wrap items-center gap-4 border-b border-line px-5 py-4 sm:px-6">
        <span className="flex h-9 w-9 items-center justify-center rounded-lg border border-crit/30 bg-crit/10 text-crit">
          <Zap className="h-4 w-4" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 id="outage-controls-title" className="text-sm font-semibold">Demo outage controls</h2>
          <p className="mt-0.5 text-xs text-muted">Break an app the way real outages happen: a bad deploy the AI agent can fix, or a payment provider going down. Restore it when finished.</p>
        </div>
      </div>
      <div className="grid divide-y divide-line lg:grid-cols-2 lg:divide-x lg:divide-y-0">
        {GROUPS.map((group) => {
          const service = services.find((item) => item.id === group.id);
          return (
            <div key={group.id} className="p-5 sm:p-6">
              <div className="flex items-center justify-between gap-3">
                <h3 className="text-sm font-semibold">{group.name}</h3>
                {service && <StatusPill status={service.current.status} />}
              </div>
              <div className="mt-4 grid gap-2 sm:grid-cols-3">
                {group.actions.map((action) => (
                  <button
                    key={action.scenario}
                    type="button"
                    disabled={pending !== null}
                    onClick={() => change(group.id, action.scenario)}
                    className="btn btn-danger min-h-11 text-left"
                  >
                    {action.label}
                  </button>
                ))}
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={pending !== null}
                  onClick={() => change(group.id)}
                  className="btn"
                >
                  <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" /> Restore {group.name}
                </button>
                {group.id === "novamart" && (
                  <button
                    type="button"
                    disabled={pending !== null}
                    onClick={sendCheckouts}
                    className="btn"
                  >
                    <ShoppingCart className="h-3.5 w-3.5" aria-hidden="true" /> Send 4 customer checkouts
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
      {notice && <p className="border-t border-line px-5 py-3 text-xs text-muted sm:px-6" role="status">{notice}</p>}
    </section>
  );
}
