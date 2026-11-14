"""The admin API writes only supported local demo modes and never returns their names."""

from fastapi.testclient import TestClient

from uptime.api import create_app


def test_demo_controls_require_token_and_restore(tmp_path, monkeypatch, settings, clock):
    for app in ("shopping", "message-platform"):
        (tmp_path / app / "backend" / "app").mkdir(parents=True)
    monkeypatch.setenv("OPSRELAY_DEMO_REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("OPSRELAY_ADMIN_TOKEN", "demo-secret")
    from .conftest import make_sqlite

    client = TestClient(create_app(make_sqlite(tmp_path), settings, clock=clock))
    headers = {"Authorization": "Bearer demo-secret"}
    for service, scenario, app in (("novamart", "payment", "shopping"), ("novaconnect", "both", "message-platform")):
        url = f"/api/v1/demo/outages/{service}"
        assert client.post(url, json={"scenario": scenario}).status_code == 401
        assert client.post(url, json={"scenario": "invalid"}, headers=headers).status_code == 422
        assert client.post(url, json={"scenario": "send" if service == "novamart" else "payment"}, headers=headers).status_code == 422
        response = client.post(url, json={"scenario": scenario}, headers=headers)
        assert response.status_code == 200
        assert scenario not in response.text
        fault = tmp_path / app / "backend" / "data" / "opsrelay-outage.json"
        assert fault.exists()
        assert client.delete(url, headers=headers).status_code == 200
        assert not fault.exists()
