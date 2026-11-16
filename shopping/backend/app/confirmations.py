"""Order confirmations, sent to the customer through NovaConnect.

The confirmation ID is derived from the checkout ID, so a client retry of the checkout and
every resend during recovery name the same confirmation. NovaConnect stores at most one
confirmation per ID, which makes a resend safe even when an earlier send was recorded but
its response was lost.

An accepted send is not a delivered confirmation: NovaConnect answers ``accepted`` while
delivery is still pending and ``delivered`` once the confirmation reached the customer.
"""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import httpx

DEFAULT_NOVACONNECT_URL = "http://127.0.0.1:8200"
DEFAULT_TIMEOUT_SECONDS = 2.0
DELIVERED = "delivered"
ACCEPTED = "accepted"


def confirmation_id_for(checkout_id: str) -> str:
    return f"cnf-{checkout_id}"


@dataclass(frozen=True)
class ConfirmationRequest:
    confirmation_id: str
    checkout_id: str
    order_id: str
    recipient: str
    body: str
    request_id: str | None = None
    probe: bool = False


@dataclass(frozen=True)
class ConfirmationResult:
    confirmation_id: str
    status: str  # "accepted" (recorded, delivery pending) | "delivered"
    message_id: str | None
    replayed: bool
    elapsed_ms: float


class ConfirmationUnavailable(Exception):
    """NovaConnect did not give a usable answer. Whether it recorded the confirmation is unknown."""

    def __init__(self, kind: str, message: str, *, elapsed_ms: float, http_status: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind  # timeout | connection_error | http_error | invalid_response
        self.message = message
        self.elapsed_ms = elapsed_ms
        self.http_status = http_status

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "message": self.message, "elapsed_ms": self.elapsed_ms,
                "http_status": self.http_status}


class ConfirmationClient(Protocol):
    def send(self, request: ConfirmationRequest) -> ConfirmationResult: ...
    def lookup(self, confirmation_id: str) -> dict[str, Any] | None: ...


class HttpConfirmationClient:
    """NovaConnect's ``/api/confirmations`` API over HTTP with a short, explicit timeout."""

    def __init__(self, base_url: str | None = None, timeout_seconds: float | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = (base_url or os.environ.get("NOVACONNECT_URL") or DEFAULT_NOVACONNECT_URL).rstrip("/")
        self.timeout_seconds = float(timeout_seconds or os.environ.get("NOVAMART_CONFIRMATION_TIMEOUT")
                                     or DEFAULT_TIMEOUT_SECONDS)
        self._transport = transport

    def _call(self, method: str, path: str, *, json: dict | None = None,
              request_id: str | None = None) -> tuple[httpx.Response, float]:
        started = time.perf_counter()
        headers = {"X-Request-ID": request_id} if request_id else {}
        try:
            with httpx.Client(timeout=self.timeout_seconds, transport=self._transport) as client:
                response = client.request(method, f"{self.base_url}{path}", json=json, headers=headers)
        except httpx.TimeoutException as exc:
            elapsed = round((time.perf_counter() - started) * 1000, 1)
            raise ConfirmationUnavailable(
                "timeout", f"NovaConnect did not answer {method} {path} within {self.timeout_seconds:.1f}s.",
                elapsed_ms=elapsed) from exc
        except httpx.HTTPError as exc:
            elapsed = round((time.perf_counter() - started) * 1000, 1)
            raise ConfirmationUnavailable(
                "connection_error", f"NovaConnect {method} {path} failed: {type(exc).__name__}: {exc}",
                elapsed_ms=elapsed) from exc
        return response, round((time.perf_counter() - started) * 1000, 1)

    def send(self, request: ConfirmationRequest) -> ConfirmationResult:
        payload = asdict(request)
        payload.pop("request_id")
        response, elapsed = self._call("POST", "/api/confirmations", json=payload, request_id=request.request_id)
        if not response.is_success:
            raise ConfirmationUnavailable("http_error", f"NovaConnect answered HTTP {response.status_code}.",
                                          elapsed_ms=elapsed, http_status=response.status_code)
        try:
            body = response.json()
            status = body["status"]
            if body["confirmation_id"] != request.confirmation_id or status not in (ACCEPTED, DELIVERED):
                raise ValueError(f"unexpected confirmation {body!r}")
        except (ValueError, KeyError, TypeError) as exc:
            raise ConfirmationUnavailable("invalid_response", f"NovaConnect sent an invalid confirmation: {exc}",
                                          elapsed_ms=elapsed, http_status=response.status_code) from exc
        return ConfirmationResult(confirmation_id=request.confirmation_id, status=status,
                                  message_id=body.get("message_id"),
                                  replayed=response.headers.get("Idempotent-Replay") == "true", elapsed_ms=elapsed)

    def lookup(self, confirmation_id: str) -> dict[str, Any] | None:
        """NovaConnect's record for this confirmation, or None when NovaConnect has none."""
        response, elapsed = self._call("GET", f"/api/confirmations/{confirmation_id}")
        if response.status_code == 404:
            return None
        if not response.is_success:
            raise ConfirmationUnavailable("http_error", f"NovaConnect answered HTTP {response.status_code}.",
                                          elapsed_ms=elapsed, http_status=response.status_code)
        return response.json()
