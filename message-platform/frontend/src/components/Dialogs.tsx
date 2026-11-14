"use client";

import { Activity, Check, CheckCircle2, FlaskConical, Loader2, Monitor, Moon, Search, Sun, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { ThemePref } from "@/lib/prefs";
import type { ConnectionState, ProbeReport, User } from "@/lib/types";
import { Avatar, Dialog, Toggle } from "./primitives";

/* ----------------------------------------------------- new conversation */
export function NewConversationDialog({
  open,
  onClose,
  me,
  users,
  online,
  onCreate,
}: {
  open: boolean;
  onClose: () => void;
  me: string;
  users: Record<string, User>;
  online: Record<string, true>;
  onCreate: (ids: string[], title: string, emoji: string) => Promise<void>;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const [q, setQ] = useState("");
  const [title, setTitle] = useState("");
  const [emoji, setEmoji] = useState("✨");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setSelected([]);
      setQ("");
      setTitle("");
      setError(null);
    }
  }, [open]);

  const people = useMemo(
    () => Object.values(users).filter((u) => u.id !== me && (!q || `${u.name} ${u.title}`.toLowerCase().includes(q.toLowerCase()))),
    [users, me, q],
  );
  const isGroup = selected.length > 1;
  const valid = selected.length === 1 || (isGroup && title.trim().length > 0);

  const submit = async () => {
    if (!valid || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onCreate(selected, title.trim(), emoji);
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} title="New conversation" description="Pick one person for a direct message, or several for a group.">
      <div className="space-y-4 px-6 py-5">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-faint" />
          <input className="input pl-9" placeholder="Search people" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search people" />
        </div>
        <ul className="space-y-1" role="listbox" aria-multiselectable="true">
          {people.map((u) => {
            const on = selected.includes(u.id);
            return (
              <li key={u.id}>
                <button
                  role="option"
                  aria-selected={on}
                  onClick={() => setSelected((s) => (on ? s.filter((x) => x !== u.id) : [...s, u.id]))}
                  className={`flex w-full items-center gap-3 rounded-2xl px-2.5 py-2 text-left transition ${on ? "bg-accent-soft" : "hover:bg-sunken"}`}
                >
                  <Avatar user={u} size="md" online={Boolean(online[u.id])} />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold">{u.name}</span>
                    <span className="block text-xs text-muted">{u.title} · {online[u.id] ? "Online" : "Offline"}</span>
                  </span>
                  <span className={`flex h-5 w-5 items-center justify-center rounded-full border transition ${on ? "border-accent bg-accent text-white" : "border-line"}`}>
                    {on && <Check className="h-3.5 w-3.5" />}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
        {isGroup && (
          <div className="flex gap-2">
            <select value={emoji} onChange={(e) => setEmoji(e.target.value)} className="input w-16 px-2 text-center text-lg" aria-label="Group emoji">
              {["✨", "🚀", "🎨", "📣", "🧪", "☕", "🏔️", "🎉", "💼", "📚"].map((e) => <option key={e}>{e}</option>)}
            </select>
            <input className="input" placeholder="Group name" maxLength={60} value={title} onChange={(e) => setTitle(e.target.value)} onKeyDown={(e) => e.key === "Enter" && submit()} aria-label="Group name" />
          </div>
        )}
        {error && <p className="rounded-xl bg-danger/10 px-3 py-2 text-sm text-danger">{error}</p>}
        <div className="flex items-center justify-between gap-3 pt-1">
          <p className="text-xs text-muted">{selected.length === 0 ? "Nobody selected" : isGroup ? `Group of ${selected.length + 1}` : "Direct message"}</p>
          <button className="btn-primary" disabled={!valid || busy} onClick={submit}>
            {busy && <Loader2 className="h-4 w-4 animate-spin" />}
            {isGroup ? "Create group" : "Start chat"}
          </button>
        </div>
      </div>
    </Dialog>
  );
}

/* --------------------------------------------------------------- settings */
export function SettingsDialog({
  open,
  onClose,
  me,
  users,
  theme,
  setTheme,
  onSwitchUser,
  notifications,
  setNotifications,
  simulateRetry,
  setSimulateRetry,
  connection,
}: {
  open: boolean;
  onClose: () => void;
  me: string;
  users: Record<string, User>;
  theme: ThemePref;
  setTheme: (t: ThemePref) => void;
  onSwitchUser: (id: string) => void;
  notifications: boolean;
  setNotifications: (v: boolean) => void;
  simulateRetry: boolean;
  setSimulateRetry: (v: boolean) => void;
  connection: ConnectionState;
}) {
  const [probe, setProbe] = useState<ProbeReport | null>(null);
  const [probing, setProbing] = useState(false);
  const [probeError, setProbeError] = useState<string | null>(null);

  const runProbe = async () => {
    setProbing(true);
    setProbeError(null);
    try {
      setProbe(await api.probe());
    } catch {
      setProbeError("Could not reach the monitoring endpoint.");
    } finally {
      setProbing(false);
    }
  };

  const themes: [ThemePref, string, typeof Sun][] = [["light", "Light", Sun], ["dark", "Dark", Moon], ["system", "System", Monitor]];

  return (
    <Dialog open={open} onClose={onClose} title="Settings" description="Preferences are saved in this browser." width="max-w-lg">
      <div className="divide-y divide-line">
        <section className="px-6 py-5">
          <h3 className="text-sm font-semibold">Appearance</h3>
          <div className="mt-3 grid grid-cols-3 gap-2" role="radiogroup" aria-label="Theme">
            {themes.map(([value, label, Icon]) => (
              <button
                key={value}
                role="radio"
                aria-checked={theme === value}
                onClick={() => setTheme(value)}
                className={`flex flex-col items-center gap-2 rounded-2xl border p-3 text-sm font-medium transition ${theme === value ? "border-accent bg-accent-soft text-accent-ink" : "border-line hover:bg-sunken"}`}
              >
                <Icon className="h-5 w-5" /> {label}
              </button>
            ))}
          </div>
        </section>

        <section className="px-6 py-5">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">Demo account</h3>
            <span className="inline-flex items-center gap-1 rounded-full bg-warning/10 px-2 py-0.5 text-[11px] font-semibold text-warning"><FlaskConical className="h-3 w-3" /> Demo only, not real sign-in</span>
          </div>
          <p className="mt-1 text-xs text-muted">Each browser tab can be a different person. Open a second window to chat with yourself in real time.</p>
          <div className="mt-3 grid grid-cols-2 gap-2">
            {Object.values(users).map((u) => (
              <button
                key={u.id}
                onClick={() => { onSwitchUser(u.id); onClose(); }}
                className={`flex items-center gap-2.5 rounded-2xl border p-2 text-left transition ${u.id === me ? "border-accent bg-accent-soft" : "border-line hover:bg-sunken"}`}
              >
                <Avatar user={u} size="sm" />
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">{u.name}</span>
                  <span className="block truncate text-[11px] text-muted">{u.title}</span>
                </span>
              </button>
            ))}
          </div>
        </section>

        <section className="space-y-4 px-6 py-5">
          <h3 className="text-sm font-semibold">Notifications</h3>
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-sm">In-app message toasts</p>
              <p className="text-xs text-muted">Show a toast when a message arrives in another conversation.</p>
            </div>
            <Toggle checked={notifications} onChange={setNotifications} label="In-app message toasts" />
          </div>
        </section>

        <section className="space-y-4 px-6 py-5">
          <div className="flex items-center justify-between">
            <h3 className="flex items-center gap-2 text-sm font-semibold"><Activity className="h-4 w-4 text-accent" /> OpsRelay diagnostics</h3>
            <span className={`flex items-center gap-1.5 text-xs font-medium ${connection === "open" ? "text-success" : "text-warning"}`}>
              <span className={`h-2 w-2 rounded-full ${connection === "open" ? "bg-success" : "animate-pulse bg-warning"}`} />
              {connection === "open" ? "Live connection" : "Reconnecting"}
            </span>
          </div>
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-sm">Simulate a flaky network</p>
              <p className="text-xs text-muted">Every send is retried once with the same idempotency key, as a client would after a lost response. Healthy servers store it once.</p>
            </div>
            <Toggle checked={simulateRetry} onChange={setSimulateRetry} label="Simulate a flaky network" />
          </div>
          <div className="rounded-2xl border border-line p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-medium">Functional messaging probe</p>
                <p className="text-xs text-muted">Runs real business rules in an isolated database. Sends no real messages.</p>
              </div>
              <button className="btn-ghost h-9 border border-line" onClick={runProbe} disabled={probing}>
                {probing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Activity className="h-4 w-4" />} Run
              </button>
            </div>
            {probeError && <p className="mt-3 text-sm text-danger">{probeError}</p>}
            {probe && (
              <div className="mt-3 space-y-1.5">
                <p className={`text-sm font-semibold ${probe.status === "healthy" ? "text-success" : "text-danger"}`}>
                  {probe.status === "healthy" ? "Healthy" : "Unhealthy"} · {probe.summary}
                </p>
                {probe.checks.map((c) => (
                  <div key={c.check} className="flex items-start gap-2 text-xs">
                    {c.status === "pass" ? <CheckCircle2 className="mt-px h-3.5 w-3.5 flex-none text-success" /> : <XCircle className="mt-px h-3.5 w-3.5 flex-none text-danger" />}
                    <span>
                      <span className="font-mono">{c.check}</span>
                      {c.failure_reason && <span className="block text-danger">{c.failure_reason}</span>}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>
      </div>
    </Dialog>
  );
}
