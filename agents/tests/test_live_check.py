"""Live check classification. Every check here runs against a local fixture, not the live app."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agents.live_check import load_service_config, main, run_live_check

from .messaging_support import HangingServer, free_port


def fast(url, timeout=2.0, **overrides):
    config = load_service_config("messaging", base_url=url, timeout=timeout)
    return config.model_copy(update={"delivery_wait_seconds": 1.0, "poll_interval_seconds": 0.2, **overrides})


def check(url, **overrides):
    return run_live_check("messaging", config=fast(url, **overrides), target="fixture")


@pytest.fixture
def stub_service():
    """Tiny HTTP service scripted per test: {(method, path_prefix): (status, body | fn(request_json))}."""
    servers = []

    def start(routes):
        class Handler(BaseHTTPRequestHandler):
            def _reply(self, method, payload=None):
                for (m, prefix), (status, body) in routes.items():
                    if m == method and self.path.startswith(prefix):
                        body = body(payload) if callable(body) else body
                        data = body if isinstance(body, bytes) else json.dumps(body).encode()
                        self.send_response(status)
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                        return
                self.send_response(404)
                self.end_headers()

            def do_GET(self):  # noqa: N802
                self._reply("GET")

            def do_POST(self):  # noqa: N802
                self._reply("POST", json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}"))

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_port}"

    yield start
    for server in servers:
        server.shutdown()


def test_healthy_service_delivers_the_exact_probe(messaging, deploy):
    result = check(deploy(messaging.good_sha).url)

    assert result.ok and result.reachable and result.healthy
    assert result.delivery == "delivered" and result.failure_kind is None
    assert result.target == "fixture"
    assert [s.name for s in result.steps] == ["health", "send", "readback"]
    assert result.probe_id.startswith("opsrelay-")


def test_every_check_uses_a_fresh_probe_id(messaging, deploy):
    url = deploy(messaging.good_sha).url

    assert check(url).probe_id != check(url).probe_id


def test_connection_refused_is_unreachable():
    result = check(f"http://127.0.0.1:{free_port()}")

    assert not result.reachable and not result.healthy
    assert result.failure_kind == "connection_refused"
    assert result.delivery == "not_attempted"


def test_timeout_is_distinguished_from_refused():
    server = HangingServer()
    try:
        result = check(server.url, timeout=0.5)
    finally:
        server.close()

    assert not result.reachable
    assert result.failure_kind == "connection_timeout"
    assert "no response within the timeout" in result.error
    assert result.steps[0].latency_ms < 2000


def test_health_http_error_is_reachable_but_unhealthy(stub_service):
    url = stub_service({("GET", "/health"): (503, {"status": "degraded"})})

    result = check(url)

    assert result.reachable and not result.healthy
    assert result.failure_kind == "http_error" and result.http_status == 503
    assert result.delivery == "not_attempted"


def test_send_http_error_is_not_a_delivery_timeout(stub_service):
    url = stub_service({("GET", "/health"): (200, {"status": "ok"}), ("POST", "/probe/messages"): (500, {"error": "boom"})})

    result = check(url)

    assert result.reachable and result.healthy
    assert result.failure_kind == "http_error" and result.http_status == 500
    assert "boom" in result.error


def test_accepted_but_never_delivered(messaging, deploy):
    result = check(deploy(messaging.bad_sha).url)

    assert result.reachable and result.healthy
    assert result.delivery == "not_delivered" and result.failure_kind == "not_delivered"
    assert result.steps[1].name == "send" and result.steps[1].status_code == 202
    assert f"probe {result.probe_id} not in inbox 'opsrelay-probe'" in result.error


def test_readback_without_messages_list_is_invalid(stub_service):
    url = stub_service({("GET", "/health"): (200, b"ok"), ("POST", "/probe/messages"): (202, {}),
                        ("GET", "/probe/inbox/"): (200, {"items": []})})

    result = check(url)

    assert result.failure_kind == "invalid_response"
    assert result.delivery == "not_delivered"


@pytest.mark.parametrize("delivered, kind", [(True, None), (False, "not_delivered")])
def test_synthetic_probe_mode(stub_service, delivered, kind):
    def synthetic(request):
        return {"probe_id": request["probe_id"], "delivered": delivered, "latency_ms": 12.0,
                "error": None if delivered else "worker dropped message"}

    url = stub_service({("GET", "/health"): (200, {"status": "ok"}), ("POST", "/probe/delivery"): (200, synthetic)})

    result = check(url, mode="synthetic")

    assert result.mode == "synthetic" and [s.name for s in result.steps] == ["health", "synthetic"]
    assert result.failure_kind == kind
    assert result.ok is delivered
    if not delivered:
        assert result.error == "worker dropped message"


def test_synthetic_probe_must_echo_the_probe_id(stub_service):
    url = stub_service({("GET", "/health"): (200, {}), ("POST", "/probe/delivery"): (200, {"probe_id": "old", "delivered": True})})

    result = check(url, mode="synthetic")

    assert not result.ok and result.failure_kind == "invalid_response"


def test_service_url_and_timeout_precedence(monkeypatch):
    monkeypatch.setenv("OPSRELAY_MESSAGING_URL", "http://env-host:9000/")
    monkeypatch.setenv("OPSRELAY_LIVE_TIMEOUT", "7")

    assert load_service_config("messaging").base_url == "http://env-host:9000"
    assert load_service_config("messaging").timeout_seconds == 7
    config = load_service_config("messaging", base_url="http://arg-host:1", timeout=2)
    assert (config.base_url, config.timeout_seconds) == ("http://arg-host:1", 2)
    with pytest.raises(ValueError):
        load_service_config("messaging", base_url="file:///etc/passwd")


def test_cli_exit_codes_and_fixture_label(messaging, deploy, capsys):
    url = deploy(messaging.good_sha).url

    assert main(["messaging", "--url", url, "--timeout", "2", "--fixture"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["target"] == "fixture" and out["delivery"] == "delivered"

    assert main(["messaging", "--url", f"http://127.0.0.1:{free_port()}", "--timeout", "1"]) == 1
    assert json.loads(capsys.readouterr().out)["failure_kind"] == "connection_refused"
    assert main(["nope"]) == 2
