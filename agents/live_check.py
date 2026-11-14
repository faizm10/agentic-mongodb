"""One-shot live check: is the service up, and does a probe message actually arrive?

Called by Hamza's 30-second polling loop and by Mo after a fix is deployed:

    from agents.live_check import run_live_check
    result = run_live_check("messaging")                     # never raises on network errors
    result.ok, result.failure_kind, result.delivery

CLI:

    python -m agents live-check messaging [--url URL] [--timeout SECONDS] [--fixture]

Exit code 0 when the probe was delivered, 1 when the check failed, 2 on bad usage.

Every check uses a fresh probe id. Failures are classified as:
  connection_timeout  no response within the timeout (cause unknown)
  connection_refused  nothing accepting connections at the URL
  connection_error    DNS, TLS or other network failure before any HTTP response
  http_error          the service answered with 4xx/5xx
  invalid_response    the service answered 2xx with a body that breaks the contract
  not_delivered       send was accepted but the probe id never appeared in read-back
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .contracts import UNREACHABLE, FailureKind, LiveCheckResult, ProbeStep

SERVICES_CONFIG_PATH = Path(__file__).with_name("services.toml")


class ServiceConfig(BaseModel):
    name: str
    app: str
    base_url: str
    timeout_seconds: float = 5.0
    mode: Literal["send_readback", "synthetic"] = "send_readback"
    health_path: str = "/health"
    send_path: str = "/probe/messages"
    readback_path: str = "/probe/inbox/{inbox}"
    synthetic_path: str = "/probe/delivery"
    test_inbox: str = "opsrelay-probe"
    delivery_wait_seconds: float = 10.0
    poll_interval_seconds: float = 0.5


def load_service_config(
    service: str,
    base_url: str | None = None,
    timeout: float | None = None,
    path: Path = SERVICES_CONFIG_PATH,
) -> ServiceConfig:
    with path.open("rb") as f:
        services = tomllib.load(f)["services"]
    if service not in services:
        raise KeyError(f"unknown service {service!r}; configured: {sorted(services)}")
    config = ServiceConfig(name=service, **services[service])
    url = base_url or os.environ.get(f"OPSRELAY_{service.upper()}_URL") or config.base_url
    env_timeout = os.environ.get("OPSRELAY_LIVE_TIMEOUT")
    config.base_url = url.rstrip("/")
    config.timeout_seconds = timeout or (float(env_timeout) if env_timeout else config.timeout_seconds)
    if urllib.parse.urlparse(config.base_url).scheme not in ("http", "https"):
        raise ValueError(f"base_url must be http(s), got {config.base_url!r}")
    return config


def run_live_check(
    service: str,
    base_url: str | None = None,
    timeout: float | None = None,
    *,
    target: Literal["live", "fixture"] = "live",
    config: ServiceConfig | None = None,
) -> LiveCheckResult:
    """Run one health check and one send-and-deliver probe. Network errors become results, not exceptions."""
    config = config or load_service_config(service, base_url, timeout)
    probe_id = f"opsrelay-{uuid.uuid4().hex}"
    steps: list[ProbeStep] = []

    def finish(**fields) -> LiveCheckResult:
        return LiveCheckResult(
            service=config.name, app=config.app, checked_at=datetime.now(UTC), base_url=config.base_url,
            target=target, probe_id=probe_id, mode=config.mode, steps=steps, **fields,
        )

    health = _request(config, "health", "GET", config.health_path)
    steps.append(health.step)
    if health.step.failure_kind in UNREACHABLE:
        return finish(reachable=False, healthy=False, delivery="not_attempted",
                      failure_kind=health.step.failure_kind, error=health.step.error)
    if health.step.failure_kind is not None:
        return finish(reachable=True, healthy=False, delivery="not_attempted", failure_kind=health.step.failure_kind,
                      error=health.step.error, http_status=health.step.status_code)

    if config.mode == "synthetic":
        return _synthetic(config, probe_id, steps, finish)
    return _send_readback(config, probe_id, steps, finish)


def _send_readback(config: ServiceConfig, probe_id: str, steps: list[ProbeStep], finish) -> LiveCheckResult:
    started = time.monotonic()
    send = _request(config, "send", "POST", config.send_path, {
        "probe_id": probe_id, "inbox": config.test_inbox, "body": f"OpsRelay delivery probe {probe_id}",
    })
    steps.append(send.step)
    if send.step.failure_kind is not None:
        return finish(reachable=send.step.failure_kind not in UNREACHABLE,
                      healthy=True, delivery="not_attempted", failure_kind=send.step.failure_kind,
                      error=send.step.error, http_status=send.step.status_code)

    readback_path = config.readback_path.format(inbox=urllib.parse.quote(config.test_inbox, safe=""))
    query = f"{readback_path}?probe_id={urllib.parse.quote(probe_id)}"
    deadline = started + config.delivery_wait_seconds
    last: _Response | None = None
    while True:
        last = _request(config, "readback", "GET", query)
        if last.step.failure_kind is not None:
            steps.append(last.step)
            return finish(reachable=True, healthy=True, delivery="not_delivered", failure_kind=last.step.failure_kind,
                          error=f"read-back failed: {last.step.error}", http_status=last.step.status_code)
        messages = last.json.get("messages") if isinstance(last.json, dict) else None
        if not isinstance(messages, list):
            last.step.failure_kind, last.step.error = "invalid_response", "read-back body has no 'messages' list"
            steps.append(last.step)
            return finish(reachable=True, healthy=True, delivery="not_delivered",
                          failure_kind="invalid_response", error=last.step.error, http_status=last.step.status_code)
        if any(isinstance(m, dict) and m.get("probe_id") == probe_id for m in messages):
            steps.append(last.step)
            return finish(reachable=True, healthy=True, delivery="delivered",
                          delivery_latency_ms=round((time.monotonic() - started) * 1000, 1))
        if time.monotonic() + config.poll_interval_seconds > deadline:
            break
        time.sleep(config.poll_interval_seconds)

    last.step.failure_kind = "not_delivered"
    last.step.error = f"probe {probe_id} not in inbox {config.test_inbox!r} after {config.delivery_wait_seconds:g}s"
    steps.append(last.step)
    return finish(reachable=True, healthy=True, delivery="not_delivered", failure_kind="not_delivered",
                  error=last.step.error, http_status=last.step.status_code)


def _synthetic(config: ServiceConfig, probe_id: str, steps: list[ProbeStep], finish) -> LiveCheckResult:
    """The app runs its own send-and-deliver probe and reports whether ``probe_id`` arrived."""
    probe = _request(config, "synthetic", "POST", config.synthetic_path,
                     {"probe_id": probe_id, "inbox": config.test_inbox})
    steps.append(probe.step)
    if probe.step.failure_kind is not None:
        return finish(reachable=probe.step.failure_kind not in UNREACHABLE,
                      healthy=True, delivery="not_attempted", failure_kind=probe.step.failure_kind,
                      error=probe.step.error, http_status=probe.step.status_code)
    body = probe.json if isinstance(probe.json, dict) else {}
    if body.get("probe_id") != probe_id or not isinstance(body.get("delivered"), bool):
        probe.step.failure_kind = "invalid_response"
        probe.step.error = "synthetic probe must echo probe_id and return a boolean 'delivered'"
        return finish(reachable=True, healthy=True, delivery="not_delivered", failure_kind="invalid_response",
                      error=probe.step.error, http_status=probe.step.status_code)
    if not body["delivered"]:
        probe.step.failure_kind = "not_delivered"
        probe.step.error = str(body.get("error") or f"service reports probe {probe_id} not delivered")
        return finish(reachable=True, healthy=True, delivery="not_delivered", failure_kind="not_delivered",
                      error=probe.step.error, http_status=probe.step.status_code)
    return finish(reachable=True, healthy=True, delivery="delivered", delivery_latency_ms=body.get("latency_ms"))


class _Response:
    def __init__(self, step: ProbeStep, json_body: object = None):
        self.step = step
        self.json = json_body


def _request(config: ServiceConfig, name: str, method: str, path: str, payload: dict | None = None) -> _Response:
    url = config.base_url + path
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Accept": "application/json", "User-Agent": "opsrelay-live-check/1"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    started = time.monotonic()

    def step(**fields) -> ProbeStep:
        return ProbeStep(name=name, method=method, url=url,
                         latency_ms=round((time.monotonic() - started) * 1000, 1), **fields)

    try:
        with urllib.request.urlopen(request, timeout=config.timeout_seconds, context=_tls_context()) as response:  # noqa: S310
            raw = response.read(256 * 1024)
            status = response.status
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode(errors="replace").strip()
        return _Response(step(status_code=exc.code, failure_kind="http_error",
                              error=f"HTTP {exc.code} from {method} {path}" + (f": {detail}" if detail else "")))
    except urllib.error.URLError as exc:
        kind, message = _classify(exc.reason)
        return _Response(step(failure_kind=kind, error=message))
    except (TimeoutError, socket.timeout, OSError) as exc:
        kind, message = _classify(exc)
        return _Response(step(failure_kind=kind, error=message))

    if not raw:
        return _Response(step(status_code=status), None)
    try:
        return _Response(step(status_code=status), json.loads(raw))
    except ValueError:
        if name == "health":  # a plain-text "ok" health body is fine
            return _Response(step(status_code=status), None)
        return _Response(step(status_code=status, failure_kind="invalid_response",
                              error=f"{method} {path} returned non-JSON body"))


def _classify(reason: object) -> tuple[FailureKind, str]:
    if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason):
        return "connection_timeout", f"no response within the timeout ({reason})"
    if isinstance(reason, ConnectionRefusedError) or "refused" in str(reason).lower():
        return "connection_refused", f"connection refused ({reason})"
    return "connection_error", f"could not connect ({reason})"


def _tls_context() -> ssl.SSLContext:
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agents live-check", description="One-shot live service check.")
    parser.add_argument("service", help="service name in agents/services.toml, e.g. messaging")
    parser.add_argument("--url", help="base URL (default: OPSRELAY_<SERVICE>_URL or services.toml)")
    parser.add_argument("--timeout", type=float, help="per-request timeout in seconds")
    parser.add_argument("--fixture", action="store_true", help="label the result as a fixture run, not the live app")
    args = parser.parse_args(argv)
    try:
        config = load_service_config(args.service, args.url, args.timeout)
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    result = run_live_check(args.service, config=config, target="fixture" if args.fixture else "live")
    print(result.model_dump_json(indent=2))
    verdict = "OK delivered" if result.ok else f"FAILED {result.failure_kind}: {result.error}"
    print(f"\n[{result.target}] {result.service} @ {result.base_url}: {verdict}", file=sys.stderr)
    return 0 if result.ok else 1

