"""Structured errors. Every failure has a stable code, the component that raised it,
a human-readable message and machine-readable details."""

from __future__ import annotations

from typing import Any


class MessagingError(Exception):
    status_code = 400
    code = "MESSAGING_ERROR"
    component = "messaging"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "component": self.component, "message": self.message, "details": self.details}


class MissingUserError(MessagingError):
    status_code = 401
    code = "MISSING_DEMO_USER"
    component = "users"


class UserNotFoundError(MessagingError):
    status_code = 404
    code = "USER_NOT_FOUND"
    component = "users"


class ConversationNotFoundError(MessagingError):
    status_code = 404
    code = "CONVERSATION_NOT_FOUND"
    component = "conversations"


class NotAParticipantError(MessagingError):
    status_code = 403
    code = "NOT_A_PARTICIPANT"
    component = "conversations"


class InvalidConversationError(MessagingError):
    status_code = 422
    code = "INVALID_CONVERSATION"
    component = "conversations"


class MessageNotFoundError(MessagingError):
    status_code = 404
    code = "MESSAGE_NOT_FOUND"
    component = "messages"


class InvalidMessageError(MessagingError):
    status_code = 422
    code = "INVALID_MESSAGE"
    component = "messages"


class ForbiddenMessageActionError(MessagingError):
    status_code = 403
    code = "FORBIDDEN_MESSAGE_ACTION"
    component = "messages"


class InvalidStatusTransitionError(MessagingError):
    status_code = 409
    code = "INVALID_STATUS_TRANSITION"
    component = "status"


class MessageServiceUnavailableError(MessagingError):
    status_code = 503
    code = "MESSAGE_SERVICE_UNAVAILABLE"
    component = "messages"


class ConfirmationNotFoundError(MessagingError):
    status_code = 404
    code = "CONFIRMATION_NOT_FOUND"
    component = "confirmations"


class ConfirmationConflictError(MessagingError):
    status_code = 409
    code = "CONFIRMATION_CONFLICT"
    component = "confirmations"
