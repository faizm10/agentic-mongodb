"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";

export function StatusActions() {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  async function post(label: string, url: string, body?: unknown) {
    setBusy(label);
    setMsg(null);
    const r = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body ?? {}) });
    const j = await r.json();
    setBusy(null);
    setMsg(j.error ? `Error: ${j.error}` : label === "checks" ? `Portal: ${j.portal.ok ? "pass" : "fail"} · Onboarding: ${j.onboarding.ok ? "pass" : "fail"}` : `Seeded ${j.incidentId} (${j.variant})`);
    router.refresh();
  }
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <button disabled={!!busy} onClick={() => post("checks", "/api/checks/run")} className="rounded border bg-white px-3 py-1.5 hover:bg-gray-50 disabled:opacity-50">
        {busy === "checks" ? "Running…" : "Run checks now"}
      </button>
      <button disabled={!!busy} onClick={() => post("reset", "/api/reset", { variant: "config-drift" })} className="rounded border bg-white px-3 py-1.5 hover:bg-gray-50 disabled:opacity-50">
        {busy === "reset" ? "Seeding…" : "Reset demo"}
      </button>
      <button disabled={!!busy} onClick={() => post("reset2", "/api/reset", { variant: "stalled-worker" })} className="rounded border bg-white px-3 py-1.5 text-gray-500 hover:bg-gray-50 disabled:opacity-50" title="Same symptoms, different cause: hung worker, correct mapping">
        {busy === "reset2" ? "Seeding…" : "Reset (alt: stalled worker)"}
      </button>
      {msg && <span className="text-gray-600">{msg}</span>}
    </div>
  );
}
