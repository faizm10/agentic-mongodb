"use client";

import { useState } from "react";
import { PageHeader, Shell, Skeleton } from "@/components/ui";
import { send, usePoll } from "@/lib/api";

type Connection = { connected: boolean; login?: string; connected_at?: string };
type BetterStack = { configured: boolean; webhook_path?: string; secret_header?: string; secret?: string;
  last_delivery?: { at: string; result: string; reason?: string; incident_id?: string } | null };

export default function Connectors() {
  const github = usePoll<Connection>("/api/v1/github", 60_000);
  return (
    <Shell meta={github.meta} updatedAt={github.updatedAt} error={github.error} intervalMs={60_000}>
      <PageHeader title="Connectors" sub="Where OpsRelay reads code and alerts from. Secrets stay on the server and are shown only once." />
      {!github.data ? <Skeleton className="h-56" /> : (
        <div className="space-y-6">
          <GitHubPanel connection={github.data} onChange={github.reload} />
          <BetterStackPanel />
        </div>
      )}
    </Shell>
  );
}

function GitHubPanel({ connection, onChange }: { connection: Connection; onChange: () => void }) {
  const [token, setToken] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const run = async (action: () => Promise<unknown>) => {
    setPending(true);
    setError("");
    try {
      await action();
      setToken("");
      onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  };

  return (
    <section className="panel p-5">
      <h2 className="text-sm font-semibold">GitHub</h2>
      {connection.connected ? (
        <div className="mt-2 flex flex-wrap items-center gap-3 text-sm">
          <span className="badge tone-ok">Connected as {connection.login}</span>
          <button type="button" className="btn" disabled={pending} onClick={() => run(() => send("DELETE", "/api/v1/github"))}>Disconnect</button>
        </div>
      ) : (
        <form className="mt-2 space-y-3" onSubmit={(e) => { e.preventDefault(); run(() => send("PUT", "/api/v1/github", { token })); }}>
          <p className="text-sm text-muted">
            Paste a GitHub token. Only the repositories it can access can be mapped. A{" "}
            <a className="text-accent hover:underline" href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noreferrer">fine-grained token</a>{" "}
            with Contents, Pull requests, Actions and Dependabot alerts (read) on the chosen repositories is enough. It stays on the server.
          </p>
          <div className="flex flex-wrap gap-2">
            <label className="min-w-0 flex-1">
              <span className="sr-only">GitHub token</span>
              <input className="input font-mono" type="password" autoComplete="off" placeholder="github_pat_…" value={token} onChange={(e) => setToken(e.target.value)} required minLength={20} />
            </label>
            <button type="submit" className="btn btn-primary" disabled={pending}>Connect GitHub</button>
          </div>
        </form>
      )}
      {error && <p className="mt-2 text-sm text-crit" role="alert">{error}</p>}
    </section>
  );
}

function BetterStackPanel() {
  const conf = usePoll<BetterStack>("/api/v1/betterstack", 60_000);
  const [created, setCreated] = useState<BetterStack | null>(null);
  const [error, setError] = useState("");
  const shown = created ?? conf.data;
  const rotate = async () => {
    if (shown?.configured && !window.confirm("Replace the webhook URL and secret? Better Stack must be updated with the new ones.")) return;
    try {
      setCreated(await send<BetterStack>("POST", "/api/v1/betterstack"));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  };
  if (!shown) return null;
  const last = shown.last_delivery;
  return (
    <section className="panel space-y-3 p-5">
      <h2 className="text-sm font-semibold">Better Stack alerts</h2>
      {!shown.configured ? (
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-sm text-muted">Get a webhook URL for this workspace, then map each service to its Better Stack monitor on the Repositories page.</p>
          <button type="button" className="btn btn-primary" onClick={rotate}>Create webhook</button>
        </div>
      ) : (
        <>
          <p className="text-sm text-muted">
            In Better Stack, add an outgoing webhook integration with this URL and a custom header. Keep <code>relationships</code> in the
            body template so OpsRelay can tell which monitor fired.
          </p>
          <dl className="grid gap-2 text-xs sm:grid-cols-[9rem_1fr]">
            <dt className="label">Webhook URL</dt>
            <dd className="break-all font-mono">{window.location.origin}{shown.webhook_path}</dd>
            <dt className="label">Header</dt>
            <dd className="break-all font-mono">{shown.secret_header}: {shown.secret ?? "(shown once when created; rotate to get a new one)"}</dd>
            <dt className="label">Last delivery</dt>
            <dd>{last ? `${new Date(last.at).toLocaleString()} · ${last.result}${last.reason ? ` · ${last.reason}` : ""}` : "None yet"}</dd>
          </dl>
          {shown.secret && <p className="text-xs text-warn">Copy the secret now. It isn&apos;t shown again.</p>}
          <button type="button" className="btn" onClick={rotate}>Rotate secret</button>
        </>
      )}
      {error && <p className="text-sm text-crit" role="alert">{error}</p>}
    </section>
  );
}
