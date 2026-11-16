"""Vercel production deployments: a failed one opens an incident on the mapped service.

A workspace saves a Vercel token (plus a team id for team projects) and each mapped service names its
Vercel project. The scheduled check reads that project's latest production deployment: ERROR opens
the repository's Vercel rollup incident (degraded: the previous deployment keeps serving), the next
READY one resolves it, and one still building leaves things as they are. The token stays server-side.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from . import rollup
from .storage.base import Storage, iso

VERCEL_API = "https://api.vercel.com"
SETTLED = {"READY", "ERROR"}


class VercelError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


async def _get(token: str, path: str, transport=None, **params: Any) -> Any:
    try:
        async with httpx.AsyncClient(base_url=VERCEL_API, timeout=15, transport=transport) as client:
            res = await client.get(path, params={k: v for k, v in params.items() if v}, headers={"Authorization": f"Bearer {token}"})
    except httpx.RequestError as exc:
        raise VercelError(502, "Cannot reach the Vercel API. Try again in a moment.") from exc
    if res.status_code in (401, 403):
        raise VercelError(422, "Vercel rejected the token, or it can't access this team or project.")
    if res.status_code == 404:
        raise VercelError(404, "Not found on Vercel. Check the project name and team id.")
    if res.status_code >= 400:
        raise VercelError(502, f"Vercel answered HTTP {res.status_code}.")
    return res.json()


def connection(storage: Storage, ws: str) -> dict:
    conn = storage.get_doc("vercel", ws)
    return {"connected": True, "login": conn["login"], "team_id": conn.get("team_id")} if conn else {"connected": False}


async def connect(storage: Storage, ws: str, token: str, team_id: str | None, now: datetime, transport=None) -> dict:
    user = (await _get(token.strip(), "/v2/user", transport, teamId=team_id)).get("user") or {}
    storage.put_doc("vercel", ws, {"token": token.strip(), "team_id": team_id or None,
                                   "login": user.get("username") or user.get("email") or "vercel", "connected_at": iso(now)})
    return connection(storage, ws)


def disconnect(storage: Storage, ws: str) -> None:
    storage.delete_doc("vercel", ws)


def map_project(storage: Storage, ws: str, service_id: str, project: str | None) -> dict:
    mapping = storage.get_doc("repos", f"{ws}:{service_id}")
    if mapping is None:
        raise VercelError(404, f"Unknown service '{service_id}'.")
    storage.put_doc("repos", mapping["id"], {**mapping, "vercel_project": (project or "").strip() or None})
    return {"service_id": service_id, "vercel_project": (project or "").strip() or None}


async def sync(storage: Storage, mapping: dict, now: datetime, transport=None) -> dict:
    conn = storage.get_doc("vercel", mapping["workspace_id"])
    if conn is None:
        result = {"result": "skipped", "reason": "Connect Vercel first."}
    else:
        try:
            data = await _get(conn["token"], "/v6/deployments", transport, projectId=mapping["vercel_project"],
                              target="production", limit=1, teamId=conn.get("team_id"))
            result = _apply(storage, mapping, (data.get("deployments") or [None])[0], now)
        except VercelError as exc:
            result = {"result": "skipped", "reason": str(exc)}
    storage.put_doc("repos", mapping["id"], {**mapping, "vercel": {"checked_at": iso(now), **result}})
    return result


def _apply(storage: Storage, mapping: dict, deploy: dict | None, now: datetime) -> dict:
    if deploy is None:
        return {"result": "clear", "state": None}
    state = deploy.get("state") or deploy.get("readyState")
    created = deploy.get("created") or deploy.get("createdAt")
    info = {"state": state, "sha": (deploy.get("meta") or {}).get("githubCommitSha"), "url": deploy.get("url"),
            "deployed_at": iso(datetime.fromtimestamp(created / 1000, UTC)) if created else None}
    if state not in SETTLED:
        return {"result": "building", **info}
    failed = [{"id": deploy.get("uid") or deploy.get("id"), **info, "inspector_url": deploy.get("inspectorUrl")}] if state == "ERROR" else []
    result = rollup.apply(
        storage, mapping, "vercel", failed, now, label="Vercel", noun="failed production deploy",
        cleared="A newer production deployment is ready.", field="deployments",
        severity=lambda items: "degraded", describe=lambda d: f"{(d['sha'] or 'deploy')[:7]} failed to build")
    return {**{k: v for k, v in result.items() if k != "open"}, **info}
