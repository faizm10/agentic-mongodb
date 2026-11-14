"use client";

import { AnimatePresence, MotionConfig, motion } from "motion/react";
import { FlaskConical, Keyboard, Loader2, MessagesSquare, ServerCrash } from "lucide-react";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { ApiRequestError, api, isRetryable, newIdempotencyKey } from "@/lib/api";
import { firstName } from "@/lib/format";
import { loadDemoUser, saveDemoUser, useBoolPref, useTheme } from "@/lib/prefs";
import { initialState, reducer } from "@/lib/store";
import type { Message, ServerEvent, User } from "@/lib/types";
import { useRealtime } from "@/lib/useRealtime";
import { ChatView } from "./ChatView";
import { DetailsPanel } from "./DetailsPanel";
import { NewConversationDialog, SettingsDialog } from "./Dialogs";
import { Avatar, ConfirmDialog, LogoMark, ToastProvider, useToast } from "./primitives";
import { Sidebar } from "./Sidebar";

const POLL_MS = 5000;
const sleep = (ms: number) => new Promise((r) => window.setTimeout(r, ms));

/* ------------------------------------------------------------ welcome */
function Welcome({ users, onPick, error }: { users: User[]; onPick: (id: string) => void; error: string | null }) {
  return (
    <div className="relative flex min-h-dvh items-center justify-center overflow-hidden px-4 py-10">
      <div className="pointer-events-none absolute -top-40 left-1/2 h-[520px] w-[820px] -translate-x-1/2 rounded-full bg-accent/20 blur-[120px]" />
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} className="relative w-full max-w-xl rounded-[32px] border border-line bg-surface/80 p-8 shadow-float backdrop-blur-xl sm:p-10">
        <LogoMark size={52} />
        <h1 className="mt-6 font-display text-3xl font-semibold tracking-tight sm:text-4xl">Welcome to NovaConnect</h1>
        <p className="mt-2 text-muted">Conversations that feel close, wherever your team is.</p>
        <div className="mt-6 flex items-start gap-2 rounded-2xl bg-warning/10 px-4 py-3 text-sm text-warning">
          <FlaskConical className="mt-0.5 h-4 w-4 flex-none" />
          <span><strong>Demo mode.</strong> Pick a profile to continue; there is no real sign-in. Open a second window as someone else to watch messages arrive in real time.</span>
        </div>
        {error ? (
          <div className="mt-6 flex items-center gap-3 rounded-2xl border border-danger/30 bg-danger/5 p-4 text-sm">
            <ServerCrash className="h-5 w-5 flex-none text-danger" />
            <div>
              <p className="font-semibold">Can&apos;t reach the NovaConnect API</p>
              <p className="text-muted">Start the backend on port 8200, then refresh. {error}</p>
            </div>
          </div>
        ) : users.length === 0 ? (
          <div className="mt-8 flex justify-center"><Loader2 className="h-6 w-6 animate-spin text-faint" /></div>
        ) : (
          <div className="mt-6 grid gap-2 sm:grid-cols-2">
            {users.map((u, i) => (
              <motion.button
                key={u.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0, transition: { delay: 0.04 * i } }}
                onClick={() => onPick(u.id)}
                className="flex items-center gap-3 rounded-2xl border border-line bg-surface p-3 text-left transition hover:-translate-y-0.5 hover:border-accent/40 hover:shadow-float"
              >
                <Avatar user={u} size="lg" />
                <span className="min-w-0">
                  <span className="block truncate font-semibold">{u.name}</span>
                  <span className="block truncate text-sm text-muted">{u.title}</span>
                </span>
              </motion.button>
            ))}
          </div>
        )}
      </motion.div>
    </div>
  );
}

function EmptyChat({ name }: { name: string }) {
  return (
    <div className="hidden flex-1 flex-col items-center justify-center bg-canvas px-8 text-center md:flex">
      <motion.div initial={{ scale: 0.9, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} className="relative">
        <div className="absolute inset-0 -z-10 rounded-full bg-accent/20 blur-3xl" />
        <div className="flex h-20 w-20 items-center justify-center rounded-[28px] border border-line bg-surface shadow-float">
          <MessagesSquare className="h-9 w-9 text-accent" />
        </div>
      </motion.div>
      <h2 className="mt-6 font-display text-2xl font-semibold tracking-tight">Good to see you, {name}</h2>
      <p className="mt-2 max-w-sm text-muted">Pick a conversation on the left, or start a new one. Your messages sync live across every open window.</p>
      <div className="mt-8 flex items-center gap-2 rounded-2xl border border-line bg-surface px-4 py-3 text-xs text-muted">
        <Keyboard className="h-4 w-4" />
        <span><kbd className="font-semibold text-ink">Enter</kbd> send · <kbd className="font-semibold text-ink">Shift+Enter</kbd> new line · <kbd className="font-semibold text-ink">Esc</kbd> close menus</span>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------- messenger */
function MessengerInner() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [me, setMe] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [bootError, setBootError] = useState<string | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [newOpen, setNewOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<Message | null>(null);
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const [notifications, setNotifications] = useBoolPref("novaconnect.toasts", true);
  const [simulateRetry, setSimulateRetry] = useBoolPref("novaconnect.simulateRetry", false);
  const { theme, setTheme } = useTheme();
  const toast = useToast();

  // Refs give async callbacks the latest values without re-subscribing.
  const live = useRef({ state, me, notifications, simulateRetry });
  live.current = { state, me, notifications, simulateRetry };

  /* ---------- bootstrap */
  useEffect(() => {
    setMe(loadDemoUser());
    api
      .users()
      .then((users) => dispatch({ type: "users", users }))
      .catch((e) => setBootError((e as Error).message))
      .finally(() => setReady(true));
  }, []);

  const users = state.users;
  const meUser = me ? users[me] : undefined;

  const loadConversations = useCallback(async () => {
    const id = live.current.me;
    if (!id) return;
    try {
      dispatch({ type: "conversations", conversations: await api.conversations(id) });
    } catch (e) {
      if (e instanceof ApiRequestError && e.error.code === "USER_NOT_FOUND") setMe(null);
    }
  }, []);

  /** Tell the server this conversation has been read. `force` is used for messages that arrive while the
   *  conversation is open: the local unread count stays 0 for those, but the server still has to record the read. */
  const markRead = useCallback(async (cid: string, force = false) => {
    const id = live.current.me;
    const conv = live.current.state.conversations.find((c) => c.id === cid);
    if (!id || !conv || (!force && conv.unread_count === 0) || document.visibilityState !== "visible") return;
    dispatch({ type: "read", id: cid });
    try {
      await api.markRead(id, cid);
    } catch {
      /* will be retried on next activation */
    }
  }, []);

  const loadHistory = useCallback(async (cid: string) => {
    const id = live.current.me;
    if (!id) return;
    dispatch({ type: "history.loading", id: cid });
    try {
      const h = await api.history(id, cid);
      dispatch({ type: "history.loaded", id: cid, messages: h.messages, pinned: h.pinned });
      if (live.current.state.activeId === cid) markRead(cid);
    } catch (e) {
      dispatch({ type: "history.error", id: cid, error: (e as Error).message });
    }
  }, [markRead]);

  useEffect(() => {
    if (!me || !users[me]) return;
    loadConversations();
    api.presence().then((p) => dispatch({ type: "presence.all", ids: p.online_user_ids })).catch(() => {});
  }, [me, users, loadConversations]);

  /* ---------- realtime */
  const onEvent = useCallback((event: ServerEvent) => {
    const { me: id, state: s, notifications: notify } = live.current;
    if (!id) return;
    switch (event.type) {
      case "hello":
        dispatch({ type: "presence.all", ids: event.online_user_ids });
        break;
      case "presence":
        dispatch({ type: "presence", userId: event.user_id, online: event.online });
        break;
      case "message.created": {
        const m = event.message;
        const conv = s.conversations.find((c) => c.id === m.conversation_id);
        if (!conv) loadConversations();
        dispatch({ type: "message", message: m, me: id, isNew: true });
        if (m.sender_id !== id) {
          const visible = document.visibilityState === "visible";
          if (s.activeId === m.conversation_id && visible) {
            window.setTimeout(() => markRead(m.conversation_id, true), 50);
          } else if (notify && !conv?.muted) {
            toast.push({
              title: conv?.kind === "group" ? `${firstName(s.users[m.sender_id]?.name ?? "")} in ${conv.title}` : s.users[m.sender_id]?.name ?? "New message",
              body: m.body,
              user: s.users[m.sender_id],
              onClick: () => selectRef.current(m.conversation_id),
            });
          }
        }
        break;
      }
      case "message.upserted":
        dispatch({ type: "message", message: event.message, me: id, isNew: false });
        break;
      case "receipts.updated":
        dispatch({ type: "receipts", changes: event.changes });
        break;
      case "typing":
        dispatch({ type: "typing", conversationId: event.conversation_id, userId: event.user_id, isTyping: event.is_typing });
        break;
      case "conversation.upserted":
        dispatch({ type: "conversation", conversation: event.conversation });
        break;
      case "conversation.read":
        dispatch({ type: "read", id: event.conversation_id });
        break;
    }
  }, [loadConversations, markRead, toast]);

  const resync = useCallback(() => {
    loadConversations();
    const active = live.current.state.activeId;
    if (active) loadHistory(active);
  }, [loadConversations, loadHistory]);

  const wsSend = useRealtime(meUser ? me : null, onEvent, (c) => dispatch({ type: "connection", state: c }), resync);

  // Fallback: while the socket is down, poll over REST so nothing is missed.
  useEffect(() => {
    if (!meUser || state.connection === "open") return;
    const t = window.setInterval(resync, POLL_MS);
    return () => window.clearInterval(t);
  }, [meUser, state.connection, resync]);

  useEffect(() => {
    const t = window.setInterval(() => dispatch({ type: "typing.expire", now: Date.now() }), 1500);
    return () => window.clearInterval(t);
  }, []);

  useEffect(() => {
    const onVisible = () => {
      const active = live.current.state.activeId;
      if (document.visibilityState === "visible" && active) markRead(active);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [markRead]);

  /* ---------- navigation */
  const select = useCallback((cid: string, messageId?: string) => {
    dispatch({ type: "activate", id: cid });
    setHighlightId(messageId ?? null);
    if (messageId) window.setTimeout(() => setHighlightId(null), 2600);
    loadHistory(cid);
  }, [loadHistory]);
  const selectRef = useRef(select);
  selectRef.current = select;

  // Deep link: ?c=<conversation id> opens that conversation once the list has loaded.
  const deepLinked = useRef(false);
  useEffect(() => {
    if (deepLinked.current || !state.conversationsLoaded) return;
    deepLinked.current = true;
    const target = new URLSearchParams(window.location.search).get("c");
    if (target && state.conversations.some((c) => c.id === target)) select(target);
  }, [state.conversationsLoaded, state.conversations, select]);

  useEffect(() => {
    if (!deepLinked.current) return;
    const url = new URL(window.location.href);
    if (state.activeId) url.searchParams.set("c", state.activeId);
    else url.searchParams.delete("c");
    window.history.replaceState(null, "", url.toString());
  }, [state.activeId]);

  const switchUser = (id: string) => {
    saveDemoUser(id);
    dispatch({ type: "reset" });
    deepLinked.current = true;
    setDetailsOpen(false);
    setMe(id);
    toast.push({ title: `Now chatting as ${users[id]?.name}`, body: "Demo identity switched for this tab.", tone: "success" });
  };

  /* ---------- message actions */
  const deliver = useCallback(async (msg: Message) => {
    const id = live.current.me!;
    const payload = { conversation_id: msg.conversation_id, body: msg.body, idempotency_key: msg.idempotency_key!, reply_to_id: msg.reply_to?.id ?? null };
    dispatch({ type: "message", message: { ...msg, local: "sending" }, me: id, isNew: false });
    for (let attempt = 0; attempt < 4; attempt++) {
      try {
        const res = await api.send(id, payload);
        dispatch({ type: "message", message: res.data, me: id, isNew: true });
        if (live.current.simulateRetry) {
          // Simulated lost response: the client retries with the SAME idempotency key.
          const replay = await api.send(id, payload);
          dispatch({ type: "message", message: replay.data, me: id, isNew: true });
        }
        return;
      } catch (e) {
        if (!isRetryable(e) || attempt === 3) {
          dispatch({ type: "message", message: { ...msg, local: "failed" }, me: id, isNew: false });
          toast.push({ title: "Message not sent", body: e instanceof ApiRequestError ? e.error.message : "Network error", tone: "error" });
          return;
        }
        await sleep(600 * 2 ** attempt);
      }
    }
  }, [toast]);

  const send = (cid: string, body: string, replyTo: Message | null) => {
    const key = newIdempotencyKey();
    const optimistic: Message = {
      id: `local-${key}`, seq: 0, conversation_id: cid, sender_id: me!, body, idempotency_key: key,
      reply_to: replyTo ? { id: replyTo.id, sender_id: replyTo.sender_id, body: replyTo.body, deleted: false } : null,
      created_at: new Date().toISOString(), edited_at: null, deleted: false, deleted_at: null, pinned: false, pinned_at: null,
      pinned_by: null, reactions: [], receipts: [], status: "sent", local: "sending",
    };
    deliver(optimistic);
    wsSend({ type: "typing", conversation_id: cid, is_typing: false });
  };

  const guarded = async (fn: () => Promise<Message>) => {
    try {
      dispatch({ type: "message", message: await fn(), me: me!, isNew: false });
    } catch (e) {
      toast.push({ title: "Something went wrong", body: e instanceof ApiRequestError ? e.error.message : (e as Error).message, tone: "error" });
    }
  };

  const copy = (text: string) => {
    navigator.clipboard?.writeText(text).then(
      () => toast.push({ title: "Copied to clipboard", tone: "success" }),
      () => toast.push({ title: "Copy failed", tone: "error" }),
    );
  };

  /* ---------- render */
  if (!ready) {
    return <div className="flex min-h-dvh items-center justify-center"><Loader2 className="h-6 w-6 animate-spin text-faint" /></div>;
  }
  if (!me || !meUser) {
    return <Welcome users={Object.values(users)} error={bootError} onPick={switchUser} />;
  }

  const active = state.conversations.find((c) => c.id === state.activeId) ?? null;
  const typingIds = active ? Object.keys(state.typing[active.id] ?? {}).filter((u) => u !== me) : [];

  return (
    <div className="flex h-dvh overflow-hidden">
      <div className={`${active ? "hidden md:flex" : "flex"} h-full w-full md:w-auto`}>
        <Sidebar
          me={meUser}
          users={users}
          conversations={state.conversations}
          loaded={state.conversationsLoaded}
          activeId={state.activeId}
          online={state.online}
          typing={state.typing}
          theme={theme}
          onToggleTheme={() => setTheme(document.documentElement.classList.contains("dark") ? "light" : "dark")}
          onSelect={select}
          onNew={() => setNewOpen(true)}
          onSettings={() => setSettingsOpen(true)}
          onSwitchUser={switchUser}
        />
      </div>

      <div className={`${active ? "flex" : "hidden md:flex"} relative min-w-0 flex-1`}>
        <AnimatePresence mode="wait" initial={false}>
          {active ? (
            <motion.div key={active.id} className="flex min-w-0 flex-1" initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.16 }}>
              <ChatView
                conversation={active}
                me={me}
                users={users}
                online={state.online}
                typingIds={typingIds}
                history={state.histories[active.id]}
                detailsOpen={detailsOpen}
                connection={state.connection}
                highlightId={highlightId}
                onSend={(body, replyTo) => send(active.id, body, replyTo)}
                onEdit={(id, body) => guarded(() => api.edit(me, id, body))}
                onDelete={(m) => setConfirmDelete(m)}
                onReact={(id, emoji) => guarded(() => api.react(me, id, emoji))}
                onPin={(id) => guarded(() => api.pin(me, id))}
                onRetry={(m) => deliver(m)}
                onDiscard={(m) => dispatch({ type: "message.remove", conversationId: m.conversation_id, id: m.id })}
                onTyping={(isTyping) => wsSend({ type: "typing", conversation_id: active.id, is_typing: isTyping })}
                onBack={() => dispatch({ type: "activate", id: null })}
                onToggleDetails={() => setDetailsOpen((o) => !o)}
                onReload={() => loadHistory(active.id)}
                onCopy={copy}
              />
            </motion.div>
          ) : (
            <EmptyChat key="empty" name={firstName(meUser.name)} />
          )}
        </AnimatePresence>
        <AnimatePresence>
          {active && detailsOpen && (
            <DetailsPanel
              key="details"
              conversation={active}
              me={me}
              users={users}
              online={state.online}
              history={state.histories[active.id]}
              onClose={() => setDetailsOpen(false)}
              onCopy={copy}
              onJump={(mid) => { setHighlightId(mid); window.setTimeout(() => setHighlightId(null), 2600); }}
              onMute={async (muted) => {
                try {
                  dispatch({ type: "conversation", conversation: await api.setMuted(me, active.id, muted) });
                } catch (e) {
                  toast.push({ title: "Couldn't update setting", body: (e as Error).message, tone: "error" });
                }
              }}
              onMessageUser={async (uid) => {
                const conv = await api.createConversation(me, { kind: "direct", participant_ids: [uid] });
                dispatch({ type: "conversation", conversation: conv });
                select(conv.id);
              }}
            />
          )}
        </AnimatePresence>
      </div>

      <NewConversationDialog
        open={newOpen}
        onClose={() => setNewOpen(false)}
        me={me}
        users={users}
        online={state.online}
        onCreate={async (ids, title, emoji) => {
          const conv = await api.createConversation(me, ids.length === 1 ? { kind: "direct", participant_ids: ids } : { kind: "group", participant_ids: ids, title, emoji });
          dispatch({ type: "conversation", conversation: conv });
          select(conv.id);
        }}
      />
      <SettingsDialog
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        me={me}
        users={users}
        theme={theme}
        setTheme={setTheme}
        onSwitchUser={switchUser}
        notifications={notifications}
        setNotifications={setNotifications}
        simulateRetry={simulateRetry}
        setSimulateRetry={setSimulateRetry}
        connection={state.connection}
      />
      <ConfirmDialog
        open={confirmDelete !== null}
        title="Delete message?"
        body="This removes the message for everyone in the conversation. This can't be undone."
        confirmLabel="Delete"
        onCancel={() => setConfirmDelete(null)}
        onConfirm={() => {
          const m = confirmDelete;
          setConfirmDelete(null);
          if (m) guarded(() => api.remove(me, m.id));
        }}
      />
    </div>
  );
}

export function Messenger() {
  return (
    <MotionConfig reducedMotion="user">
      <ToastProvider>
        <MessengerInner />
      </ToastProvider>
    </MotionConfig>
  );
}
