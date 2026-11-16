"""External dependency checks report actionable states without leaking credentials."""

import asyncio
from datetime import datetime, timezone

import httpx
from pymongo.errors import ServerSelectionTimeoutError

from uptime.api import create_app
from uptime.dependencies import DependencyCheck, check_dependencies

from .conftest import make_sqlite, signed_in


NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def test_missing_agent_configuration_still_checks_github_reachability():
    def handler(request):
        assert str(request.url) == "https://api.github.com/rate_limit"
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"resources": {}})

    def unexpected_ping(_):
        raise AssertionError("MongoDB should not be contacted without a URI")

    checks = asyncio.run(check_dependencies(env={}, transport=httpx.MockTransport(handler),
                                          mongo_ping=unexpected_ping, checked_at=NOW))
    assert [(c.id, c.status) for c in checks] == [
        ("github", "not_configured"), ("mongodb", "not_configured"), ("openai", "not_configured")]
    assert "reachable" in checks[0].summary
    assert checks[1].latency_ms is None and checks[2].latency_ms is None


def test_configured_checks_verify_access_and_do_not_return_credentials():
    env = {"GITHUB_TOKEN": "github-secret", "GITHUB_OWNER": "example", "GITHUB_REPO": "demo",
           "MONGODB_URI": "mongodb+srv://user:database-secret@cluster.mongodb.net/demo",
           "OPENAI_API_KEY": "openai-secret"}
    seen = []

    def handler(request):
        seen.append(str(request.url))
        assert request.headers.get("Authorization", "").startswith("Bearer ")
        return httpx.Response(200, json={})

    def ping(uri):
        assert uri == env["MONGODB_URI"]
        seen.append("mongodb")

    checks = asyncio.run(check_dependencies(env=env, transport=httpx.MockTransport(handler),
                                          mongo_ping=ping, checked_at=NOW))
    assert [c.status for c in checks] == ["operational"] * 3
    assert set(seen) == {"https://api.github.com/repos/example/demo", "https://api.openai.com/v1/models", "mongodb"}
    assert all(c.latency_ms is not None for c in checks)
    payload = str([c.model_dump() for c in checks])
    assert not any(secret in payload for secret in ("github-secret", "database-secret", "openai-secret"))


def test_failures_are_classified_without_raw_provider_errors():
    env = {"GITHUB_TOKEN": "github-secret", "GITHUB_OWNER": "example",
           "MONGODB_URI": "mongodb+srv://user:database-secret@cluster.mongodb.net/demo",
           "OPENAI_API_KEY": "openai-secret"}

    def handler(request):
        if request.url.host == "api.github.com":
            return httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, text="private github error")
        return httpx.Response(401, text="private openai error")

    def ping(_):
        raise ServerSelectionTimeoutError("database-secret in raw driver error")

    checks = asyncio.run(check_dependencies(env=env, transport=httpx.MockTransport(handler),
                                          mongo_ping=ping, checked_at=NOW))
    assert [(c.id, c.status) for c in checks] == [
        ("github", "degraded"), ("mongodb", "down"), ("openai", "degraded")]
    assert "rate limit" in checks[0].summary
    payload = str([c.model_dump() for c in checks])
    assert "private" not in payload and "database-secret" not in payload


def test_dependency_endpoint_reuses_recent_checks(tmp_path, settings, clock, monkeypatch):
    calls = 0

    async def fake_checks():
        nonlocal calls
        calls += 1
        return [DependencyCheck(id="github", name="GitHub", status="operational",
                                summary="Repository access verified.", checked_at=NOW)]

    monkeypatch.setattr("uptime.api.check_dependencies", fake_checks)
    client = signed_in(create_app(make_sqlite(tmp_path), settings, clock=clock))
    first = client.get("/api/v1/dependencies")
    second = client.get("/api/v1/dependencies")
    assert first.status_code == second.status_code == 200
    assert calls == 1
    assert second.json()["data"][0]["status"] == "operational"
