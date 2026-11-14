"use client";
import { useCallback, useEffect, useState } from "react";

type Any = any; // payload from lib/plugins pluginStatuses()

const STATUS: Record<string, [string, string]> = {
  connected: ["Connected", "bg-ok-soft text-ok"],
  error: ["Error", "bg-bad-soft text-bad"],
  not_connected: ["Not connected", "bg-canvas text-muted"],
};
const DELIVERY: Record<string, string> = { delivered: "text-ok", failed: "text-bad", sending: "text-warn", skipped: "text-muted" };

export function PluginsView() {
  const [plugins, setPlugins] = useState<Any[] | null>(null);
  const [result, setResult] = useState<Record<string, { ok: boolean; detail: string }>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    const j = await (await fetch("/api/plugins", { cache: "no-store" })).json();
    if (j.error) setErr(j.error);
    else setPlugins(j);
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  async function test(id: string) {
    setBusy(id);
    const j = await (await fetch(`/api/plugins/${id}/test`, { method: "POST" })).json();
    setResult((r) => ({ ...r, [id]: j.error ? { ok: false, detail: j.error } : j }));
    setBusy(null);
    load();
  }
  async function retry() {
    setBusy("retry");
    await fetch("/api/plugins/retry", { method: "POST" });
    setBusy(null);
    load();
  }

  if (err) return <div className="rounded-lg border border-bad bg-bad-soft p-4 text-sm text-bad">{err}</div>;
  if (!plugins) return <div className="text-sm text-muted">Loading plugins…</div>;
  const anyUndelivered = plugins.some((p) => p.status === "connected" && p.deliveries.some((d: Any) => d.state !== "delivered"));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-medium">Plugins</h1>
          <p className="mt-1 text-sm text-muted">
            Credentials stay server-side in <code className="font-mono">.env.local</code>. Each notification is saved in an Atlas outbox and sent once.
          </p>
        </div>
        {anyUndelivered && (
          <button disabled={!!busy} onClick={retry} className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
            {busy === "retry" ? "Retrying…" : "Retry undelivered notifications"}
          </button>
        )}
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        {plugins.map((p) => {
          const [label, cls] = STATUS[p.status];
          const r = result[p.id];
          return (
            <section key={p.id} className="flex flex-col rounded-lg border border-line bg-surface p-4">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <h2 className="font-medium">{p.name}</h2>
                  <div className="text-xs text-muted">{p.vendor} · {p.capabilities.join(" · ")}</div>
                </div>
                <span className={`whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium ${cls}`}>{label}</span>
              </div>
              <p className="mt-3 text-sm">{p.description}</p>
              <p className={`mt-2 text-xs ${p.status === "error" ? "text-bad" : "text-muted"}`}>{p.detail}</p>

              <div className="mt-3 rounded-md bg-canvas p-2">
                <div className="mb-1 text-xs font-medium text-muted">Setup</div>
                <ul className="space-y-1">
                  {p.envVars.map((v: Any) => (
                    <li key={v.name} className="text-xs">
                      <span className={v.set ? "text-ok" : "text-muted"}>{v.set ? "✓" : "○"}</span>{" "}
                      <code className="font-mono">{v.name}</code>
                      {!v.required && <span className="text-muted"> (optional)</span>}
                      <div className="ml-4 text-muted">{v.hint}</div>
                    </li>
                  ))}
                </ul>
              </div>

              {p.deliveries.length > 0 && (
                <div className="mt-3">
                  <div className="mb-1 text-xs font-medium text-muted">Recent notifications</div>
                  <ul className="space-y-0.5 text-xs">
                    {p.deliveries.map((d: Any) => (
                      <li key={d._id} className="flex gap-2">
                        <span className="w-20 shrink-0 font-mono">{d.event}</span>
                        <span className={`w-16 shrink-0 ${DELIVERY[d.state]}`}>{d.state}</span>
                        <span className="min-w-0 truncate text-muted" title={d.detail}>{d.detail}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="mt-auto pt-4">
                <button
                  disabled={p.status === "not_connected" || !!busy}
                  onClick={() => test(p.id)}
                  className="w-full rounded-md border border-line px-3 py-2 text-sm hover:bg-canvas disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {busy === p.id ? "Testing…" : p.canSendTest ? "Send test message" : "Test connection"}
                </button>
                {r && <p className={`mt-2 text-xs ${r.ok ? "text-ok" : "text-bad"}`}>{r.detail}</p>}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
