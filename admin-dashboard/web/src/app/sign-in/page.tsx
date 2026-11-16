"use client";

import { useEffect, useState } from "react";
import { type Account, selectWorkspace, send } from "@/lib/api";
import { Logo } from "@/components/ui";

/** Sign in, create an account, or (signed in without a workspace) create the first workspace. */
export default function SignInPage() {
  const [mode, setMode] = useState<"sign-in" | "sign-up" | "workspace" | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  const enter = (account: Account) => {
    if (!account.workspaces.length) return setMode("workspace");
    if (!account.workspace_id) selectWorkspace(account.workspaces[0].id);
    window.location.assign("/");
  };

  useEffect(() => {
    fetch("/api/v1/auth/me", { cache: "no-store" })
      .then(async (res) => (res.ok ? enter((await res.json()).data) : setMode("sign-in")))
      .catch(() => setMode("sign-in"));
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPending(true);
    setError("");
    try {
      if (mode === "workspace") {
        selectWorkspace((await send<{ id: string }>("POST", "/api/v1/workspaces", { name })).id);
        window.location.assign("/");
      } else {
        enter(await send<Account>("POST", `/api/v1/auth/${mode}`, { email, password }));
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPending(false);
    }
  };

  if (!mode) return null;
  const title = { "sign-in": "Sign in to OpsRelay", "sign-up": "Create your OpsRelay account", workspace: "Create a workspace" }[mode];
  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="panel w-full max-w-sm space-y-4 p-6">
        <Logo />
        <div>
          <h1 className="text-base font-semibold">{title}</h1>
          {mode === "workspace" && (
            <p className="mt-1 text-sm text-muted">A workspace holds your services and incidents. Only you can see it.</p>
          )}
        </div>
        {mode === "workspace" ? (
          <label className="block space-y-1.5">
            <span className="label">Workspace name</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} autoFocus />
          </label>
        ) : (
          <>
            <label className="block space-y-1.5">
              <span className="label">Email</span>
              <input className="input" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
            </label>
            <label className="block space-y-1.5">
              <span className="label">Password</span>
              <input
                className="input" type="password" minLength={8} required value={password} onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === "sign-up" ? "new-password" : "current-password"}
              />
            </label>
          </>
        )}
        {error && <p className="text-sm text-crit" role="alert">{error}</p>}
        <button type="submit" className="btn btn-primary w-full justify-center" disabled={pending}>
          {mode === "workspace" ? "Create workspace" : mode === "sign-up" ? "Create account" : "Sign in"}
        </button>
        {mode !== "workspace" && (
          <button
            type="button" className="w-full text-center text-xs text-muted hover:text-ink"
            onClick={() => { setMode(mode === "sign-in" ? "sign-up" : "sign-in"); setError(""); }}
          >
            {mode === "sign-in" ? "No account yet? Create one" : "Already have an account? Sign in"}
          </button>
        )}
      </form>
    </div>
  );
}
