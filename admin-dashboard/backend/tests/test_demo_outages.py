"""The API writes only supported local demo modes and never returns their names."""


from uptime.api import create_app


def test_demo_controls_activate_and_restore(tmp_path, monkeypatch, settings, clock):
    for app in ("shopping", "message-platform"):
        (tmp_path / app / "backend" / "app").mkdir(parents=True)
    monkeypatch.setenv("OPSRELAY_DEMO_REPO_ROOT", str(tmp_path))
    from .conftest import make_sqlite, signed_in

    client = signed_in(create_app(make_sqlite(tmp_path), settings, clock=clock, demo_controls=True))
    for service, scenario, app in (("novamart", "payment", "shopping"), ("novaconnect", "both", "message-platform")):
        url = f"/api/v1/demo/outages/{service}"
        assert client.post(url, json={"scenario": "invalid"}).status_code == 422
        assert client.post(url, json={"scenario": "send" if service == "novamart" else "payment"}).status_code == 422
        response = client.post(url, json={"scenario": scenario})
        assert response.status_code == 200
        assert scenario not in response.text
        fault = tmp_path / app / "backend" / "data" / "opsrelay-outage.json"
        assert fault.exists()
        assert client.delete(url).status_code == 200
        assert not fault.exists()


def test_demo_mutations_are_refused_outside_development(tmp_path, monkeypatch, settings, clock):
    for app in ("shopping", "message-platform"):
        (tmp_path / app / "backend" / "app").mkdir(parents=True)
    monkeypatch.setenv("OPSRELAY_DEMO_REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("OPSRELAY_ENV", "production")
    from .conftest import make_sqlite, signed_in

    client = signed_in(create_app(make_sqlite(tmp_path), settings, clock=clock))
    calls = [("post", "/api/v1/demo/outages/novamart", {"scenario": "payment"}), ("delete", "/api/v1/demo/outages/novamart", None),
             ("post", "/api/v1/demo/bad-deploys/novamart", {"scenario": 1}), ("delete", "/api/v1/demo/bad-deploys/novamart", None),
             ("post", "/api/v1/demo/checkouts", {"count": 1}), ("post", "/api/v1/incidents/UPT-x/fix-merged", None)]
    for method, url, body in calls:
        response = getattr(client, method)(url, **({"json": body} if body else {}))
        assert response.status_code == 404, url
        assert "OPSRELAY_ENV" in response.json()["error"]["message"]
    assert not (tmp_path / "shopping" / "backend" / "data").exists()
