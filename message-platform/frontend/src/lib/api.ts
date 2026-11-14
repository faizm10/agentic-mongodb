import type { ApiError, Conversation, Message, ProbeReport, User } from "./types";

export class ApiRequestError extends Error {
  constructor(
    public status: number,
    public error: ApiError,
  ) {
    super(error.message);
  }
}

/** True for failures worth retrying with the same idempotency key (network/5xx). */
export function isRetryable(e: unknown) {
  return !(e instanceof ApiRequestError) || e.status >= 500;
}

async function request<T>(path: string, userId: string | null, init: RequestInit = {}): Promise<{ data: T; status: number; headers: Headers }> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        ...(userId ? { "X-Demo-User": userId } : {}),
        ...(init.headers ?? {}),
      },
    });
  } catch {
    throw new ApiRequestError(0, { code: "NETWORK_ERROR", component: "frontend", message: "Can't reach NovaConnect. Check your connection." });
  }
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!res.ok) {
    const err = (body as { error?: ApiError } | null)?.error;
    throw new ApiRequestError(
      res.status,
      err ?? { code: `HTTP_${res.status}`, component: "api", message: res.status >= 500 ? "The NovaConnect server is unavailable." : res.statusText },
    );
  }
  return { data: body as T, status: res.status, headers: res.headers };
}

export type History = { conversation_id: string; messages: Message[]; pinned: Message[]; has_more: boolean };

export const api = {
  users: () => request<User[]>("/api/users", null).then((r) => r.data),
  presence: () => request<{ online_user_ids: string[] }>("/api/presence", null).then((r) => r.data),
  conversations: (me: string) => request<Conversation[]>("/api/conversations", me).then((r) => r.data),
  history: (me: string, cid: string) =>
    request<History>(`/api/conversations/${encodeURIComponent(cid)}/messages?limit=200`, me).then((r) => r.data),
  markRead: (me: string, cid: string) =>
    request<{ unread_count: number }>(`/api/conversations/${encodeURIComponent(cid)}/read`, me, { method: "POST" }).then((r) => r.data),
  createConversation: (me: string, body: { kind: "direct" | "group"; participant_ids: string[]; title?: string; emoji?: string }) =>
    request<Conversation>("/api/conversations", me, { method: "POST", body: JSON.stringify(body) }).then((r) => r.data),
  setMuted: (me: string, cid: string, muted: boolean) =>
    request<Conversation>(`/api/conversations/${encodeURIComponent(cid)}/settings`, me, { method: "PATCH", body: JSON.stringify({ muted }) }).then((r) => r.data),
  send: (me: string, body: { conversation_id: string; body: string; idempotency_key: string; reply_to_id?: string | null }) =>
    request<Message>("/api/messages", me, { method: "POST", body: JSON.stringify(body) }),
  edit: (me: string, id: string, body: string) =>
    request<Message>(`/api/messages/${id}`, me, { method: "PATCH", body: JSON.stringify({ body }) }).then((r) => r.data),
  remove: (me: string, id: string) => request<Message>(`/api/messages/${id}`, me, { method: "DELETE" }).then((r) => r.data),
  react: (me: string, id: string, emoji: string) =>
    request<Message>(`/api/messages/${id}/reactions`, me, { method: "POST", body: JSON.stringify({ emoji }) }).then((r) => r.data),
  pin: (me: string, id: string) => request<Message>(`/api/messages/${id}/pin`, me, { method: "POST" }).then((r) => r.data),
  search: (me: string, q: string) =>
    request<{ conversations: Conversation[]; messages: Message[] }>(`/api/search?q=${encodeURIComponent(q)}`, me).then((r) => r.data),
  health: () => request<{ status: string; version: string; uptime_seconds: number }>("/health", null).then((r) => r.data),
  probe: async (): Promise<ProbeReport> => {
    const res = await fetch("/api/monitor/probe", { method: "POST", cache: "no-store" });
    return (await res.json()) as ProbeReport;
  },
};

export function wsUrl(userId: string): string {
  const configured = process.env.NEXT_PUBLIC_WS_URL;
  const base = configured && configured.length > 0
    ? configured.replace(/\/$/, "")
    : `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.hostname}:8200`;
  return `${base}/ws/${encodeURIComponent(userId)}`;
}

export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}
