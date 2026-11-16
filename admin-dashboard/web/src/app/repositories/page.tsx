"use client";

import { useEffect, useState } from "react";
import { GitBranch, Trash2 } from "lucide-react";
import { ErrorPanel, PageHeader, Shell, Skeleton } from "@/components/ui";
import { send, usePoll } from "@/lib/api";

type Connection = { connected: boolean; login?: string; connected_at?: string };
type Mapping = { service_id: string; service_name: string; repo: string; base_branch: string; deployed_url: string; workflow: string | null };
type Repo = { full_name: string; default_branch: string; private: boolean };
type Options = { branches: string[]; workflows: { file: string; name: string }[] };

export default function Repositories() {
  const github = usePoll<Connection>("/api/v1/github", 60_000);
  const mapped = usePoll<Mapping[]>("/api/v1/repositories", 60_000);
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
