"""Keeps site checks off private networks on a shared deployment.

With OPSRELAY_BLOCK_PRIVATE_URLS=1 (set on Vercel), a site URL may not resolve to a loopback, private,
link-local or other non-public address, both when it is added and on every request a check makes
(redirects included), so a site can't be used to reach the deployment's internal network.
Local development leaves it off so localhost sites still work.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from urllib.parse import urlsplit

import httpx


def enabled() -> bool:
    return os.environ.get("OPSRELAY_BLOCK_PRIVATE_URLS") == "1"


def private_reason(host: str) -> str | None:
    """Why `host` is off limits, or None. A host that doesn't resolve is left to fail on its own."""
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except (socket.gaierror, UnicodeError):
        return None
    for address in addresses:
        if not ipaddress.ip_address(address.split("%")[0]).is_global:
            return f"{host} resolves to a non-public address ({address})."
    return None


def check_url(url: str) -> str | None:
    return private_reason(urlsplit(url).hostname or "") if enabled() else None


async def guard_request(request: httpx.Request) -> None:
    """httpx request hook: refuse each request (and redirect hop) to a non-public address."""
    if enabled() and (reason := await asyncio.to_thread(private_reason, request.url.host)):
        raise httpx.ConnectError(f"Blocked: {reason}", request=request)
