"""Safe, read-only checks for the repair agent's external dependencies."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel
from pymongo import MongoClient
from pymongo.errors import ConfigurationError, OperationFailure, PyMongoError, ServerSelectionTimeoutError

from .storage.mongo import escape_credentials, is_atlas


class DependencyCheck(BaseModel):
    id: Literal["github", "mongodb", "openai"]
    name: str
    status: Literal["operational", "degraded", "down", "not_configured"]
    summary: str
    action: str | None = None
    checked_at: datetime
    latency_ms: float | None = None


def _result(id: str, name: str, status: str, summary: str, checked_at: datetime,
            action: str | None = None, started: float | None = None) -> DependencyCheck:
    return DependencyCheck(id=id, name=name, status=status, summary=summary, action=action,
                           checked_at=checked_at,
                           latency_ms=round((time.monotonic() - started) * 1000, 1) if started is not None else None)


async def _github(client: httpx.AsyncClient, env: Mapping[str, str], checked_at: datetime) -> DependencyCheck:
    token, owner = env.get("GITHUB_TOKEN", ""), env.get("GITHUB_OWNER", "")
    repo = env.get("GITHUB_REPO", "agentic-mongodb")
    configured = bool(token and owner)
    url = (f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
           if configured else "https://api.github.com/rate_limit")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "opsrelay-dependency-check"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    started = time.monotonic()
    try:
        response = await client.get(url, headers=headers)
    except httpx.TimeoutException:
        return _result("github", "GitHub", "down", "GitHub API timed out.", checked_at,
                       "Check GitHub availability and network access.", started)
    except httpx.RequestError:
        return _result("github", "GitHub", "down", "Cannot reach the GitHub API.", checked_at,
                       "Check DNS, network access, and GitHub availability.", started)
    if response.status_code == 200:
        if configured:
            return _result("github", "GitHub", "operational", "Repository access verified.", checked_at,
                           started=started)
        missing = ", ".join(key for key, value in (("GITHUB_TOKEN", token), ("GITHUB_OWNER", owner)) if not value)
        return _result("github", "GitHub", "not_configured", "GitHub API is reachable; agent access is incomplete.",
                       checked_at, f"Set {missing} for the repair agent.", started)
    if response.status_code == 401:
        return _result("github", "GitHub", "degraded", "GitHub rejected the agent token.", checked_at,
                       "Check GITHUB_TOKEN.", started)
    if response.status_code in (403, 429):
        limited = response.status_code == 429 or response.headers.get("x-ratelimit-remaining") == "0"
        return _result("github", "GitHub", "degraded",
                       "GitHub rate limit reached." if limited else "GitHub denied API access.", checked_at,
                       "Wait for the rate limit to reset." if limited else "Check token permissions and repository access.",
                       started)
    if response.status_code == 404:
        return _result("github", "GitHub", "degraded", "Repository not found or inaccessible.", checked_at,
                       "Check GITHUB_OWNER, GITHUB_REPO, and token permissions.", started)
    return _result("github", "GitHub", "down" if response.status_code >= 500 else "degraded",
                   f"GitHub API returned HTTP {response.status_code}.", checked_at,
                   "Check GitHub availability and agent access.", started)


def _ping_mongodb(uri: str) -> None:
    client = MongoClient(escape_credentials(uri), appname="opsrelay-dependency-check",
                         serverSelectionTimeoutMS=2500, connectTimeoutMS=2500, socketTimeoutMS=2500)
    try:
        client.admin.command("ping")
    finally:
        client.close()


async def _mongodb(env: Mapping[str, str], checked_at: datetime,
                   ping: Callable[[str], None]) -> DependencyCheck:
    uri = env.get("MONGODB_URI", "")
    name = "MongoDB Atlas" if not uri or is_atlas(uri) else "MongoDB"
    if not uri or "<" in uri:
        return _result("mongodb", name, "not_configured", "Database connection is not configured.", checked_at,
                       "Set MONGODB_URI for the uptime API and repair agent.")
    started = time.monotonic()
    try:
        await asyncio.to_thread(ping, uri)
    except ConfigurationError:
        return _result("mongodb", name, "degraded", "Database connection string is invalid.", checked_at,
                       "Check MONGODB_URI.", started)
    except OperationFailure:
        return _result("mongodb", name, "degraded", "Database rejected the connection.", checked_at,
                       "Check database credentials and permissions.", started)
    except ServerSelectionTimeoutError:
        return _result("mongodb", name, "down", "Cannot reach the database.", checked_at,
                       "Check Atlas network access, DNS, and cluster status.", started)
    except PyMongoError:
        return _result("mongodb", name, "down", "Database connection failed.", checked_at,
                       "Check the cluster and connection settings.", started)
    return _result("mongodb", name, "operational", "Database ping succeeded.", checked_at, started=started)


async def _openai(client: httpx.AsyncClient, env: Mapping[str, str], checked_at: datetime) -> DependencyCheck:
    token = env.get("OPENAI_API_KEY", "")
    if not token:
        return _result("openai", "OpenAI", "not_configured", "AI provider access is not configured.", checked_at,
                       "Set OPENAI_API_KEY for the repair agent.")
    started = time.monotonic()
    try:
        response = await client.get("https://api.openai.com/v1/models",
                                    headers={"Authorization": f"Bearer {token}"})
    except httpx.TimeoutException:
        return _result("openai", "OpenAI", "down", "OpenAI API timed out.", checked_at,
                       "Check provider availability and network access.", started)
    except httpx.RequestError:
        return _result("openai", "OpenAI", "down", "Cannot reach the OpenAI API.", checked_at,
                       "Check DNS, network access, and provider availability.", started)
    if response.status_code == 200:
        return _result("openai", "OpenAI", "operational", "API access verified; model calls are not tested.",
                       checked_at, started=started)
    if response.status_code == 401:
        return _result("openai", "OpenAI", "degraded", "OpenAI rejected the API key.", checked_at,
                       "Check OPENAI_API_KEY.", started)
    if response.status_code == 429:
        return _result("openai", "OpenAI", "degraded", "OpenAI rate limit or quota reached.", checked_at,
                       "Check account limits and usage.", started)
    return _result("openai", "OpenAI", "down" if response.status_code >= 500 else "degraded",
                   f"OpenAI API returned HTTP {response.status_code}.", checked_at,
                   "Check provider status and API access.", started)


async def check_dependencies(*, env: Mapping[str, str] | None = None,
                             transport: httpx.AsyncBaseTransport | None = None,
                             mongo_ping: Callable[[str], None] = _ping_mongodb,
                             checked_at: datetime | None = None) -> list[DependencyCheck]:
    """Check dependencies concurrently without returning credentials or provider bodies."""
    env = env if env is not None else os.environ
    checked_at = checked_at or datetime.now(timezone.utc)
    async with httpx.AsyncClient(timeout=3, transport=transport) as client:
        return list(await asyncio.gather(_github(client, env, checked_at),
                                         _mongodb(env, checked_at, mongo_ping),
                                         _openai(client, env, checked_at)))
