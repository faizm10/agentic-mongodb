"""Typed request and response models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MessageStatus = Literal["sent", "delivered", "read"]


# --------------------------------------------------------------------- requests
class CreateConversationIn(BaseModel):
    kind: Literal["direct", "group"]
    participant_ids: list[str] = Field(min_length=1, max_length=50)
    title: str | None = Field(default=None, max_length=60)
    topic: str = Field(default="", max_length=120)
    emoji: str = Field(default="", max_length=8)


class SendMessageIn(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=64)
    body: str = Field(max_length=8000)
    idempotency_key: str | None = Field(default=None, max_length=200)
    reply_to_id: str | None = Field(default=None, max_length=64)


class EditMessageIn(BaseModel):
    body: str = Field(max_length=8000)


class ReactionIn(BaseModel):
    emoji: str = Field(min_length=1, max_length=32)


class ConversationSettingsIn(BaseModel):
    muted: bool


# -------------------------------------------------------------------- responses
class UserOut(BaseModel):
    id: str
    name: str
    handle: str
    title: str
    bio: str
    location: str
    avatar_hue: int


class ReactionOut(BaseModel):
    emoji: str
    count: int
    user_ids: list[str]


class ReceiptOut(BaseModel):
    user_id: str
    status: MessageStatus
    delivered_at: str | None
    read_at: str | None


class ReplyPreviewOut(BaseModel):
    id: str
    sender_id: str
    body: str
    deleted: bool


class MessageOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    seq: int
    conversation_id: str
    sender_id: str
    body: str
    idempotency_key: str | None
    reply_to: ReplyPreviewOut | None
    created_at: str
    edited_at: str | None
    deleted: bool
    deleted_at: str | None
    pinned: bool
    pinned_at: str | None
    pinned_by: str | None
    reactions: list[ReactionOut]
    receipts: list[ReceiptOut]
    status: MessageStatus


class LastMessageOut(BaseModel):
    id: str
    sender_id: str
    body: str
    deleted: bool
    created_at: str


class ConversationOut(BaseModel):
    id: str
    kind: Literal["direct", "group"]
    title: str
    topic: str
    emoji: str
    created_by: str
    created_at: str
    participant_ids: list[str]
    participants: list[UserOut]
    muted: bool
    unread_count: int
    last_message: LastMessageOut | None
    updated_at: str


class HistoryOut(BaseModel):
    conversation_id: str
    messages: list[MessageOut]
    pinned: list[MessageOut]
    has_more: bool


class ReadOut(BaseModel):
    conversation_id: str
    marked_read: int
    unread_count: int


class SearchOut(BaseModel):
    query: str
    conversations: list[ConversationOut]
    messages: list[MessageOut]
