"""NovaConnect FastAPI application.

Run locally:  uvicorn app.main:app --reload --port 8200

Identity is demo-only: REST calls send ``X-Demo-User: <user id>`` and the
WebSocket connects to ``/ws/<user id>``. There is no real authentication.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Header, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__, conversations, db, delivery, demo_outage, messages, monitor, users
from .errors import MessageServiceUnavailableError, MessagingError
from .realtime import ConnectionManager
from .schemas import (
    ConversationOut,
    ConversationSettingsIn,
    CreateConversationIn,
    EditMessageIn,
    HistoryOut,
    MessageOut,
    ReactionIn,
    ReadOut,
    SearchOut,
    SendMessageIn,
    UserOut,
)
from .seed import seed_if_empty

SERVICE_ID = "messaging"
SERVICE_NAME = "novaconnect-api"
logger = logging.getLogger("novaconnect")


def _log(level: int, event: str, **fields: Any) -> None:
    logger.log(level, json.dumps({"event": event, "service": SERVICE_ID, **fields}, default=str))


def create_app(
    db_path: str | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
    seed: bool = True,
) -> FastAPI:
    logging.basicConfig(level=os.environ.get("NOVACONNECT_LOG_LEVEL", "INFO"), format="%(message)s")
    path = db.resolve_db_path(db_path)
    conn = db.connect(path)
    now: Callable[[], datetime] = clock or db.utcnow
    if seed:
        seed_if_empty(conn, now())
    manager = ConnectionManager()
    started_at = datetime.now(timezone.utc)

    app = FastAPI(
        title="NovaConnect API",
        version=__version__,
        description="Realtime messaging demo for OpsRelay. Identity is demo-only (X-Demo-User header).",
    )
    app.state.conn = conn
    app.state.manager = manager
    app.state.db_path = path

    origins = os.environ.get("NOVACONNECT_CORS_ORIGINS", "http://localhost:3200,http://127.0.0.1:3200")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "Idempotent-Replay"],
    )

    # ------------------------------------------------------------- middleware
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        t0 = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if request.url.path.startswith("/api"):
            _log(logging.INFO, "http_request", method=request.method, path=request.url.path,
                 status=response.status_code, duration_ms=round((time.perf_counter() - t0) * 1000, 2),
                 request_id=request_id)
        return response

    def error_response(request: Request, status_code: int, error: dict[str, Any]) -> JSONResponse:
        rid = getattr(request.state, "request_id", None)
        _log(logging.WARNING, "api_error", status=status_code, path=request.url.path, request_id=rid, error=error)
        return JSONResponse(status_code=status_code, content={"error": error, "request_id": rid})

    @app.exception_handler(MessagingError)
    async def on_messaging_error(request: Request, exc: MessagingError):
        return error_response(request, exc.status_code, exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError):
        fields = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"], "type": e["type"]}
            for e in exc.errors()
        ]
        return error_response(request, 422, {
            "code": "VALIDATION_FAILED", "component": "api",
            "message": "; ".join(f"{f['field']}: {f['message']}" for f in fields) or "Invalid request.",
            "details": {"fields": fields},
        })

    def me(user_id: str | None) -> str:
        return users.get_user(conn, user_id)["id"]

    def require_receiving() -> None:
        if demo_outage.mode(path) in {"receive", "both"}:
            raise MessageServiceUnavailableError("Messages are temporarily unavailable.")

    async def notify_receipts(changes: list[dict[str, Any]]) -> None:
        by_sender: dict[str, list[dict[str, Any]]] = {}
        for c in changes:
            by_sender.setdefault(c["sender_id"], []).append(c)
        for sender_id, items in by_sender.items():
            await manager.send_to_users([sender_id], {"type": "receipts.updated", "changes": items})

    async def push_message(message: dict[str, Any], event: str = "message.upserted") -> set[str]:
        members = conversations.participant_ids(conn, message["conversation_id"])
        if demo_outage.mode(path) in {"receive", "both"}:
            members = [message["sender_id"]]
        return await manager.send_to_users(members, {"type": event, "message": message})

    # ------------------------------------------------------------- monitoring
    @app.get("/health", tags=["monitoring"])
    def health() -> dict[str, Any]:
        """Availability only: the process is up and the database answers."""
        conn.execute("SELECT 1").fetchone()
        return {
            "service": SERVICE_ID,
            "check": "availability",
            "status": "ok",
            "name": SERVICE_NAME,
            "version": __version__,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "uptime_seconds": round((datetime.now(timezone.utc) - started_at).total_seconds(), 1),
            "websocket_connections": manager.connection_count(),
        }

    @app.api_route("/api/monitor/probe", methods=["GET", "POST"], tags=["monitoring"])
    def probe() -> JSONResponse:
        """Functional correctness probe. 200 when healthy, 503 when any business rule fails."""
        report = monitor.run_probe(path)
        if demo_outage.mode(path):
            report["demo_outage"] = True
            report["checks"].append({
                "service": SERVICE_ID, "check": "customer_messaging", "component": "messages",
                "status": "fail", "failure_reason": "A customer-facing messaging flow is unavailable.",
                "details": {}, "timestamp": datetime.now(timezone.utc).isoformat(), "duration_ms": 0.0,
            })
            report["failed_checks"].append("customer_messaging")
            report["summary"] = f"{len(report['checks']) - len(report['failed_checks'])}/{len(report['checks'])} checks passed"
            report["status"] = "unhealthy"
        if report["status"] != "healthy":
            _log(logging.ERROR, "messaging_probe_failed", failed_checks=report["failed_checks"], report=report)
        return JSONResponse(status_code=200 if report["status"] == "healthy" else 503, content=report)

    # ------------------------------------------------------------------ users
    @app.get("/api/users", response_model=list[UserOut], tags=["users"])
    def list_users() -> list[dict[str, Any]]:
        return users.list_users(conn)

    @app.get("/api/presence", tags=["users"])
    def presence() -> dict[str, Any]:
        return {"online_user_ids": sorted(manager.online_user_ids())}

    # ---------------------------------------------------------- conversations
    @app.get("/api/conversations", response_model=list[ConversationOut], tags=["conversations"])
    def list_conversations(x_demo_user: str | None = Header(default=None)) -> list[dict[str, Any]]:
        require_receiving()
        return conversations.list_conversations(conn, me(x_demo_user))

    @app.get("/api/conversations/{conversation_id}", response_model=ConversationOut, tags=["conversations"])
    def get_conversation(conversation_id: str, x_demo_user: str | None = Header(default=None)) -> dict[str, Any]:
        require_receiving()
        return conversations.conversation_summary(conn, conversation_id, me(x_demo_user))

    @app.post("/api/conversations", response_model=ConversationOut, tags=["conversations"])
    async def create_conversation(body: CreateConversationIn, x_demo_user: str | None = Header(default=None)):
        user_id = me(x_demo_user)
        cid, created = conversations.create_conversation(
            conn, creator_id=user_id, kind=body.kind, participant_ids_in=body.participant_ids,
            title=body.title, topic=body.topic, emoji=body.emoji, now=now(),
        )
        if created:
            for member in conversations.participant_ids(conn, cid):
                summary = conversations.conversation_summary(conn, cid, member)
                await manager.send_to_users([member], {"type": "conversation.upserted", "conversation": summary})
        return JSONResponse(
            status_code=201 if created else 200,
            content=conversations.conversation_summary(conn, cid, user_id),
        )

    @app.patch("/api/conversations/{conversation_id}/settings", response_model=ConversationOut, tags=["conversations"])
    def update_settings(conversation_id: str, body: ConversationSettingsIn, x_demo_user: str | None = Header(default=None)):
        user_id = me(x_demo_user)
        conversations.set_muted(conn, conversation_id, user_id, body.muted)
        return conversations.conversation_summary(conn, conversation_id, user_id)

    @app.get("/api/conversations/{conversation_id}/messages", response_model=HistoryOut, tags=["messages"])
    async def history(
        conversation_id: str,
        x_demo_user: str | None = Header(default=None),
        limit: int = Query(default=messages.DEFAULT_PAGE_SIZE, ge=1, le=500),
        before_seq: int | None = Query(default=None, ge=1),
    ):
        require_receiving()
        user_id = me(x_demo_user)
        page = messages.list_history(conn, conversation_id, user_id, limit=limit + 1, before_seq=before_seq)
        has_more = len(page) > limit
        page = page[-limit:] if has_more else page
        # Fetching history proves the client received these messages -> deliver pending receipts.
        changes = delivery.deliver_pending(conn, user_id, now(), conversation_id)
        await notify_receipts(changes)
        if changes:
            page = messages.list_history(conn, conversation_id, user_id, limit=limit, before_seq=before_seq)
        return {
            "conversation_id": conversation_id,
            "messages": page,
            "pinned": messages.list_pinned(conn, conversation_id, user_id),
            "has_more": has_more,
        }

    @app.post("/api/conversations/{conversation_id}/read", response_model=ReadOut, tags=["messages"])
    async def mark_read(conversation_id: str, x_demo_user: str | None = Header(default=None)):
        require_receiving()
        user_id = me(x_demo_user)
        changes = delivery.mark_conversation_read(conn, conversation_id, user_id, now())
        await notify_receipts(changes)
        await manager.send_to_users([user_id], {"type": "conversation.read", "conversation_id": conversation_id})
        return {
            "conversation_id": conversation_id,
            "marked_read": len(changes),
            "unread_count": conversations.unread_count(conn, conversation_id, user_id),
        }

    # --------------------------------------------------------------- messages
    @app.post("/api/messages", response_model=MessageOut, tags=["messages"])
    async def send(body: SendMessageIn, request: Request, x_demo_user: str | None = Header(default=None)):
        if demo_outage.mode(path) in {"send", "both"}:
            raise MessageServiceUnavailableError("Message sending is temporarily unavailable.")
        user_id = me(x_demo_user)
        message, created = messages.send_message(
            conn, conversation_id=body.conversation_id, sender_id=user_id, body=body.body, now=now(),
            idempotency_key=body.idempotency_key, reply_to_id=body.reply_to_id,
        )
        if created:
            reached = await push_message(message, "message.created")
            recipients = [u for u in conversations.participant_ids(conn, message["conversation_id"]) if u != user_id]
            delivered = delivery.record_delivery(conn, message["id"], recipients, reached, now())
            if delivered:
                message = messages.get_message(conn, message["id"])
                await push_message(message)
            _log(logging.INFO, "message_created", message_id=message["id"], conversation_id=message["conversation_id"],
                 delivered_to=delivered, request_id=request.state.request_id)
        return JSONResponse(
            status_code=201 if created else 200,
            content=message,
            headers={"Idempotent-Replay": "false" if created else "true"},
        )

    @app.patch("/api/messages/{message_id}", response_model=MessageOut, tags=["messages"])
    async def edit(message_id: str, body: EditMessageIn, x_demo_user: str | None = Header(default=None)):
        message = messages.edit_message(conn, message_id, me(x_demo_user), body.body, now())
        await push_message(message)
        return message

    @app.delete("/api/messages/{message_id}", response_model=MessageOut, tags=["messages"])
    async def delete(message_id: str, x_demo_user: str | None = Header(default=None)):
        message = messages.delete_message(conn, message_id, me(x_demo_user), now())
        await push_message(message)
        return message

    @app.post("/api/messages/{message_id}/reactions", response_model=MessageOut, tags=["messages"])
    async def react(message_id: str, body: ReactionIn, x_demo_user: str | None = Header(default=None)):
        message = messages.toggle_reaction(conn, message_id, me(x_demo_user), body.emoji, now())
        await push_message(message)
        return message

    @app.post("/api/messages/{message_id}/pin", response_model=MessageOut, tags=["messages"])
    async def pin(message_id: str, x_demo_user: str | None = Header(default=None)):
        message = messages.toggle_pin(conn, message_id, me(x_demo_user), now())
        await push_message(message)
        return message

    @app.get("/api/search", response_model=SearchOut, tags=["messages"])
    def search(q: str = Query(min_length=1, max_length=100), x_demo_user: str | None = Header(default=None)):
        require_receiving()
        user_id = me(x_demo_user)
        needle = q.strip().lower()
        convs = [
            c for c in conversations.list_conversations(conn, user_id)
            if needle in c["title"].lower() or any(needle in p["name"].lower() for p in c["participants"])
        ]
        return {"query": q, "conversations": convs, "messages": messages.search_messages(conn, user_id, q)}

    # -------------------------------------------------------------- websocket
    @app.websocket("/ws/{user_id}")
    async def websocket_endpoint(websocket: WebSocket, user_id: str):
        if demo_outage.mode(path) in {"receive", "both"}:
            await websocket.close(code=1013, reason="Messages are temporarily unavailable")
            return
        try:
            users.get_user(conn, user_id)
        except MessagingError:
            await websocket.close(code=4404, reason="unknown demo user")
            return
        await websocket.accept()
        first = await manager.connect(user_id, websocket)
        await websocket.send_json({"type": "hello", "user_id": user_id, "online_user_ids": sorted(manager.online_user_ids())})
        if first:
            await manager.broadcast({"type": "presence", "user_id": user_id, "online": True}, exclude=user_id)
        await notify_receipts(delivery.deliver_pending(conn, user_id, now()))
        try:
            while True:
                event = await websocket.receive_json()
                kind = event.get("type")
                if kind == "ping":
                    await websocket.send_json({"type": "pong", "ts": event.get("ts")})
                elif kind == "typing":
                    cid = str(event.get("conversation_id", ""))
                    try:
                        conversations.ensure_participant(conn, cid, user_id)
                    except MessagingError:
                        continue
                    others = [u for u in conversations.participant_ids(conn, cid) if u != user_id]
                    await manager.send_to_users(others, {
                        "type": "typing", "conversation_id": cid, "user_id": user_id,
                        "is_typing": bool(event.get("is_typing")),
                    })
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            last = await manager.disconnect(user_id, websocket)
            if last:
                await manager.broadcast({"type": "presence", "user_id": user_id, "online": False}, exclude=user_id)

    return app


def _make_default_app() -> FastAPI:
    return create_app()


app = _make_default_app() if os.environ.get("NOVACONNECT_SKIP_APP") != "1" else FastAPI()
