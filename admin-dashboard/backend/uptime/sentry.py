"""Sentry issue webhooks -> one incident per mapped repository listing its unresolved Sentry issues.

A workspace creates a webhook URL here, adds it to a Sentry internal integration with "issue" webhooks,
and pastes back that integration's client secret; Sentry signs every delivery with it
(Sentry-Hook-Signature: HMAC-SHA256 of the body). Each mapped service names its Sentry project slug.
An issue created or unresolved joins the repository's open set, resolved or ignored leaves it, and
the set drives the rollup incident, so a redelivered webhook changes nothing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime

from . import rollup
from .storage.base import Storage, iso

SIGNATURE_HEADER = "sentry-hook-signature"
OPENS, CLOSES = {"created", "unresolved"}, {"resolved", "ignored", "archived"}


class SentryError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def settings(storage: Storage, ws: str) -> dict:
    conf = storage.get_doc("sentry", ws)
    if conf is None:
        return {"configured": False}
    return {"configured": True, "webhook_path": f"/api/v1/webhooks/sentry/{conf['hook_id']}",
            "has_secret": bool(conf.get("client_secret")), "last_delivery": conf.get("last_delivery")}


def create(storage: Storage, ws: str, now: datetime) -> dict:
    """The webhook URL to give Sentry; kept if it already exists."""
    if storage.get_doc("sentry", ws) is None:
        hook_id = secrets.token_hex(12)
        storage.put_doc("sentry_hooks", hook_id, {"workspace_id": ws})
        storage.put_doc("sentry", ws, {"hook_id": hook_id, "created_at": iso(now)})
    return settings(storage, ws)


def set_secret(storage: Storage, ws: str, client_secret: str) -> dict:
    conf = storage.get_doc("sentry", ws)
    if conf is None:
        raise SentryError(409, "Create the webhook URL first.")
    if len(client_secret.strip()) < 32:
        raise SentryError(422, "Paste the internal integration's Client Secret (64 characters).")
    storage.put_doc("sentry", ws, {**conf, "client_secret": client_secret.strip()})
    return settings(storage, ws)


def map_project(storage: Storage, ws: str, service_id: str, project: str | None) -> dict:
    mapping = storage.get_doc("repos", f"{ws}:{service_id}")
    if mapping is None:
        raise SentryError(404, f"Unknown service '{service_id}'.")
    project = (project or "").strip().lower() or None
    if old := mapping.get("sentry_project"):
        storage.delete_doc("sentry_projects", f"{ws}:{old}")
    if project and not storage.insert_doc("sentry_projects", f"{ws}:{project}", {"service_key": mapping["id"]}):
        raise SentryError(409, f"Sentry project '{project}' is already mapped to another service.")
    storage.put_doc("repos", mapping["id"], {**mapping, "sentry_project": project})
    return {"service_id": service_id, "sentry_project": project}


def receive(storage: Storage, hook_id: str, signature: str | None, resource: str | None, body: bytes, now: datetime) -> dict:
    hook = storage.get_doc("sentry_hooks", hook_id)
    conf = storage.get_doc("sentry", hook["workspace_id"]) if hook else None
    secret = (conf or {}).get("client_secret")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest() if secret else None
    if not expected or not signature or not hmac.compare_digest(expected, signature):
        raise SentryError(401, "Unknown webhook URL, no client secret saved yet, or a bad Sentry-Hook-Signature.")
    ws = hook["workspace_id"]
    result = _apply(storage, ws, resource, body, now)
    storage.put_doc("sentry", ws, {**conf, "last_delivery": {"at": iso(now), **result}})
    return result


def _apply(storage: Storage, ws: str, resource: str | None, body: bytes, now: datetime) -> dict:
    if resource == "installation":
        return {"result": "ignored", "reason": "Installation event."}
    try:
        payload = json.loads(body)
    except ValueError:
        raise SentryError(422, "Expected a JSON body.")
    issue = ((payload.get("data") or {}).get("issue") or {}) if isinstance(payload, dict) else {}
    action = payload.get("action") if isinstance(payload, dict) else None
    if resource != "issue" or not issue.get("id") or action not in OPENS | CLOSES:
        return {"result": "ignored", "reason": "Only issue created, resolved, ignored and unresolved events open or close incidents."}
    slug = str((issue.get("project") or {}).get("slug") or "").lower()
    ref = storage.get_doc("sentry_projects", f"{ws}:{slug}")
    if ref is None:
        return {"result": "ignored", "reason": f"Sentry project '{slug}' isn't mapped to a service."}
    mapping = storage.get_doc("repos", ref["service_key"])

    tracked = storage.get_doc("sentry_issues", mapping["id"]) or {"issues": []}
    issues = [i for i in tracked["issues"] if i["id"] != str(issue["id"])]
    if action in OPENS:
        issues.append({"id": str(issue["id"]), "short_id": issue.get("shortId"), "title": (issue.get("title") or "")[:200],
                       "culprit": issue.get("culprit"), "level": issue.get("level") or "error",
                       "url": issue.get("web_url") or issue.get("permalink")})
    storage.put_doc("sentry_issues", mapping["id"], {"issues": issues})
    return rollup.apply(
        storage, mapping, "sentry", issues, now, label="Sentry", noun="unresolved Sentry issue",
        cleared="Every Sentry issue is resolved or ignored.", field="issues",
        severity=lambda items: "down" if any(i["level"] == "fatal" for i in items) else "degraded",
        describe=lambda i: f"{i['short_id'] or i['id']}: {i['title']}")
