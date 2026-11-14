"use client";

import { useState } from "react";
import { RotateCcw, Zap } from "lucide-react";
import type { ServiceOverview } from "@/lib/api";
import { StatusPill } from "@/components/ui";

type ServiceId = "novamart" | "novaconnect";

// Each control pushes a real code bug to main (see uptime/bad_deploys.py). Labels name
// what customers notice, and only the scenario number is sent, never the bug.
const GROUPS: { id: ServiceId; name: string; actions: { scenario: 1 | 2 | 3; label: string }[] }[] = [
  {
    id: "novamart",
    name: "NovaMart",
    actions: [
      { scenario: 1, label: "Images not loading" },
      { scenario: 2, label: "Payments failing (Stripe down)" },
      { scenario: 3, label: "Everything out of stock" },
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

  async function change(id: ServiceId, scenario?: number) {
    setPending(id);
    setNotice(scenario ? "Deploying the change…" : "Restoring…");
    try {
      const response = await fetch(`/api/v1/demo/bad-deploys/${id}`, {
        method: scenario ? "POST" : "DELETE",
        headers: scenario ? { "Content-Type": "application/json" } : undefined,
        body: scenario ? JSON.stringify({ scenario }) : undefined,
      });
      if (!response.ok) {
        setNotice((await errorMessage(response)) ?? "Could not change the demo outage. Check the uptime API log.");
        return;
      }
      if (!scenario) {
        // Also clear any outage left by the earlier flag-file controls.
        await fetch(`/api/v1/demo/outages/${id}`, { method: "DELETE" }).catch(() => undefined);
      }
      setNotice(
        scenario
          ? "Deployed. The next status check will catch the outage, then the AI agent investigates and opens a fix PR."
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
                {service && <StatusPill status={service.current.status} size="sm" />}
              </div>
              <div className="mt-4 grid gap-2 sm:grid-cols-3">
                {group.actions.map((action) => (
                  <button
                    key={action.scenario}
                    type="button"
                    disabled={pending !== null}
                    onClick={() => change(group.id, action.scenario)}
                    className="min-h-11 rounded-lg border border-crit/25 bg-crit/5 px-3 py-2 text-left text-xs font-medium text-ink transition hover:border-crit/60 hover:bg-crit/10 disabled:cursor-wait disabled:opacity-50"
                  >
                    {action.label}
                  </button>
                ))}
              </div>
              <button
                type="button"
                disabled={pending !== null}
                onClick={() => change(group.id)}
                className="mt-3 inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-line px-3 py-2 text-xs font-medium text-muted transition hover:border-line-strong hover:text-ink disabled:cursor-wait disabled:opacity-50"
              >
                <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" /> Restore {group.name}
              </button>
            </div>
          );
        })}
      </div>
      {notice && <p className="border-t border-line px-5 py-3 text-xs text-muted sm:px-6" role="status">{notice}</p>}
    </section>
  );
}
