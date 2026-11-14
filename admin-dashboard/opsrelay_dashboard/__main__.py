"""Run the dashboard: ``python -m opsrelay_dashboard [--demo] [--port 8090]`` from admin-dashboard/."""

from __future__ import annotations

import argparse
import os
import random
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .ci import GitHubActions
from .config import DEFAULT_CONFIG, load_config
from .repairs import RepairStore
from .server import Dashboard, serve
from .uptime import Target, UptimeMonitor


def _demo_health_server(fail_every: int) -> ThreadingHTTPServer:
    """Local stand-in health endpoint. fail_every=0 never fails; N fails roughly 1 in N checks."""

    class Health(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            down = fail_every and random.randrange(fail_every) == 0
            body = b'{"status":"degraded"}' if down else b'{"status":"ok"}'
            self.send_response(503 if down else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="OpsRelay admin dashboard")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--demo", action="store_true",
                        help="simulate health endpoints (Messaging flaps) instead of the configured URLs")
    args = parser.parse_args()

    config = load_config(args.config)
    uptime_log = config.uptime_log
    if args.demo:
        for i, app in enumerate(config.apps):
            fake = _demo_health_server(fail_every=0 if i == 0 else 4)
            app.health_url = f"http://127.0.0.1:{fake.server_port}/health"
            app.check_interval_seconds = 3
        uptime_log = None  # keep simulated samples out of the real history

    monitor = UptimeMonitor(
        [Target(a.name, a.health_url, a.check_interval_seconds) for a in config.apps], log_path=uptime_log,
    )
    ci = GitHubActions(config.github_repo, token=os.environ.get("GITHUB_TOKEN")) if config.github_repo else None
    dashboard = Dashboard(config, monitor, RepairStore(config.repairs_dir), ci, demo=args.demo)

    monitor.start()
    server = serve(dashboard, args.host, args.port)
    print(f"OpsRelay dashboard on http://{args.host}:{server.server_port}" + ("  (demo health endpoints)" if args.demo else ""))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop()
        server.server_close()


if __name__ == "__main__":
    main()
