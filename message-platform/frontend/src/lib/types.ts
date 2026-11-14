export type User = {
  id: string;
  name: string;
  handle: string;
  title: string;
  bio: string;
  location: string;
  avatar_hue: number;
};

export type MessageStatus = "sent" | "delivered" | "read";
/** Client-only states for optimistic messages that the server has not confirmed yet. */
export type LocalStatus = "sending" | "failed";

export type Receipt = { user_id: string; status: MessageStatus; delivered_at: string | null; read_at: string | null };
export type Reaction = { emoji: string; count: number; user_ids: string[] };
export type ReplyPreview = { id: string; sender_id: string; body: string; deleted: boolean };

export type Message = {
  id: string;
  seq: number;
  conversation_id: string;
  sender_id: string;
  body: string;
  idempotency_key: string | null;
  reply_to: ReplyPreview | null;
  created_at: string;
  edited_at: string | null;
  deleted: boolean;
  deleted_at: string | null;
  pinned: boolean;
  pinned_at: string | null;
  pinned_by: string | null;
  reactions: Reaction[];
  receipts: Receipt[];
  status: MessageStatus;
  local?: LocalStatus;
};

export type LastMessage = { id: string; sender_id: string; body: string; deleted: boolean; created_at: string };

export type Conversation = {
  id: string;
  kind: "direct" | "group";
  title: string;
  topic: string;
  emoji: string;
  created_by: string;
  created_at: string;
  participant_ids: string[];
  participants: User[];
  muted: boolean;
  unread_count: number;
  last_message: LastMessage | null;
  updated_at: string;
};

export type ApiError = { code: string; component: string; message: string; details?: Record<string, unknown> };

export type ReceiptChange = {
  message_id: string;
  conversation_id: string;
  sender_id: string;
  user_id: string;
  status: MessageStatus;
  at: string;
};

export type ServerEvent =
  | { type: "hello"; user_id: string; online_user_ids: string[] }
  | { type: "presence"; user_id: string; online: boolean }
  | { type: "message.created"; message: Message }
  | { type: "message.upserted"; message: Message }
  | { type: "receipts.updated"; changes: ReceiptChange[] }
  | { type: "typing"; conversation_id: string; user_id: string; is_typing: boolean }
  | { type: "conversation.upserted"; conversation: Conversation }
  | { type: "conversation.read"; conversation_id: string }
  | { type: "pong"; ts?: number };

export type ConnectionState = "connecting" | "open" | "reconnecting";

export type ProbeCheck = {
  service: string;
  check: string;
  component: string;
  status: "pass" | "fail";
  timestamp: string;
  failure_reason: string | null;
  details: Record<string, unknown>;
  duration_ms: number;
};

export type ProbeReport = {
  service: string;
  probe: string;
  status: "healthy" | "unhealthy";
  timestamp: string;
  summary: string;
  failed_checks: string[];
  checks: ProbeCheck[];
};
