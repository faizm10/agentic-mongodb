"""Probe classification: availability vs functional correctness, against fake and real sockets."""

import asyncio
import socket

import httpx
import pytest

from uptime.classify import classify, investigation_route
from uptime.models import ServiceConfig
from uptime.probes import probe_availability, probe_frontend, probe_functional

from .conftest import NOVACONNECT, NOVAMART


async def run(apps, service, mode):
    apps.mode[service.id] = mode
    async with httpx.AsyncClient(transport=apps.transport()) as client:
        results = await asyncio.gather(probe_frontend(client, service), probe_availability(client, service),
                                       probe_functional(client, service))
    return results, classify(*results)


@pytest.mark.parametrize(("mode", "status", "failure_type"), [
    ("healthy", "operational", None),
    ("functional_bug", "degraded", "functional_probe_failed"),
    ("probe_timeout", "degraded", "functional_probe_timeout"),
    ("probe_garbage", "degraded", "functional_probe_error"),
    ("backend_down", "down", "backend_unreachable"),
    ("availability_timeout", "down", "backend_timeout"),
    ("frontend_down", "down", "frontend_unreachable"),
])
def test_classification(apps, mode, status, failure_type):
    _, (got_status, got_type, summary) = asyncio.run(run(apps, NOVAMART, mode))
    assert (got_status, got_type) == (status, failure_type), summary


def test_http_200_homepage_is_not_enough(apps):
    """The website answers 200, but the checkout probe fails: degraded, not operational."""
    (frontend, availability, functional), (status, _, _) = asyncio.run(run(apps, NOVAMART, "functional_bug"))
    assert frontend.ok and frontend.http_status == 200 and availability.ok
    assert status == "degraded"


@pytest.mark.parametrize(("service", "check", "source"), [
    (NOVAMART, "promotion_discounts", "backend/app/pricing.py::compute_discount"),
    (NOVACONNECT, "message_ordering", "backend/app/messages.py::sort_timeline"),
])
def test_functional_failure_evidence_is_parsed_for_both_app_formats(apps, service, check, source):
    (_, _, functional), _ = asyncio.run(run(apps, service, "functional_bug"))
    assert functional.error_kind == "probe_failed" and functional.http_status == 503
    failure = functional.details["failures"][0]
    assert failure["check"] == check and failure["source"] == source and failure["reason"]


def test_investigation_routes_distinguish_outages_from_logic_failures():
    assert investigation_route("backend_unreachable")[0] == "service_health"
    assert investigation_route("backend_timeout")[0] == "service_health"
    assert investigation_route("functional_probe_failed")[0] == "investigate"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_unreachable_application_real_socket():
    port = _free_port()  # nothing listens here
    svc = NOVAMART.model_copy(update={"backend_url": f"http://127.0.0.1:{port}", "frontend_url": f"http://127.0.0.1:{port}"})

    async def go():
        async with httpx.AsyncClient() as client:
            return await probe_availability(client, svc)

    result = asyncio.run(go())
    assert not result.ok and result.error_kind == "connection_refused"


def test_monitoring_timeout_real_socket():
    """A server that accepts the connection but never answers must time out, not hang the monitor."""

    async def go():
        async def never_answer(reader, writer):
            await asyncio.sleep(5)
            writer.close()

        server = await asyncio.start_server(never_answer, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        svc: ServiceConfig = NOVAMART.model_copy(update={"backend_url": f"http://127.0.0.1:{port}", "timeout_seconds": 0.3})
        async with server, httpx.AsyncClient() as client:
            started = asyncio.get_running_loop().time()
            result = await probe_availability(client, svc)
            return result, asyncio.get_running_loop().time() - started

    result, elapsed = asyncio.run(go())
    assert result.error_kind == "timeout" and not result.ok
    assert elapsed < 1.5, f"probe took {elapsed:.2f}s with a 0.3s timeout"
