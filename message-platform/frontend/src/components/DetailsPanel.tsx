"use client";

import { motion } from "motion/react";
import { Bell, Copy, Link2, MapPin, Pin, Users, X } from "lucide-react";
import { useMemo } from "react";
import { extractLinks, firstName, relativeStamp } from "@/lib/format";
import type { History } from "@/lib/store";
import type { Conversation, User } from "@/lib/types";
import { Avatar, ConversationAvatar, Toggle } from "./primitives";

type Props = {
  conversation: Conversation;
  me: string;
  users: Record<string, User>;
  online: Record<string, true>;
  history: History | undefined;
  onClose: () => void;
  onMute: (muted: boolean) => void;
  onJump: (messageId: string) => void;
  onMessageUser: (userId: string) => void;
  onCopy: (text: string) => void;
};

function hueFor(text: string) {
  return text.split("").reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
}

function Block({ title, icon: Icon, count, children }: { title: string; icon: React.ComponentType<{ className?: string }>; count?: number; children: React.ReactNode }) {
  return (
    <section className="border-t border-line px-5 py-4">
      <h3 className="mb-3 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">
        <Icon className="h-3.5 w-3.5" /> {title}
        {count !== undefined && <span className="ml-auto font-medium normal-case tracking-normal">{count}</span>}
      </h3>
      {children}
    </section>
  );
}

export function DetailsPanel(p: Props) {
  const c = p.conversation;
  const other = c.kind === "direct" ? p.users[c.participant_ids.find((u) => u !== p.me) ?? ""] : undefined;
  const links = useMemo(() => {
    const seen = new Map<string, { url: string; sender: string; at: string }>();
    for (const m of [...(p.history?.messages ?? [])].reverse()) {
      if (m.deleted) continue;
      for (const url of extractLinks(m.body)) if (!seen.has(url)) seen.set(url, { url, sender: m.sender_id, at: m.created_at });
    }
    return [...seen.values()].slice(0, 6);
  }, [p.history]);
  const pinned = p.history?.pinned ?? [];

  return (
    <motion.aside
      initial={{ x: 24, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      exit={{ x: 24, opacity: 0 }}
      transition={{ type: "spring", stiffness: 380, damping: 36 }}
      className="scroll-thin absolute inset-y-0 right-0 z-30 flex w-full max-w-[340px] flex-col overflow-y-auto border-l border-line bg-surface shadow-float xl:static xl:z-auto xl:w-[320px] xl:flex-none xl:shadow-none"
      aria-label="Conversation details"
    >
      <div className="flex justify-end px-3 pt-3">
        <button className="icon-btn" onClick={p.onClose} aria-label="Close details"><X className="h-4 w-4" /></button>
      </div>
      <div className="flex flex-col items-center px-6 pb-5 text-center">
        <ConversationAvatar conversation={c} me={p.me} users={p.users} online={p.online} size="xl" />
        <h2 className="mt-4 font-display text-xl font-semibold tracking-tight">{c.title}</h2>
        {other ? (
          <>
            <p className="text-sm text-muted">@{other.handle} · {other.title}</p>
            <p className={`mt-2 inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${p.online[other.id] ? "bg-success/10 text-success" : "bg-sunken text-muted"}`}>
              <span className={`h-1.5 w-1.5 rounded-full ${p.online[other.id] ? "bg-success" : "bg-faint"}`} />
              {p.online[other.id] ? "Online now" : "Offline"}
            </p>
          </>
        ) : (
          <p className="text-sm text-muted">{c.topic || "Group chat"}</p>
        )}
      </div>

      {other && (
        <Block title="About" icon={Users}>
          <p className="text-sm leading-relaxed text-ink/90">{other.bio}</p>
          {other.location && <p className="mt-2 flex items-center gap-1.5 text-sm text-muted"><MapPin className="h-3.5 w-3.5" /> {other.location}</p>}
        </Block>
      )}

      {c.kind === "group" && (
        <Block title="Members" icon={Users} count={c.participant_ids.length}>
          <ul className="space-y-1">
            {c.participant_ids.map((id) => {
              const u = p.users[id];
              return (
                <li key={id}>
                  <button
                    disabled={id === p.me}
                    onClick={() => p.onMessageUser(id)}
                    className="flex w-full items-center gap-3 rounded-xl px-2 py-1.5 text-left transition enabled:hover:bg-sunken"
                    title={id === p.me ? undefined : `Message ${u?.name}`}
                  >
                    <Avatar user={u} size="sm" online={Boolean(p.online[id])} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">{u?.name}{id === p.me && <span className="text-faint"> (you)</span>}</span>
                      <span className="block truncate text-xs text-muted">{u?.title}</span>
                    </span>
                    {id === c.created_by && <span className="rounded-full bg-sunken px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted">Owner</span>}
                  </button>
                </li>
              );
            })}
          </ul>
        </Block>
      )}

      <Block title="Pinned" icon={Pin} count={pinned.length}>
        {pinned.length === 0 ? (
          <p className="text-sm text-muted">Pin important messages from the message menu.</p>
        ) : (
          <ul className="space-y-2">
            {pinned.map((m) => (
              <li key={m.id}>
                <button onClick={() => p.onJump(m.id)} className="w-full rounded-xl border border-line bg-sunken/50 p-3 text-left transition hover:border-accent/40">
                  <p className="text-xs font-semibold">{m.sender_id === p.me ? "You" : firstName(p.users[m.sender_id]?.name ?? "")} <span className="font-normal text-faint">· {relativeStamp(m.created_at)}</span></p>
                  <p className="mt-0.5 line-clamp-2 text-sm text-muted">{m.body}</p>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Block>

      <Block title="Shared links" icon={Link2} count={links.length}>
        {links.length === 0 ? (
          <p className="text-sm text-muted">Links shared in this chat appear here.</p>
        ) : (
          <ul className="grid grid-cols-2 gap-2">
            {links.map((l) => {
              let host = l.url;
              let path = "";
              try {
                const u = new URL(l.url);
                host = u.hostname.replace(/^www\./, "");
                path = u.pathname.replace(/^\//, "") || "/";
              } catch {
                /* keep raw */
              }
              const hue = hueFor(host);
              return (
                <li key={l.url}>
                  <a href={l.url} target="_blank" rel="noreferrer noopener" className="group block overflow-hidden rounded-xl border border-line transition hover:border-accent/40">
                    <div className="flex h-16 items-end p-2" style={{ background: `linear-gradient(135deg, hsl(${hue} 70% 60% / .9), hsl(${(hue + 50) % 360} 70% 45% / .9))` }}>
                      <Link2 className="h-4 w-4 text-white/90" />
                    </div>
                    <div className="p-2">
                      <p className="truncate text-xs font-semibold">{host}</p>
                      <p className="truncate text-[11px] text-muted">{path}</p>
                    </div>
                  </a>
                </li>
              );
            })}
          </ul>
        )}
      </Block>

      <Block title="Settings" icon={Bell}>
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-medium">Mute notifications</p>
            <p className="text-xs text-muted">No toasts; unread badge turns grey.</p>
          </div>
          <Toggle checked={c.muted} onChange={p.onMute} label="Mute notifications" />
        </div>
        <button onClick={() => p.onCopy(c.id)} className="mt-4 flex w-full items-center justify-between rounded-xl bg-sunken/60 px-3 py-2 text-left text-xs text-muted hover:text-ink">
          <span>Conversation ID <span className="font-mono text-ink">{c.id}</span></span>
          <Copy className="h-3.5 w-3.5" />
        </button>
      </Block>
    </motion.aside>
  );
}
