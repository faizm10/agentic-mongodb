"use client";

import { AnimatePresence, motion } from "motion/react";
import { BellOff, Check, ChevronDown, ChevronsUpDown, FlaskConical, Hash, MessageCirclePlus, Moon, Search, Settings, Sun, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { firstName, relativeStamp } from "@/lib/format";
import type { ThemePref } from "@/lib/prefs";
import type { Conversation, Message, User } from "@/lib/types";
import { Avatar, ConversationAvatar, Wordmark, useDismiss } from "./primitives";

type Props = {
  me: User;
  users: Record<string, User>;
  conversations: Conversation[];
  loaded: boolean;
  activeId: string | null;
  online: Record<string, true>;
  typing: Record<string, Record<string, number>>;
  theme: ThemePref;
  onToggleTheme: () => void;
  onSelect: (id: string, messageId?: string) => void;
  onNew: () => void;
  onSettings: () => void;
  onSwitchUser: (id: string) => void;
};

function preview(c: Conversation, me: string, users: Record<string, User>, typingIds: string[]) {
  if (typingIds.length) {
    const who = c.kind === "group" ? `${firstName(users[typingIds[0]]?.name ?? "Someone")} is ` : "";
    return <span className="font-medium text-accent">{who}typing…</span>;
  }
  const m = c.last_message;
  if (!m) return <span className="italic">No messages yet</span>;
  const prefix = m.sender_id === me ? "You: " : c.kind === "group" ? `${firstName(users[m.sender_id]?.name ?? "")}: ` : "";
  return (
    <>
      {prefix}
      {m.deleted ? <span className="italic">Message deleted</span> : m.body}
    </>
  );
}

function Row({ c, active, me, users, online, typingIds, onClick }: {
  c: Conversation; active: boolean; me: string; users: Record<string, User>; online: Record<string, true>; typingIds: string[]; onClick: () => void;
}) {
  const unread = c.unread_count > 0 && !active;
  return (
    <button
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={`group relative flex w-full items-center gap-3 rounded-2xl px-2.5 py-2.5 text-left transition ${active ? "bg-accent-soft" : "hover:bg-sunken"}`}
    >
      {active && <motion.span layoutId="active-row" className="absolute left-0 top-3 bottom-3 w-1 rounded-full bg-accent" />}
      <ConversationAvatar conversation={c} me={me} users={users} online={online} />
      <span className="min-w-0 flex-1">
        <span className="flex items-baseline justify-between gap-2">
          <span className={`flex min-w-0 items-center gap-1 truncate text-[14.5px] ${unread ? "font-semibold text-ink" : "font-medium text-ink"}`}>
            <span className="truncate">{c.title}</span>
            {c.muted && <BellOff className="h-3 w-3 flex-none text-faint" aria-label="Muted" />}
          </span>
          <span className={`flex-none text-[11.5px] ${unread ? "font-semibold text-accent" : "text-faint"}`}>
            {c.last_message ? relativeStamp(c.last_message.created_at) : ""}
          </span>
        </span>
        <span className="mt-0.5 flex items-center justify-between gap-2">
          <span className={`truncate text-[13px] ${unread ? "text-ink/80" : "text-muted"}`}>{preview(c, me, users, typingIds)}</span>
          {unread && (
            <motion.span
              initial={{ scale: 0.4 }}
              animate={{ scale: 1 }}
              className={`flex h-5 min-w-5 flex-none items-center justify-center rounded-full px-1.5 text-[11px] font-bold text-white ${c.muted ? "bg-faint" : "bg-accent"}`}
              aria-label={`${c.unread_count} unread`}
            >
              {c.unread_count > 99 ? "99+" : c.unread_count}
            </motion.span>
          )}
        </span>
      </span>
    </button>
  );
}

function Section({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="mt-3">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-faint hover:text-muted" aria-expanded={open}>
        <ChevronDown className={`h-3.5 w-3.5 transition ${open ? "" : "-rotate-90"}`} />
        {title}
        <span className="ml-auto font-medium normal-case tracking-normal">{count}</span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
            <div className="space-y-0.5">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function UserSwitcher({ me, users, online, onSwitch }: { me: User; users: Record<string, User>; online: Record<string, true>; onSwitch: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useDismiss(open, () => setOpen(false));
  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-3 rounded-2xl p-2 text-left transition hover:bg-sunken" aria-haspopup="menu" aria-expanded={open}>
        <Avatar user={me} online />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-semibold">{me.name}</span>
          <span className="block truncate text-xs text-muted">{me.title}</span>
        </span>
        <ChevronsUpDown className="h-4 w-4 text-faint" />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            role="menu"
            initial={{ opacity: 0, y: -6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.98 }}
            className="absolute left-0 right-0 top-full z-30 mt-2 rounded-2xl bg-raised p-1.5 shadow-pop"
          >
            <div className="mx-1 mb-1 flex items-center gap-2 rounded-xl bg-warning/10 px-2.5 py-2 text-[11.5px] font-medium text-warning">
              <FlaskConical className="h-3.5 w-3.5 flex-none" /> Demo only: switch identity without signing in
            </div>
            {Object.values(users).map((u) => (
              <button
                key={u.id}
                role="menuitemradio"
                aria-checked={u.id === me.id}
                onClick={() => {
                  setOpen(false);
                  if (u.id !== me.id) onSwitch(u.id);
                }}
                className="flex w-full items-center gap-2.5 rounded-xl px-2 py-1.5 text-left hover:bg-sunken"
              >
                <Avatar user={u} size="sm" online={Boolean(online[u.id])} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{u.name}</span>
                  <span className="block truncate text-[11.5px] text-muted">{u.title}</span>
                </span>
                {u.id === me.id && <Check className="h-4 w-4 text-accent" />}
              </button>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function Sidebar(p: Props) {
  const [query, setQuery] = useState("");
  const [onlyUnread, setOnlyUnread] = useState(false);
  const [hits, setHits] = useState<Message[]>([]);
  const q = query.trim().toLowerCase();

  useEffect(() => {
    if (q.length < 2) {
      setHits([]);
      return;
    }
    const t = window.setTimeout(() => {
      api.search(p.me.id, q).then((r) => setHits(r.messages)).catch(() => setHits([]));
    }, 200);
    return () => window.clearTimeout(t);
  }, [q, p.me.id]);

  const filtered = useMemo(
    () =>
      p.conversations.filter(
        (c) =>
          (!onlyUnread || c.unread_count > 0 || c.id === p.activeId) &&
          (!q || c.title.toLowerCase().includes(q) || c.participants.some((u) => u.name.toLowerCase().includes(q))),
      ),
    [p.conversations, onlyUnread, q, p.activeId],
  );
  const groups = filtered.filter((c) => c.kind === "group");
  const direct = filtered.filter((c) => c.kind === "direct");
  const totalUnread = p.conversations.reduce((n, c) => n + (c.id === p.activeId ? 0 : c.unread_count), 0);
  const typingIds = (cid: string) => Object.keys(p.typing[cid] ?? {}).filter((u) => u !== p.me.id);
  const titleOf = (cid: string) => p.conversations.find((c) => c.id === cid)?.title ?? "Conversation";

  const row = (c: Conversation) => (
    <Row key={c.id} c={c} active={c.id === p.activeId} me={p.me.id} users={p.users} online={p.online} typingIds={typingIds(c.id)} onClick={() => p.onSelect(c.id)} />
  );

  return (
    <aside className="flex h-full w-full flex-col border-r border-line bg-surface md:w-[340px] md:flex-none" aria-label="Conversations">
      <div className="flex items-center justify-between px-4 pb-2 pt-4">
        <Wordmark />
        <div className="flex items-center gap-0.5">
          <button className="icon-btn" onClick={p.onToggleTheme} aria-label="Toggle theme" title="Toggle theme">
            <Sun className="hidden h-[18px] w-[18px] dark:block" />
            <Moon className="h-[18px] w-[18px] dark:hidden" />
          </button>
          <button className="icon-btn" onClick={p.onSettings} aria-label="Settings" title="Settings">
            <Settings className="h-[18px] w-[18px]" />
          </button>
        </div>
      </div>

      <div className="px-3">
        <UserSwitcher me={p.me} users={p.users} online={p.online} onSwitch={p.onSwitchUser} />
      </div>

      <div className="flex items-center gap-2 px-4 pb-1 pt-3">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-faint" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && setQuery("")}
            placeholder="Search people and messages"
            aria-label="Search conversations and messages"
            className="input h-10 pl-9 pr-8"
          />
          {query && (
            <button className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full p-1 text-faint hover:text-ink" onClick={() => setQuery("")} aria-label="Clear search">
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
        <button className="icon-btn h-10 w-10 bg-accent text-white hover:bg-accent hover:text-white hover:brightness-110" onClick={p.onNew} aria-label="New conversation" title="New conversation">
          <MessageCirclePlus className="h-[18px] w-[18px]" />
        </button>
      </div>

      <div className="flex gap-1.5 px-4 pt-2">
        {[
          [false, "All"],
          [true, `Unread${totalUnread ? ` · ${totalUnread}` : ""}`],
        ].map(([value, label]) => (
          <button
            key={String(value)}
            onClick={() => setOnlyUnread(value as boolean)}
            aria-pressed={onlyUnread === value}
            className={`rounded-full px-3 py-1 text-xs font-semibold transition ${onlyUnread === value ? "bg-ink text-canvas" : "text-muted hover:bg-sunken hover:text-ink"}`}
          >
            {label as string}
          </button>
        ))}
      </div>

      <nav className="scroll-thin flex-1 overflow-y-auto px-2 pb-4">
        {!p.loaded ? (
          <div className="space-y-2 px-2 pt-4" aria-label="Loading conversations">
            {Array.from({ length: 6 }, (_, i) => (
              <div key={i} className="flex animate-pulse items-center gap-3 py-1.5">
                <div className="h-10 w-10 rounded-full bg-sunken" />
                <div className="flex-1 space-y-2">
                  <div className="h-3 w-1/2 rounded bg-sunken" />
                  <div className="h-3 w-4/5 rounded bg-sunken" />
                </div>
              </div>
            ))}
          </div>
        ) : (
          <>
            {direct.length > 0 && <Section title="Direct messages" count={direct.length}>{direct.map(row)}</Section>}
            {groups.length > 0 && <Section title="Group chats" count={groups.length}>{groups.map(row)}</Section>}
            {hits.length > 0 && (
              <Section title="Messages" count={hits.length}>
                {hits.slice(0, 12).map((m) => (
                  <button key={m.id} onClick={() => p.onSelect(m.conversation_id, m.id)} className="flex w-full items-start gap-3 rounded-2xl px-2.5 py-2 text-left hover:bg-sunken">
                    <Avatar user={p.users[m.sender_id]} size="sm" />
                    <span className="min-w-0 flex-1">
                      <span className="flex justify-between gap-2 text-xs text-muted">
                        <span className="flex items-center gap-1 truncate font-medium text-ink">
                          <Hash className="h-3 w-3" /> {titleOf(m.conversation_id)}
                        </span>
                        <span className="flex-none text-faint">{relativeStamp(m.created_at)}</span>
                      </span>
                      <span className="line-clamp-2 text-[13px] text-muted">{m.body}</span>
                    </span>
                  </button>
                ))}
              </Section>
            )}
            {filtered.length === 0 && hits.length === 0 && (
              <div className="px-6 py-12 text-center">
                <p className="text-sm font-medium">{q ? "No matches" : onlyUnread ? "You're all caught up ✨" : "No conversations yet"}</p>
                <p className="mt-1 text-xs text-muted">{q ? "Try a name or a word from a message." : "Start a new conversation with the button above."}</p>
              </div>
            )}
          </>
        )}
      </nav>
    </aside>
  );
}
