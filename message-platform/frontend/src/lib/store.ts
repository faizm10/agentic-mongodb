import type { ConnectionState, Conversation, Message, MessageStatus, ReceiptChange, User } from "./types";

export type History = {
  status: "loading" | "ready" | "error";
  messages: Message[];
  pinned: Message[];
  error?: string;
};

export type State = {
  users: Record<string, User>;
  conversations: Conversation[];
  conversationsLoaded: boolean;
  histories: Record<string, History>;
  activeId: string | null;
  online: Record<string, true>;
  typing: Record<string, Record<string, number>>; // conversation -> user -> expires at (ms)
  connection: ConnectionState;
};

export const initialState: State = {
  users: {},
  conversations: [],
  conversationsLoaded: false,
  histories: {},
  activeId: null,
  online: {},
  typing: {},
  connection: "connecting",
};

export type Action =
  | { type: "users"; users: User[] }
  | { type: "conversations"; conversations: Conversation[] }
  | { type: "conversation"; conversation: Conversation }
  | { type: "activate"; id: string | null }
  | { type: "history.loading"; id: string }
  | { type: "history.loaded"; id: string; messages: Message[]; pinned: Message[] }
  | { type: "history.error"; id: string; error: string }
  | { type: "message"; message: Message; me: string; isNew: boolean }
  | { type: "message.remove"; conversationId: string; id: string }
  | { type: "receipts"; changes: ReceiptChange[] }
  | { type: "read"; id: string }
  | { type: "presence"; userId: string; online: boolean }
  | { type: "presence.all"; ids: string[] }
  | { type: "typing"; conversationId: string; userId: string; isTyping: boolean }
  | { type: "typing.expire"; now: number }
  | { type: "connection"; state: ConnectionState }
  | { type: "reset" };

const ORDER: MessageStatus[] = ["sent", "delivered", "read"];

function aggregate(message: Message): MessageStatus {
  if (!message.receipts.length) return "sent";
  return message.receipts.reduce<MessageStatus>(
    (lowest, r) => (ORDER.indexOf(r.status) < ORDER.indexOf(lowest) ? r.status : lowest),
    "read",
  );
}

/** Replace by id, or replace the optimistic copy with the same idempotency key, otherwise append.
 *  The client never re-sorts: it renders history in exactly the order the server returned. */
function upsert(list: Message[], message: Message): { list: Message[]; existed: boolean } {
  const byId = list.findIndex((m) => m.id === message.id);
  if (byId >= 0) {
    const next = list.slice();
    next[byId] = message;
    return { list: next, existed: true };
  }
  if (message.idempotency_key) {
    const optimistic = list.findIndex((m) => m.local && m.idempotency_key === message.idempotency_key);
    if (optimistic >= 0) {
      const next = list.slice();
      next[optimistic] = message;
      return { list: next, existed: true };
    }
  }
  return { list: [...list, message], existed: false };
}

function sortConversations(list: Conversation[]) {
  return list.slice().sort((a, b) => (a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0));
}

export function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "users":
      return { ...state, users: Object.fromEntries(action.users.map((u) => [u.id, u])) };

    case "conversations":
      return { ...state, conversations: sortConversations(action.conversations), conversationsLoaded: true };

    case "conversation": {
      const others = state.conversations.filter((c) => c.id !== action.conversation.id);
      return { ...state, conversations: sortConversations([...others, action.conversation]) };
    }

    case "activate":
      return { ...state, activeId: action.id };

    case "history.loading": {
      const prev = state.histories[action.id];
      return {
        ...state,
        histories: { ...state.histories, [action.id]: { messages: prev?.messages ?? [], pinned: prev?.pinned ?? [], status: prev ? prev.status : "loading" } },
      };
    }

    case "history.loaded": {
      // Keep optimistic messages that the server does not know about yet.
      const pending = (state.histories[action.id]?.messages ?? []).filter(
        (m) => m.local && !action.messages.some((s) => s.idempotency_key && s.idempotency_key === m.idempotency_key),
      );
      return {
        ...state,
        histories: { ...state.histories, [action.id]: { status: "ready", messages: [...action.messages, ...pending], pinned: action.pinned } },
      };
    }

    case "history.error": {
      const prev = state.histories[action.id];
      return {
        ...state,
        histories: { ...state.histories, [action.id]: { messages: prev?.messages ?? [], pinned: prev?.pinned ?? [], status: prev?.messages.length ? "ready" : "error", error: action.error } },
      };
    }

    case "message": {
      const { message, me } = action;
      const cid = message.conversation_id;
      const history = state.histories[cid];
      let existed = false;
      let histories = state.histories;
      if (history) {
        const result = upsert(history.messages, message);
        existed = result.existed;
        const pinned = message.pinned && !message.deleted
          ? [message, ...history.pinned.filter((p) => p.id !== message.id)]
          : history.pinned.filter((p) => p.id !== message.id);
        histories = { ...state.histories, [cid]: { ...history, messages: result.list, pinned } };
      }
      const conversations = state.conversations.map((c) => {
        if (c.id !== cid || message.local) return c;
        const isLast = !c.last_message || c.last_message.id === message.id || (action.isNew && !existed);
        if (!isLast) return c;
        const fresh = action.isNew && !existed && c.last_message?.id !== message.id;
        return {
          ...c,
          last_message: { id: message.id, sender_id: message.sender_id, body: message.body, deleted: message.deleted, created_at: message.created_at },
          updated_at: fresh || !c.last_message ? message.created_at : c.updated_at,
          unread_count: fresh && message.sender_id !== me && state.activeId !== cid ? c.unread_count + 1 : c.unread_count,
        };
      });
      // A message from someone ends their typing indicator.
      const typing = state.typing[cid]?.[message.sender_id]
        ? { ...state.typing, [cid]: Object.fromEntries(Object.entries(state.typing[cid]).filter(([u]) => u !== message.sender_id)) }
        : state.typing;
      return { ...state, histories, conversations: sortConversations(conversations), typing };
    }

    case "message.remove": {
      const history = state.histories[action.conversationId];
      if (!history) return state;
      return {
        ...state,
        histories: { ...state.histories, [action.conversationId]: { ...history, messages: history.messages.filter((m) => m.id !== action.id) } },
      };
    }

    case "receipts": {
      let histories = state.histories;
      for (const change of action.changes) {
        const history = histories[change.conversation_id];
        if (!history) continue;
        const messages = history.messages.map((m) => {
          if (m.id !== change.message_id) return m;
          const receipts = m.receipts.map((r) =>
            r.user_id === change.user_id && ORDER.indexOf(change.status) > ORDER.indexOf(r.status)
              ? {
                  ...r,
                  status: change.status,
                  delivered_at: r.delivered_at ?? change.at,
                  read_at: change.status === "read" ? change.at : r.read_at,
                }
              : r,
          );
          const next = { ...m, receipts };
          return { ...next, status: aggregate(next) };
        });
        histories = { ...histories, [change.conversation_id]: { ...history, messages } };
      }
      return { ...state, histories };
    }

    case "read":
      return { ...state, conversations: state.conversations.map((c) => (c.id === action.id ? { ...c, unread_count: 0 } : c)) };

    case "presence": {
      const online = { ...state.online };
      if (action.online) online[action.userId] = true;
      else delete online[action.userId];
      return { ...state, online };
    }

    case "presence.all":
      return { ...state, online: Object.fromEntries(action.ids.map((id) => [id, true as const])) };

    case "typing": {
      const current = { ...(state.typing[action.conversationId] ?? {}) };
      if (action.isTyping) current[action.userId] = Date.now() + 6000;
      else delete current[action.userId];
      return { ...state, typing: { ...state.typing, [action.conversationId]: current } };
    }

    case "typing.expire": {
      let changed = false;
      const typing: State["typing"] = {};
      for (const [cid, people] of Object.entries(state.typing)) {
        const alive = Object.fromEntries(Object.entries(people).filter(([, exp]) => exp > action.now));
        if (Object.keys(alive).length !== Object.keys(people).length) changed = true;
        typing[cid] = alive;
      }
      return changed ? { ...state, typing } : state;
    }

    case "connection":
      return state.connection === action.state ? state : { ...state, connection: action.state };

    case "reset":
      return { ...initialState, users: state.users };
  }
}
