"""Accounts: email/password users, cookie sessions and owned workspaces.

The services configured in settings belong to the "demo" workspace. The first account to sign
up claims it, so an existing install keeps its data; every later account starts empty.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from .storage.base import Storage, iso

SESSION_DAYS = 14
DEMO_WORKSPACE = "demo"


class AccountError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1).hex()


def _token_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def sign_up(storage: Storage, email: str, password: str, now: datetime) -> dict:
    email = email.strip().lower()
    user = {"id": "usr_" + secrets.token_hex(8), "email": email, "created_at": iso(now)}
    if not storage.insert_doc("emails", email, {"user_id": user["id"]}):
        raise AccountError(409, "An account with this email already exists. Sign in instead.")
    salt = secrets.token_bytes(16)
    storage.put_doc("users", user["id"], {**user, "salt": salt.hex(), "password": _hash_password(password, salt)})
    storage.insert_doc("workspaces", DEMO_WORKSPACE, {"id": DEMO_WORKSPACE, "name": "NovaMart & NovaConnect demo",
                                                      "owner_id": user["id"], "created_at": iso(now)})
    return user


def sign_in(storage: Storage, email: str, password: str) -> dict:
    ref = storage.get_doc("emails", email.strip().lower())
    user = storage.get_doc("users", ref["user_id"]) if ref else None
    given = _hash_password(password, bytes.fromhex(user["salt"]) if user else b"timing")
    if user is None or not hmac.compare_digest(given, user["password"]):
        raise AccountError(401, "That email and password don't match an account.")
    return {k: user[k] for k in ("id", "email", "created_at")}


def start_session(storage: Storage, user_id: str, now: datetime) -> str:
    token = secrets.token_urlsafe(32)
    storage.put_doc("sessions", _token_key(token), {"user_id": user_id, "expires_at": iso(now + timedelta(days=SESSION_DAYS))})
    return token


def end_session(storage: Storage, token: str) -> None:
    storage.delete_doc("sessions", _token_key(token))


def session_user(storage: Storage, token: str | None, now: datetime) -> dict | None:
    session = storage.get_doc("sessions", _token_key(token)) if token else None
    if session is None or session["expires_at"] <= iso(now):
        return None
    user = storage.get_doc("users", session["user_id"])
    return {k: user[k] for k in ("id", "email", "created_at")} if user else None


def create_workspace(storage: Storage, user_id: str, name: str, now: datetime) -> dict:
    ws = {"id": "ws_" + secrets.token_hex(8), "name": name.strip(), "owner_id": user_id, "created_at": iso(now)}
    storage.put_doc("workspaces", ws["id"], ws)
    return ws


def workspaces(storage: Storage, user_id: str) -> list[dict]:
    return sorted(storage.find_docs("workspaces", "owner_id", user_id), key=lambda w: w["created_at"])


def owned_workspace(storage: Storage, user_id: str, workspace_id: str | None) -> dict | None:
    """The requested workspace if this user owns it; with none requested, their first one."""
    if not workspace_id:
        return next(iter(workspaces(storage, user_id)), None)
    ws = storage.get_doc("workspaces", workspace_id)
    return ws if ws and ws["owner_id"] == user_id else None
