import json
import threading
import urllib.error
import urllib.request

import pytest

from opsrelay_dashboard.config import AppEntry, DashboardConfig, load_config
from opsrelay_dashboard.repairs import RepairStore
from opsrelay_dashboard.server import Dashboard, serve
from opsrelay_dashboard.uptime import Target, UptimeMonitor

VERIFIED = {
    "outcome": "verified", "reason": "fixed", "app": "shopping", "commit_sha": "3f2c9e1aa",
    "finished_at": "2026-11-14T10:00:00Z", "flaky": False, "patch": "--- a/x\n+++ b/x\n",
    "changed_files": ["shopping/src/shopping/cart.py"],
    "diagnosis": {"likely_cause": "<img src=x onerror=alert(1)>", "confidence": 0.9, "explanation": "e",
                  "evidence": [], "suspect_files": [], "failing_tests": []},
    "baseline": [{"command": ["python", "-m", "pytest"], "passed": False, "duration_seconds": 0.1, "output_tail": "F"}],
    "reproduce_after_patch": [], "patched_suite": None,
    "attempts": [{"round": 1, "index": 1, "passed": True, "reason": "ok", "changed_lines": 3, "summary": "s"}],
}


@pytest.fixture
def dashboard_url(tmp_path, health_server):
    up_url, down_url = health_server
    repairs = tmp_path / "repairs"
    repairs.mkdir()
    (repairs / "r1.json").write_text(json.dumps(VERIFIED))
    (repairs / "broken.json").write_text("{not json")
    config = DashboardConfig(
        apps=[AppEntry("shopping", "Shopping", up_url), AppEntry("messaging", "Messaging", down_url)],
        repairs_dir=repairs, uptime_log=None, github_repo=None,
    )
    monitor = UptimeMonitor([Target(a.name, a.health_url) for a in config.apps])
    monitor.check_all()
    server = serve(Dashboard(config, monitor, RepairStore(repairs), ci=None), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, dict(r.headers), r.read()


def test_status_api_combines_uptime_ci_and_repairs(dashboard_url):
    status, headers, body = get(f"{dashboard_url}/api/status")
    data = json.loads(body)

    assert status == 200 and headers["Content-Type"] == "application/json"
    assert [(a["name"], a["uptime"]["status"]) for a in data["apps"]] == [("shopping", "up"), ("messaging", "down")]
    assert data["apps"][0]["ci"]["state"] == "unconfigured"
    assert [r["id"] for r in data["repairs"]] == ["r1"]  # broken.json is skipped
    assert data["totals"] == {"apps_up": 1, "apps": 2, "repairs_verified": 1, "repairs_needs_review": 0}


def test_repair_detail_and_unknown_ids(dashboard_url):
    _, _, body = get(f"{dashboard_url}/api/repairs/r1")
    detail = json.loads(body)
    assert detail["commit_sha"] == "3f2c9e1"
    assert detail["detail"]["attempts"][0]["changed_lines"] == 3

    for bad in ("nope", "..%2F..%2Fetc%2Fpasswd", ".hidden"):
        with pytest.raises(urllib.error.HTTPError) as exc:
            get(f"{dashboard_url}/api/repairs/{bad}")
        assert exc.value.code == 404


def test_page_and_assets_are_served_with_security_headers(dashboard_url):
    status, headers, body = get(f"{dashboard_url}/")
    assert status == 200 and b"<title>OpsRelay Status</title>" in body
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    for asset in ("app.js", "app.css"):
        assert get(f"{dashboard_url}/static/{asset}")[0] == 200
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(f"{dashboard_url}/static/../server.py")
    assert exc.value.code == 404


def test_writes_are_rejected(dashboard_url):
    request = urllib.request.Request(f"{dashboard_url}/api/status", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=5)
    assert exc.value.code == 405


def test_frontend_never_injects_html():
    from opsrelay_dashboard.server import STATIC_DIR

    js = (STATIC_DIR / "app.js").read_text()
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "document.write" not in js


def test_default_config_loads():
    config = load_config()
    assert [a.name for a in config.apps] == ["shopping", "messaging"]
    assert config.repairs_dir.name == "repairs"
