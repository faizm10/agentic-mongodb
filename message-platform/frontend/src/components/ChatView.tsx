"use client";

import { AnimatePresence, motion } from "motion/react";
import {
  AlertCircle, ArrowDown, Check, CheckCheck, ChevronLeft, Clock, Copy, CornerUpLeft, MoreHorizontal,
  PanelRight, Pencil, Pin, PinOff, RefreshCw, SmilePlus, Trash2, WifiOff,
} from "lucide-react";
import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { dayLabel, firstName, isJumboEmoji, parseTs, sameDay, splitLinks, timeOf } from "@/lib/format";
import type { History } from "@/lib/store";
import type { ConnectionState, Conversation, Message, User } from "@/lib/types";
import { Composer, EmojiGrid, type ComposerHandle } from "./Composer";
import { Avatar, ConversationAvatar, MenuItem, useDismiss } from "./primitives";

const QUICK_REACTIONS = ["👍", "❤️", "😂", "🎉", "😮", "🙏"];
const GROUP_WINDOW_MS = 5 * 60_000;

type Handlers = {
  onSend: (body: string, replyTo: Message | null) => void;
  onEdit: (id: string, body: string) => void;
  onDelete: (message: Message) => void;
  onReact: (id: string, emoji: string) => void;
  onPin: (id: string) => void;
  onRetry: (message: Message) => void;
  onDiscard: (message: Message) => void;
  onTyping: (isTyping: boolean) => void;
  onBack: () => void;
  onToggleDetails: () => void;
  onReload: () => void;
  onCopy: (text: string) => void;
};

type Props = Handlers & {
  conversation: Conversation;
  me: string;
  users: Record<string, User>;
  online: Record<string, true>;
  typingIds: string[];
  history: History | undefined;
  detailsOpen: boolean;
  connection: ConnectionState;
  highlightId: string | null;
};

/* ------------------------------------------------------------ status ticks */
function StatusIcon({ m }: { m: Message }) {
  if (m.local === "sending") return <Clock className="h-3.5 w-3.5" aria-label="Sending" />;
  if (m.local === "failed") return <AlertCircle className="h-3.5 w-3.5 text-danger" aria-label="Failed" />;
  if (m.status === "read") return <CheckCheck className="h-3.5 w-3.5 text-accent" aria-label="Read" />;
  if (m.status === "delivered") return <CheckCheck className="h-3.5 w-3.5" aria-label="Delivered" />;
  return <Check className="h-3.5 w-3.5" aria-label="Sent" />;
}

function statusLabel(m: Message, conversation: Conversation) {
  if (m.local === "sending") return "Sending…";
  if (m.status === "read") {
    if (conversation.kind === "group") return `Read by ${m.receipts.length}`;
    const at = m.receipts[0]?.read_at;
    return at ? `Read ${timeOf(at)}` : "Read";
  }
  if (m.status === "delivered") return "Delivered";
  if (conversation.kind === "group") {
    const read = m.receipts.filter((r) => r.status === "read").length;
    if (read) return `Read by ${read} of ${m.receipts.length}`;
  }
  return "Sent";
}

/* ----------------------------------------------------------------- bubble */
type BubbleProps = {
  m: Message;
  mine: boolean;
  first: boolean;
  last: boolean;
  showAvatar: boolean;
  showName: boolean;
  isGroup: boolean;
  me: string;
  users: Record<string, User>;
  highlighted: boolean;
  footer: React.ReactNode;
} & Pick<Handlers, "onReact" | "onPin" | "onRetry" | "onDiscard" | "onCopy" | "onDelete"> & {
  onReply: (m: Message) => void;
  onStartEdit: (m: Message) => void;
};

function Bubble(p: BubbleProps) {
  const { m, mine } = p;
  const [menu, setMenu] = useState<"none" | "react" | "more" | "picker">("none");
  const close = useCallback(() => setMenu("none"), []);
  const ref = useDismiss(menu !== "none", close);
  const author = p.users[m.sender_id];
  const jumbo = !m.deleted && isJumboEmoji(m.body);
  const live = !m.local && !m.deleted;

  const shape = mine
    ? `rounded-[20px] ${p.first ? "" : "rounded-tr-[6px]"} ${p.last ? "" : "rounded-br-[6px]"}`
    : `rounded-[20px] ${p.first ? "" : "rounded-tl-[6px]"} ${p.last ? "" : "rounded-bl-[6px]"}`;

  return (
    <motion.div
      id={`msg-${m.id}`}
      layout="position"
      initial={{ opacity: 0, y: 8, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: "spring", stiffness: 500, damping: 38 }}
      className={`group/msg flex gap-2 ${mine ? "justify-end" : "justify-start"} ${p.first ? "mt-3" : "mt-[3px]"}`}
      onContextMenu={(e) => {
        if (!live) return;
        e.preventDefault();
        setMenu("more");
      }}
    >
      {!mine && p.isGroup && (
        <div className={`w-8 flex-none self-end ${p.showAvatar ? "pb-[22px]" : ""}`}>{p.showAvatar && <Avatar user={author} size="sm" />}</div>
      )}
      <div className={`flex max-w-[min(78%,560px)] flex-col ${mine ? "items-end" : "items-start"}`}>
        {p.showName && <span className="mb-1 ml-3 text-xs font-semibold" style={{ color: `hsl(${author?.avatar_hue ?? 220} 60% 52%)` }}>{author?.name}</span>}
        <div className={`relative flex items-center gap-1.5 ${mine ? "flex-row-reverse" : ""}`} ref={ref}>
          {m.deleted ? (
            <div className={`${shape} border border-dashed border-line px-3.5 py-2 text-sm italic text-faint`}>This message was deleted</div>
          ) : jumbo ? (
            <div className={`text-5xl leading-tight ${m.local ? "opacity-60" : ""}`}>{m.body}</div>
          ) : (
            <div
              className={`${shape} relative px-3.5 py-2 text-[15px] leading-[1.4] shadow-sm transition ${
                mine ? "bg-accent text-white" : "bg-bubble text-bubble-ink"
              } ${m.local ? "opacity-70" : ""} ${p.highlighted ? "ring-4 ring-warning/50" : ""}`}
            >
              {m.reply_to && (
                <div className={`mb-1.5 rounded-xl border-l-[3px] px-2.5 py-1.5 text-[13px] ${mine ? "border-white/70 bg-white/15" : "border-accent bg-surface/70"}`}>
                  <p className={`text-xs font-semibold ${mine ? "text-white" : "text-accent"}`}>{m.reply_to.sender_id === p.me ? "You" : p.users[m.reply_to.sender_id]?.name}</p>
                  <p className={`line-clamp-2 ${mine ? "text-white/85" : "text-muted"}`}>{m.reply_to.deleted ? "Deleted message" : m.reply_to.body}</p>
                </div>
              )}
              <p className="whitespace-pre-wrap break-words">
                {splitLinks(m.body).map((part, i) =>
                  part.href ? (
                    <a key={i} href={part.href} target="_blank" rel="noreferrer noopener" className={`underline decoration-1 underline-offset-2 ${mine ? "text-white" : "text-accent"}`}>
                      {part.text}
                    </a>
                  ) : (
                    <Fragment key={i}>{part.text}</Fragment>
                  ),
                )}
              </p>
              {m.pinned && <Pin className={`absolute -top-1.5 h-3.5 w-3.5 rotate-45 ${mine ? "-left-1.5 text-accent" : "-right-1.5 text-accent"}`} aria-label="Pinned" />}
            </div>
          )}

          {live && (
            <div className={`flex items-center gap-0.5 transition-opacity ${menu !== "none" ? "opacity-100" : "opacity-0 group-hover/msg:opacity-100 group-focus-within/msg:opacity-100"}`}>
              <button className="icon-btn h-7 w-7" onClick={() => setMenu(menu === "react" ? "none" : "react")} aria-label="Add reaction"><SmilePlus className="h-4 w-4" /></button>
              <button className="icon-btn h-7 w-7" onClick={() => p.onReply(m)} aria-label="Reply"><CornerUpLeft className="h-4 w-4" /></button>
              <button className="icon-btn h-7 w-7" onClick={() => setMenu(menu === "more" ? "none" : "more")} aria-label="More actions" aria-haspopup="menu"><MoreHorizontal className="h-4 w-4" /></button>
            </div>
          )}

          <AnimatePresence>
            {menu === "react" && (
              <motion.div
                initial={{ opacity: 0, y: 6, scale: 0.9 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 6, scale: 0.9 }}
                className={`absolute bottom-full z-20 mb-2 flex items-center gap-0.5 rounded-full bg-raised p-1 shadow-pop ${mine ? "right-0" : "left-0"}`}
              >
                {QUICK_REACTIONS.map((e) => (
                  <button key={e} onClick={() => { p.onReact(m.id, e); close(); }} className="flex h-9 w-9 items-center justify-center rounded-full text-xl transition hover:scale-125 hover:bg-sunken" aria-label={`React ${e}`}>
                    {e}
                  </button>
                ))}
                <button onClick={() => setMenu("picker")} className="icon-btn h-9 w-9 rounded-full" aria-label="More emoji"><SmilePlus className="h-4 w-4" /></button>
              </motion.div>
            )}
            {menu === "picker" && (
              <motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }} className={`absolute bottom-full z-20 mb-2 rounded-2xl bg-raised shadow-pop ${mine ? "right-0" : "left-0"}`}>
                <EmojiGrid onPick={(e) => { p.onReact(m.id, e); close(); }} />
              </motion.div>
            )}
            {menu === "more" && (
              <motion.div
                role="menu"
                initial={{ opacity: 0, y: -4, scale: 0.97 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: -4, scale: 0.97 }}
                className={`absolute top-full z-20 mt-1.5 w-48 rounded-2xl bg-raised p-1.5 shadow-pop ${mine ? "right-0" : "left-0"}`}
              >
                <MenuItem icon={CornerUpLeft} label="Reply" onClick={() => { p.onReply(m); close(); }} />
                <MenuItem icon={SmilePlus} label="React" onClick={() => setMenu("react")} />
                <MenuItem icon={Copy} label="Copy text" onClick={() => { p.onCopy(m.body); close(); }} />
                <MenuItem icon={m.pinned ? PinOff : Pin} label={m.pinned ? "Unpin" : "Pin"} onClick={() => { p.onPin(m.id); close(); }} />
                {mine && <MenuItem icon={Pencil} label="Edit" onClick={() => { p.onStartEdit(m); close(); }} />}
                {mine && <MenuItem icon={Trash2} label="Delete" danger onClick={() => { p.onDelete(m); close(); }} />}
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {m.reactions.length > 0 && !m.deleted && (
          <div className={`-mt-1 flex flex-wrap gap-1 ${mine ? "mr-2 justify-end" : "ml-2"}`}>
            {m.reactions.map((r) => {
              const reacted = r.user_ids.includes(p.me);
              const names = r.user_ids.map((u) => (u === p.me ? "You" : firstName(p.users[u]?.name ?? u))).join(", ");
              return (
                <motion.button
                  key={r.emoji}
                  layout
                  initial={{ scale: 0.6 }}
                  animate={{ scale: 1 }}
                  onClick={() => p.onReact(m.id, r.emoji)}
                  title={names}
                  aria-label={`${r.emoji} ${r.count}, ${names}`}
                  className={`relative z-[1] flex h-6 items-center gap-1 rounded-full border px-1.5 text-xs shadow-sm transition ${
                    reacted ? "border-accent/50 bg-accent-soft text-accent-ink" : "border-line bg-raised text-muted hover:border-faint"
                  }`}
                >
                  <span className="text-sm leading-none">{r.emoji}</span>
                  {r.count > 1 && <span className="font-semibold tabular-nums">{r.count}</span>}
                </motion.button>
              );
            })}
          </div>
        )}

        {m.local === "failed" && (
          <div className="mt-1 flex items-center gap-2 text-xs text-danger">
            <AlertCircle className="h-3.5 w-3.5" /> Not delivered
            <button className="font-semibold underline" onClick={() => p.onRetry(m)}>Retry</button>
            <button className="text-faint hover:text-ink" onClick={() => p.onDiscard(m)}>Discard</button>
          </div>
        )}
        {p.footer}
      </div>
    </motion.div>
  );
}

/* ---------------------------------------------------------------- typing */
function TypingBubble({ users, ids, isGroup }: { users: Record<string, User>; ids: string[]; isGroup: boolean }) {
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 4 }} className="mt-3 flex items-end gap-2">
      {isGroup && <Avatar user={users[ids[0]]} size="sm" />}
      <div className="rounded-[20px] rounded-bl-[6px] bg-bubble px-4 py-3" aria-label={`${ids.map((i) => firstName(users[i]?.name ?? "")).join(", ")} typing`}>
        <div className="flex gap-1">
          {[0, 1, 2].map((i) => (
            <span key={i} className="h-2 w-2 animate-blink rounded-full bg-muted" style={{ animationDelay: `${i * 0.18}s` }} />
          ))}
        </div>
      </div>
    </motion.div>
  );
}

/* ------------------------------------------------------------------ view */
export function ChatView(p: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const composer = useRef<ComposerHandle>(null);
  const [replyTo, setReplyTo] = useState<Message | null>(null);
  const [editing, setEditing] = useState<Message | null>(null);
  const [atBottom, setAtBottom] = useState(true);
  const [unseen, setUnseen] = useState(0);
  const messages = useMemo(() => p.history?.messages ?? [], [p.history]);
  const lastCount = useRef(0);
  const isGroup = p.conversation.kind === "group";
  const other = p.conversation.participant_ids.find((u) => u !== p.me);

  useEffect(() => {
    setReplyTo(null);
    setEditing(null);
    setUnseen(0);
    lastCount.current = 0;
  }, [p.conversation.id]);

  const scrollToBottom = useCallback((smooth = true) => {
    const el = scroller.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  }, []);

  // Auto-scroll: on open, on my own messages, and on new messages when already near the bottom.
  useLayoutEffect(() => {
    const prev = lastCount.current;
    lastCount.current = messages.length;
    if (!messages.length) return;
    if (prev === 0) {
      if (!p.highlightId) scrollToBottom(false);
      return;
    }
    if (messages.length > prev) {
      const newest = messages[messages.length - 1];
      if (atBottom || newest.sender_id === p.me) scrollToBottom(true);
      else setUnseen((n) => n + (messages.length - prev));
    }
  }, [messages, atBottom, p.me, scrollToBottom, p.highlightId]);

  useEffect(() => {
    if (p.typingIds.length && atBottom) scrollToBottom(true);
  }, [p.typingIds.length, atBottom, scrollToBottom]);

  useEffect(() => {
    if (!p.highlightId || !messages.length) return;
    document.getElementById(`msg-${p.highlightId}`)?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [p.highlightId, messages.length]);

  // Split the timeline (in server order) into day sections; each section's date pill is sticky only within it.
  const daySections = useMemo(() => {
    const out: { key: string; start: number; items: Message[] }[] = [];
    messages.forEach((m, i) => {
      const cur = out[out.length - 1];
      if (cur && sameDay(parseTs(cur.items[cur.items.length - 1].created_at), parseTs(m.created_at))) cur.items.push(m);
      else out.push({ key: `${m.id}-day`, start: i, items: [m] });
    });
    return out;
  }, [messages]);

  const lastMine = useMemo(() => [...messages].reverse().find((m) => m.sender_id === p.me && !m.deleted && !m.local), [messages, p.me]);

  const subtitle = (() => {
    if (p.typingIds.length) {
      return isGroup ? `${p.typingIds.map((i) => firstName(p.users[i]?.name ?? "")).join(", ")} ${p.typingIds.length > 1 ? "are" : "is"} typing…` : "typing…";
    }
    if (!isGroup) return other && p.online[other] ? "Online" : `Offline · ${other ? p.users[other]?.title ?? "" : ""}`;
    const onlineCount = p.conversation.participant_ids.filter((u) => p.online[u]).length;
    return `${p.conversation.participant_ids.length} members · ${onlineCount} online`;
  })();

  const status = p.history?.status ?? "loading";

  return (
    <section className="flex h-full min-w-0 flex-1 flex-col bg-canvas" aria-label={`Conversation with ${p.conversation.title}`}>
      <header className="glass z-10 flex h-16 flex-none items-center gap-3 border-b border-line px-3 sm:px-5">
        <button className="icon-btn md:hidden" onClick={p.onBack} aria-label="Back to conversations"><ChevronLeft className="h-5 w-5" /></button>
        <button onClick={p.onToggleDetails} className="flex min-w-0 flex-1 items-center gap-3 text-left" aria-label="Show conversation details">
          <ConversationAvatar conversation={p.conversation} me={p.me} users={p.users} online={p.online} />
          <span className="min-w-0">
            <span className="flex items-center gap-2 truncate font-display text-[16px] font-semibold tracking-tight">{p.conversation.title}</span>
            <span className={`block truncate text-xs ${p.typingIds.length ? "font-medium text-accent" : other && p.online[other] && !isGroup ? "text-success" : "text-muted"}`}>{subtitle}</span>
          </span>
        </button>
        <button className={`icon-btn ${p.detailsOpen ? "bg-sunken text-ink" : ""}`} onClick={p.onToggleDetails} aria-label="Toggle details panel" aria-pressed={p.detailsOpen}>
          <PanelRight className="h-[18px] w-[18px]" />
        </button>
      </header>

      <AnimatePresence>
        {p.connection !== "open" && (
          <motion.div initial={{ height: 0 }} animate={{ height: "auto" }} exit={{ height: 0 }} className="overflow-hidden">
            <div className="flex items-center justify-center gap-2 bg-warning/10 px-4 py-1.5 text-xs font-medium text-warning" role="status">
              <WifiOff className="h-3.5 w-3.5" />
              {p.connection === "connecting" ? "Connecting to live updates…" : "Connection lost. Reconnecting; messages will sync automatically."}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <div
        ref={scroller}
        onScroll={(e) => {
          const el = e.currentTarget;
          const near = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
          setAtBottom(near);
          if (near) setUnseen(0);
        }}
        className="scroll-thin relative flex-1 overflow-y-auto px-3 pb-4 sm:px-6"
      >
        <div className="mx-auto max-w-3xl">
          {status === "loading" && messages.length === 0 && (
            <div className="space-y-4 py-8" aria-label="Loading messages">
              {[60, 40, 72, 30, 52].map((w, i) => (
                <div key={i} className={`flex ${i % 2 ? "justify-end" : ""}`}>
                  <div className="h-10 animate-pulse rounded-[20px] bg-bubble" style={{ width: `${w}%` }} />
                </div>
              ))}
            </div>
          )}
          {status === "error" && (
            <div className="mx-auto mt-16 max-w-sm rounded-3xl border border-line bg-surface p-6 text-center">
              <AlertCircle className="mx-auto h-8 w-8 text-danger" />
              <p className="mt-3 font-semibold">Couldn&apos;t load messages</p>
              <p className="mt-1 text-sm text-muted">{p.history?.error}</p>
              <button className="btn-primary mt-4" onClick={p.onReload}><RefreshCw className="h-4 w-4" /> Try again</button>
            </div>
          )}
          {status === "ready" && messages.length === 0 && (
            <div className="flex flex-col items-center py-20 text-center">
              <ConversationAvatar conversation={p.conversation} me={p.me} users={p.users} online={p.online} size="xl" />
              <p className="mt-5 font-display text-xl font-semibold">{p.conversation.title}</p>
              <p className="mt-1 text-sm text-muted">This is the beginning of your conversation.</p>
              <div className="mt-6 flex flex-wrap justify-center gap-2">
                {["Hey! 👋", "How's your week going?", "Got a minute to chat?"].map((s) => (
                  <button key={s} onClick={() => p.onSend(s, null)} className="rounded-full border border-line bg-surface px-3.5 py-1.5 text-sm transition hover:border-accent/40 hover:bg-accent-soft">
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {daySections.map((section) => (
            <section key={section.key} aria-label={dayLabel(section.items[0].created_at)}>
              <div className="sticky top-2 z-[5] my-5 flex justify-center" role="separator">
                <span className="rounded-full border border-line bg-raised/95 px-3 py-1 text-[11.5px] font-semibold text-muted shadow-sm backdrop-blur">{dayLabel(section.items[0].created_at)}</span>
              </div>
              {section.items.map((m) => {
                const i = section.start + section.items.indexOf(m);
                const prev = messages[i - 1];
                const next = messages[i + 1];
                const d = parseTs(m.created_at);
                const joins = (a?: Message, b?: Message) =>
                  !!a && !!b && a.sender_id === b.sender_id && sameDay(parseTs(a.created_at), parseTs(b.created_at)) &&
                  Math.abs(parseTs(b.created_at).getTime() - parseTs(a.created_at).getTime()) < GROUP_WINDOW_MS;
                const first = m === section.items[0] || !joins(prev, m);
                const last = m === section.items[section.items.length - 1] || !joins(m, next) || !sameDay(d, parseTs(next.created_at));
                const mine = m.sender_id === p.me;
                const showStatus = mine && lastMine?.id === m.id;
                const footer = last || m.local || showStatus ? (
                  <div className={`mt-1 flex items-center gap-1 px-1.5 text-[11px] text-faint ${mine ? "justify-end" : ""}`}>
                    <span>{timeOf(m.created_at)}</span>
                    {m.edited_at && !m.deleted && <span>· edited</span>}
                    {mine && !m.deleted && (
                      <span className="flex items-center gap-1">
                        · <StatusIcon m={m} />
                        {showStatus && <span>{statusLabel(m, p.conversation)}</span>}
                      </span>
                    )}
                  </div>
                ) : null;
                return (
                  <Bubble
                    key={m.id}
                    m={m}
                    mine={mine}
                    first={first}
                    last={last}
                    showAvatar={last}
                    showName={isGroup && !mine && first}
                    isGroup={isGroup}
                    me={p.me}
                    users={p.users}
                    highlighted={p.highlightId === m.id}
                    footer={footer}
                    onReact={p.onReact}
                    onPin={p.onPin}
                    onRetry={p.onRetry}
                    onDiscard={p.onDiscard}
                    onCopy={p.onCopy}
                    onDelete={p.onDelete}
                    onReply={(msg) => { setEditing(null); setReplyTo(msg); }}
                    onStartEdit={(msg) => { setReplyTo(null); setEditing(msg); }}
                  />
                );
              })}
            </section>
          ))}
          <AnimatePresence>{p.typingIds.length > 0 && <TypingBubble users={p.users} ids={p.typingIds} isGroup={isGroup} />}</AnimatePresence>
        </div>
      </div>

      <div className="relative">
        <AnimatePresence>
          {!atBottom && messages.length > 0 && (
            <motion.button
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 10 }}
              onClick={() => { scrollToBottom(true); setUnseen(0); }}
              className="glass absolute -top-14 left-1/2 z-10 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-line px-3.5 py-2 text-xs font-semibold shadow-float"
            >
              <ArrowDown className="h-3.5 w-3.5" /> {unseen ? `${unseen} new message${unseen > 1 ? "s" : ""}` : "Jump to latest"}
            </motion.button>
          )}
        </AnimatePresence>
        <Composer
          ref={composer}
          conversationId={p.conversation.id}
          me={p.me}
          users={p.users}
          replyTo={replyTo}
          editing={editing}
          onCancelReply={() => setReplyTo(null)}
          onCancelEdit={() => setEditing(null)}
          onSend={(body) => { p.onSend(body, replyTo); setReplyTo(null); }}
          onSaveEdit={(id, body) => { p.onEdit(id, body); setEditing(null); }}
          onEditLast={() => lastMine && setEditing(lastMine)}
          onTyping={p.onTyping}
        />
      </div>
    </section>
  );
}
