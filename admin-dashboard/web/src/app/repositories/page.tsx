"use client";

import { useEffect, useState } from "react";
import { GitBranch, Trash2 } from "lucide-react";
import { ErrorPanel, PageHeader, Shell, Skeleton } from "@/components/ui";
import { type AgentMode, send, usePoll } from "@/lib/api";

type Connection = { connected: boolean; login?: string; connected_at?: string };
type Mapping = { service_id: string; service_name: string; repo: string; base_branch: string; deployed_url: string; workflow: string | null; betterstack_monitor_id: string | null;
  dependabot?: { checked_at: string; result: string; reason?: string; open_alerts?: number } | null;
  agent: { enabled: boolean; mode: AgentMode; app: string | null } };
type AgentConfig = { worker_repo: string | null; apps: string[] };
type BetterStack = { configured: boolean; webhook_path?: string; secret_header?: string; secret?: string;
  last_delivery?: { at: string; result: string; reason?: string; incident_id?: string } | null };
type Repo = { full_name: string; default_branch: string; private: boolean };
type Options = { branches: string[]; workflows: { file: string; name: string }[] };

export default function Repositories() {
  const github = usePoll<Connection>("/api/v1/github", 60_000);
  const mapped = usePoll<Mapping[]>("/api/v1/repositories", 60_000);
  const agentConfig = usePoll<AgentConfig>("/api/v1/agent/config", 300_000);
  const [error, setError] = useState("");
  const remove = async (m: Mapping) => {
    try {
      await send("DELETE", `/api/v1/repositories/${encodeURIComponent(m.service_id)}`);
      mapped.reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const loadError = github.error ?? mapped.error;
  return (
    <Shell meta={github.meta} updatedAt={github.updatedAt} error={loadError} intervalMs={60_000}>
      <PageHeader title="Repositories" sub="Each service maps to the GitHub repository, branch and deployment the repair agent works on." />
      {loadError && !github.data ? <ErrorPanel error={loadError} onRetry={github.reload} />
        : !github.data || !mapped.data ? <Skeleton className="h-56" />
        : (
          <div className="space-y-6">
            <GitHubPanel connection={github.data} onChange={github.reload} />
            <BetterStackPanel />
            <section className="panel">
              <h2 className="border-b border-line px-5 py-3 text-sm font-semibold">Services</h2>
              {mapped.data.length === 0 ? (
                <p className="px-5 py-4 text-sm text-muted">No repositories mapped yet.</p>
              ) : (
                <ul className="divide-y divide-line">
                  {mapped.data.map((m) => (
                    <li key={m.service_id} className="flex flex-wrap items-center gap-x-6 gap-y-1 px-5 py-3 text-sm">
                      <span className="min-w-36 font-medium">{m.service_name}</span>
                      <span className="flex items-center gap-1.5 font-mono text-xs text-muted">
                        <GitBranch className="h-3.5 w-3.5" aria-hidden="true" />{m.repo} · {m.base_branch}
                      </span>
                      <a href={m.deployed_url} className="truncate text-xs text-accent hover:underline" target="_blank" rel="noreferrer">{m.deployed_url}</a>
                      <span className="text-xs text-faint">{m.workflow ? `Check: ${m.workflow}` : "No workflow check"}</span>
                      <span className={`text-xs ${m.dependabot?.result === "skipped" ? "text-warn" : "text-faint"}`} title={m.dependabot?.reason}>
                        {!m.dependabot ? "Dependabot: not checked yet"
                          : m.dependabot.result === "skipped" ? "Dependabot: can't read alerts (token needs Dependabot alerts: read)"
                          : `Dependabot: ${m.dependabot.open_alerts} open alert${m.dependabot.open_alerts === 1 ? "" : "s"}`}
                      </span>
                      <MonitorField mapping={m} onSaved={mapped.reload} />
                      {agentConfig.data && <AgentControl mapping={m} config={agentConfig.data} onSaved={mapped.reload} />}
                      <button type="button" className="btn ml-auto px-2" aria-label={`Remove ${m.service_name}`} onClick={() => remove(m)}>
                        <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {error && <p className="px-5 pb-3 text-sm text-crit" role="alert">{error}</p>}
            </section>
            {github.data.connected && <AddRepository onAdded={mapped.reload} />}
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
            with Contents, Pull requests and Actions (read) on the chosen repositories is enough. It stays on the server.
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

function AddRepository({ onAdded }: { onAdded: () => void }) {
  const repos = usePoll<Repo[]>("/api/v1/github/repos", 300_000);
  const [repo, setRepo] = useState("");
  const [options, setOptions] = useState<Options | null>(null);
  const [form, setForm] = useState({ service_name: "", base_branch: "", deployed_url: "", workflow: "" });
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({ ...form, [k]: e.target.value });

  useEffect(() => {
    setOptions(null);
    if (!repo) return;
    const picked = repos.data?.find((r) => r.full_name === repo);
    setForm((f) => ({ ...f, base_branch: picked?.default_branch ?? "", workflow: "", service_name: f.service_name || repo.split("/")[1] }));
    let live = true;
    send<Options>("GET", `/api/v1/github/repos/${repo}`).then((o) => live && setOptions(o)).catch((e) => live && setError((e as Error).message));
    return () => { live = false; };
  }, [repo]); // not repos.data: a refetched list must not reset the form

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPending(true);
    setError("");
    try {
      await send("POST", "/api/v1/repositories", { repo, ...form, workflow: form.workflow || null });
      setRepo("");
      setForm({ service_name: "", base_branch: "", deployed_url: "", workflow: "" });
      onAdded();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPending(false);
    }
  };

  return (
    <form className="panel space-y-4 p-5" onSubmit={submit}>
      <h2 className="text-sm font-semibold">Add a repository</h2>
      {repos.error && <p className="text-sm text-crit" role="alert">{repos.error.message}</p>}
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="space-y-1.5">
          <span className="label">Repository</span>
          <select className="input" value={repo} onChange={(e) => setRepo(e.target.value)} required>
            <option value="">{repos.data ? "Choose a repository" : "Loading repositories…"}</option>
            {repos.data?.map((r) => <option key={r.full_name} value={r.full_name}>{r.full_name}{r.private ? " (private)" : ""}</option>)}
          </select>
        </label>
        <label className="space-y-1.5">
          <span className="label">Service name</span>
          <input className="input" value={form.service_name} onChange={set("service_name")} required maxLength={60} />
        </label>
        <label className="space-y-1.5">
          <span className="label">Base branch</span>
          <select className="input" value={form.base_branch} onChange={set("base_branch")} required disabled={!options}>
            {(options?.branches ?? [form.base_branch]).map((b) => <option key={b} value={b}>{b}</option>)}
          </select>
        </label>
        <label className="space-y-1.5">
          <span className="label">Deployed URL</span>
          <input className="input" type="url" placeholder="https://" value={form.deployed_url} onChange={set("deployed_url")} required />
        </label>
        <label className="space-y-1.5 sm:col-span-2">
          <span className="label">Workflow check (optional)</span>
          <select className="input" value={form.workflow} onChange={set("workflow")} disabled={!options}>
            <option value="">None</option>
            {options?.workflows.map((w) => <option key={w.file} value={w.file}>{w.name} ({w.file})</option>)}
          </select>
        </label>
      </div>
      {error && <p className="text-sm text-crit" role="alert">{error}</p>}
      <button type="submit" className="btn btn-primary" disabled={pending || !options}>
        Add repository
      </button>
    </form>
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
          <p className="text-sm text-muted">Get a webhook URL for this workspace, then map each service to its Better Stack monitor below.</p>
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

function MonitorField({ mapping, onSaved }: { mapping: Mapping; onSaved: () => void }) {
  const [value, setValue] = useState(mapping.betterstack_monitor_id ?? "");
  const [error, setError] = useState("");
  const dirty = value !== (mapping.betterstack_monitor_id ?? "");
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await send("PUT", `/api/v1/repositories/${encodeURIComponent(mapping.service_id)}/betterstack`, { monitor_id: value || null });
      setError("");
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    }
  };
  return (
    <form onSubmit={save} className="flex items-center gap-1.5 text-xs">
      <label className="flex items-center gap-1.5">
        <span className="text-faint">Better Stack monitor</span>
        <input className="input w-28 py-1 font-mono text-xs" inputMode="numeric" pattern="[0-9]*" placeholder="ID" value={value} onChange={(e) => setValue(e.target.value)} />
      </label>
      {dirty && <button type="submit" className="btn px-2">Save</button>}
      {error && <span className="text-crit" role="alert">{error}</span>}
    </form>
  );
}

/** The repair agent is off until switched on, and then only reports (no PR) unless draft PRs are chosen. */
function AgentControl({ mapping, config, onSaved }: { mapping: Mapping; config: AgentConfig; onSaved: () => void }) {
  const [mode, setMode] = useState<AgentMode>(mapping.agent.mode);
  const [app, setApp] = useState(mapping.agent.app ?? config.apps[0] ?? "");
  const [error, setError] = useState("");
  const save = async (enabled: boolean, next: { mode?: AgentMode; app?: string } = {}) => {
    try {
      await send("PUT", `/api/v1/repositories/${encodeURIComponent(mapping.service_id)}/agent`, { enabled, mode: next.mode ?? mode, app: next.app ?? app });
      setError("");
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const on = mapping.agent.enabled;
  return (
    <div className="flex w-full flex-wrap items-center gap-2 text-xs">
      <button
        type="button" role="switch" aria-checked={on} onClick={() => save(!on)}
        className={`relative h-5 w-9 flex-none rounded-full border transition ${on ? "border-accent bg-accent" : "border-line-strong bg-raised"}`}
      >
        <span className={`absolute top-0.5 h-3.5 w-3.5 rounded-full bg-white transition-all ${on ? "left-[18px]" : "left-0.5"}`} />
        <span className="sr-only">Repair agent for {mapping.service_name}</span>
      </button>
      <span className="text-muted">Repair agent {on ? "on" : "off"}</span>
      <label className="flex items-center gap-1.5">
        <span className="text-faint">Mode</span>
        <select className="input w-auto py-1 text-xs" value={mode}
          onChange={(e) => { const m = e.target.value as AgentMode; setMode(m); if (on) save(true, { mode: m }); }}>
          <option value="report_only">Report only</option>
          <option value="draft_pr">Open draft PRs</option>
        </select>
      </label>
      <label className="flex items-center gap-1.5">
        <span className="text-faint">Code area</span>
        <select className="input w-auto py-1 text-xs" value={app}
          onChange={(e) => { setApp(e.target.value); if (on) save(true, { app: e.target.value }); }}>
          {config.apps.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
      </label>
      {error && <span className="basis-full text-crit" role="alert">{error}</span>}
    </div>
  );
}
