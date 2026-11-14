"""Tiny read-only HTTP server: static dashboard + JSON status API.

GET /                     dashboard page
GET /static/<file>        app.js / app.css
GET /api/status           apps (uptime + CI) and recent repairs
GET /api/repairs/<id>     one repair with diagnosis, patch and test output
"""

from __future__ import annotations

import json
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .ci import GitHubActions
from .config import DashboardConfig
from .repairs import RepairStore
from .uptime import UptimeMonitor

STATIC_DIR = Path(__file__).with_name("static")
STATIC_FILES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class Dashboard:
    def __init__(
        self,
        config: DashboardConfig,
        monitor: UptimeMonitor,
        repairs: RepairStore,
        ci: GitHubActions | None,
        demo: bool = False,
    ):
        self.config = config
        self.monitor = monitor
        self.repairs = repairs
        self.ci = ci
        self.demo = demo

    def status(self) -> dict:
        apps = []
        for app in self.config.apps:
            ci = self.ci.latest(app.workflow) if self.ci else {
                "state": "unconfigured", "runs": [], "error": "github_repo not configured",
            }
            apps.append({"name": app.name, "label": app.label, "uptime": self.monitor.summary(app.name), "ci": ci})
        repairs = self.repairs.list()
        return {
            "generated_at": time.time(),
            "refresh_seconds": self.config.refresh_seconds,
            "demo": self.demo,
            "github_repo": self.config.github_repo,
            "apps": apps,
            "repairs": repairs,
            "totals": {
                "apps_up": sum(a["uptime"]["status"] == "up" for a in apps),
                "apps": len(apps),
                "repairs_verified": sum(r["outcome"] == "verified" for r in repairs),
                "repairs_needs_review": sum(r["outcome"] == "needs_review" for r in repairs),
            },
        }


def make_handler(dashboard: Dashboard) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "opsrelay-dashboard"
        sys_version = ""

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path.removeprefix("/static/"))
            if path == "/api/status":
                return self._json(dashboard.status())
            if path.startswith("/api/repairs/"):
                repair = dashboard.repairs.get(path.removeprefix("/api/repairs/"))
                return self._json(repair) if repair else self._error(HTTPStatus.NOT_FOUND)
            return self._error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            self._error(HTTPStatus.METHOD_NOT_ALLOWED)

        do_PUT = do_DELETE = do_PATCH = do_POST

        def _static(self, name: str) -> None:
            if name not in STATIC_FILES:
                return self._error(HTTPStatus.NOT_FOUND)
            self._send(HTTPStatus.OK, (STATIC_DIR / name).read_bytes(), STATIC_FILES[name])

        def _json(self, payload: dict) -> None:
            self._send(HTTPStatus.OK, json.dumps(payload).encode(), "application/json")

        def _error(self, status: HTTPStatus) -> None:
            self._send(status, json.dumps({"error": status.phrase}).encode(), "application/json")

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in SECURITY_HEADERS.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:  # quiet by default
            pass

    return Handler


def serve(dashboard: Dashboard, host: str = "127.0.0.1", port: int = 8090) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(dashboard))
