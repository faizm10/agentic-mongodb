"""Outgoing notifications: incident and repair-agent events posted to a workspace's connectors.

The scheduled check derives events from stored state instead of hooking every place an incident
changes, so Better Stack, Dependabot, site checks and the agent are all covered the same way:
an incident opened or resolved, the agent opening a draft PR, or the agent handing it to a human.
Each (connector, incident, event) is claimed once before sending, so a cron run that overlaps or
repeats never double-posts; a failed send releases the claim so the next run retries it. Events
from before a connector was saved are skipped, so connecting doesn't replay old incidents.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime
from typing import Callable
from urllib.parse import urlsplit

import httpx

from . import netguard
from .models import Incident
from .storage.base import Storage, iso

class NotifyError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _text(event: str, ev: dict) -> str:
    head = {"opened": f"{'🔴' if ev['severity'] == 'down' else '🟠'} Incident opened",
            "resolved": "✅ Incident resolved", "pr_opened": "🛠️ Draft fix ready for review",
            "needs_review": "👀 The repair agent needs a human", "test": "👋 Test from OpsRelay"}[event]
    lines = [f"{head}: {ev['service']}: {ev['title']}", ev["url"]]
    if ev.get("pr_url"):
        lines.append(f"Draft PR: {ev['pr_url']}")
    if ev.get("reason"):
        lines.append(ev["reason"])
    return "\n".join(lines)


def _slack(conf: dict, event: str, ev: dict) -> dict:
    return {"url": conf["url"], "json": {"text": _text(event, ev)}}


def _discord(conf: dict, event: str, ev: dict) -> dict:
    return {"url": conf["url"], "json": {"content": _text(event, ev)[:2000]}}


def _webhook(conf: dict, event: str, ev: dict) -> dict:
    """JSON event signed like GitHub's webhooks: X-OpsRelay-Signature: sha256=HMAC(secret, body)."""
    body = json.dumps({"event": event, **{k: ev.get(k) for k in ("incident_id", "service", "title", "severity", "state",
                                                                    "url", "pr_url", "reason")}}).encode()
    signature = hmac.new(conf["secret"].encode(), body, hashlib.sha256).hexdigest()
    return {"url": conf["url"], "content": body,
            "headers": {"Content-Type": "application/json", "X-OpsRelay-Event": event, "X-OpsRelay-Signature": f"sha256={signature}"}}


def _pagerduty(conf: dict, event: str, ev: dict) -> dict | None:
    """Events API v2: trigger on open, resolve on close, keyed by incident. The test resolves an unused
    key, which checks the routing key without paging anyone."""
    if event not in ("opened", "resolved", "test"):
        return None
    dedup = f"opsrelay-{ev.get('incident_id') or 'connection-test'}"
    body: dict = {"routing_key": conf["routing_key"], "dedup_key": dedup,
                  "event_action": "trigger" if event == "opened" else "resolve"}
    if event == "opened":
        body["payload"] = {"summary": f"{ev['service']}: {ev['title']}"[:1024], "source": "OpsRelay",
                           "severity": "critical" if ev["severity"] == "down" else "warning"}
        body["links"] = [{"href": ev["url"], "text": "OpsRelay incident"}]
    return {"url": "https://events.pagerduty.com/v2/enqueue", "json": body}


def _webhook_config(body: dict) -> dict:
    url = str(body.get("url") or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise NotifyError(422, "Enter the http(s) URL that should receive the events.")
    if reason := netguard.check_url(url):
        raise NotifyError(422, f"That URL isn't allowed: {reason}")
    return {"url": url, "secret": secrets.token_urlsafe(32)}


def _pagerduty_config(body: dict) -> dict:
    key = str(body.get("routing_key") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9]{32}", key):
        raise NotifyError(422, "Paste the 32-character integration (routing) key of a PagerDuty Events API v2 integration.")
    return {"routing_key": key}


def _url_on(prefixes: tuple[str, ...], label: str) -> Callable[[dict], dict]:
    def check(body: dict) -> dict:
        url = str(body.get("url") or "").strip()
        if not url.startswith(prefixes):
            raise NotifyError(422, f"Paste a {label} webhook URL (it starts with {prefixes[0]}).")
        return {"url": url}
    return check


# kind -> (label, validate the submitted config, build the HTTP request for an event or None to skip it)
KINDS: dict[str, tuple[str, Callable[[dict], dict], Callable[[dict, str, dict], dict | None]]] = {
    "slack": ("Slack", _url_on(("https://hooks.slack.com/services/",), "Slack incoming"), _slack),
    "discord": ("Discord", _url_on(("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/"),
                                   "Discord"), _discord),
    "webhook": ("Webhook", _webhook_config, _webhook),
    "pagerduty": ("PagerDuty", _pagerduty_config, _pagerduty),
}


def _kind(kind: str) -> tuple:
    if kind not in KINDS:
        raise NotifyError(404, f"Unknown connector '{kind}'.")
    return KINDS[kind]


def _public(conf: dict) -> dict:
    target = conf.get("url") or conf.get("routing_key") or ""
    return {"kind": conf["kind"], "label": KINDS[conf["kind"]][0], "configured": True,
            "target": f"…{target[-6:]}" if target else None, "created_at": conf["created_at"],
            "last_delivery": conf.get("last_delivery")}


def settings(storage: Storage, ws: str) -> list[dict]:
    saved = {c["kind"]: c for c in storage.find_docs("notify", "workspace_id", ws)}
    return [_public(saved[k]) if k in saved else {"kind": k, "label": label, "configured": False}
            for k, (label, _, _) in KINDS.items()]


def save(storage: Storage, ws: str, kind: str, body: dict, now: datetime) -> dict:
    _, validate, _ = _kind(kind)
    conf = {"workspace_id": ws, "kind": kind, **validate(body), "created_at": iso(now)}
    storage.put_doc("notify", f"{ws}:{kind}", conf)
    # The signing secret is shown once, here; the server keeps it to sign each delivery.
    return {**_public(conf), **({"secret": conf["secret"]} if "secret" in conf else {})}


def remove(storage: Storage, ws: str, kind: str) -> None:
    _kind(kind)
    storage.delete_doc("notify", f"{ws}:{kind}")


async def _send(conf: dict, event: str, ev: dict, transport=None) -> str | None:
    """Post one event; returns an error message, or None when delivered (or skipped by this kind)."""
    request = KINDS[conf["kind"]][2](conf, event, ev)
    if request is None:
        return None
    try:
        async with httpx.AsyncClient(timeout=10, transport=transport, event_hooks={"request": [netguard.guard_request]}) as client:
            res = await client.post(request["url"], json=request.get("json"), content=request.get("content"),
                                    headers=request.get("headers"))
    except httpx.RequestError as exc:
        return f"Couldn't reach {KINDS[conf['kind']][0]}: {exc.__class__.__name__}"
    return None if res.status_code < 300 else f"{KINDS[conf['kind']][0]} answered HTTP {res.status_code}"


async def test(storage: Storage, ws: str, kind: str, base_url: str, now: datetime, transport=None) -> dict:
    _kind(kind)
    conf = storage.get_doc("notify", f"{ws}:{kind}")
    if conf is None:
        raise NotifyError(404, "Save this connector first.")
    ev = {"service": "OpsRelay", "title": "notifications are connected", "severity": "down", "url": base_url}
    error = await _send(conf, "test", ev, transport)
    storage.put_doc("notify", f"{ws}:{kind}", {**conf, "last_delivery": {"at": iso(now), "event": "test", "error": error}})
    if error:
        raise NotifyError(502, error)
    return {"delivered": True}


def _events(incident: Incident, run: dict | None, since: datetime) -> list[tuple[str, dict]]:
    found = []
    if incident.started_at >= since:
        found.append(("opened", {}))
    if incident.state == "resolved" and incident.resolved_at and incident.resolved_at >= since:
        found.append(("resolved", {}))
    agent = (run or {}).get("incident") or {}
    if incident.started_at >= since and (agent.get("pr") or {}).get("url"):
        found.append(("pr_opened", {"pr_url": agent["pr"]["url"]}))
    if incident.started_at >= since and agent.get("status") == "needs_review":
        found.append(("needs_review", {"reason": (agent.get("status_reason") or "")[:300]}))
    return found


async def dispatch(storage: Storage, ws: str, incidents: list[Incident], service_name: Callable[[str], str],
                   base_url: str, now: datetime, transport=None) -> dict[str, int]:
    """Send every new event for ``incidents`` to each of the workspace's connectors."""
    counts = {"sent": 0, "failed": 0}
    for conf in storage.find_docs("notify", "workspace_id", ws):
        since = datetime.fromisoformat(conf["created_at"])
        last = conf.get("last_delivery")
        for incident in incidents:
            run = storage.get_agent_run(incident.id)
            for event, extra in _events(incident, run, since):
                key = f"{ws}:{conf['kind']}:{incident.id}:{event}"
                if not storage.insert_doc("notify_sent", key, {"at": iso(now)}):
                    continue
                ev = {"service": service_name(incident.service_id), "title": incident.title, "severity": incident.severity,
                      "state": incident.state, "incident_id": incident.id,
                      "url": f"{base_url.rstrip('/')}/incidents/{incident.id}", **extra}
                error = await _send(conf, event, ev, transport)
                if error:
                    storage.delete_doc("notify_sent", key)  # retried on the next run
                counts["failed" if error else "sent"] += 1
                last = {"at": iso(now), "event": event, "incident_id": incident.id, "error": error}
        if last is not conf.get("last_delivery"):
            storage.put_doc("notify", f"{ws}:{conf['kind']}", {**conf, "last_delivery": last})
    return counts


def workspaces(storage: Storage) -> set[str]:
    return {c["workspace_id"] for c in storage.all_docs("notify")}
