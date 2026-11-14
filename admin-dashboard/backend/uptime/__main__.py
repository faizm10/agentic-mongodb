"""CLI.

    python -m uptime serve     API on :8300 with the monitoring worker embedded (simplest)
    python -m uptime worker    monitoring worker only (runs without any browser or API)
    python -m uptime api       read API only (pair with a separate worker)
    python -m uptime check     run one check cycle now and print the results
    python -m uptime status    print storage, worker and current statuses
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

from .config import load_settings
from .storage import open_storage
from .worker import UptimeMonitor


def _monitor(settings, storage) -> UptimeMonitor:
    return UptimeMonitor(storage, settings.services, interval_seconds=settings.interval_seconds,
                         lease_ttl_seconds=settings.lease_ttl, retention_days=settings.retention_days,
                         incident_retention_days=settings.incident_retention_days)


async def _run_worker() -> None:
    settings = load_settings()
    storage = open_storage(retention_days=settings.retention_days, incident_retention_days=settings.incident_retention_days)
    monitor = _monitor(settings, storage)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await monitor.run_forever(stop)


def serve_app():
    """API with the monitoring worker embedded (``python -m uptime serve``)."""
    from .api import create_app

    return create_app(embed_worker=True)


def api_app():
    """Read API only (``python -m uptime api``)."""
    from .api import create_app

    return create_app(embed_worker=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m uptime", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("serve", "api"):
        p = sub.add_parser(name)
        p.add_argument("--host", default=os.environ.get("OPSRELAY_API_HOST", "127.0.0.1"))
        p.add_argument("--port", type=int, default=int(os.environ.get("OPSRELAY_API_PORT", "8300")))
        p.add_argument("--reload", action="store_true", help="restart when uptime/ code changes (development)")
    sub.add_parser("worker")
    sub.add_parser("check")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.environ.get("OPSRELAY_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per probe is too noisy every 30 s

    if args.cmd in ("serve", "api"):
        import uvicorn

        # An import string lets uvicorn's reloader re-create the app after a code change.
        # Only uptime/ is watched: demo bad deploys edit the apps' source, not this API.
        uvicorn.run(f"uptime.__main__:{args.cmd}_app", factory=True, host=args.host, port=args.port,
                    log_level="warning", reload=args.reload,
                    reload_dirs=[str(Path(__file__).resolve().parent)] if args.reload else None)
        return 0
    if args.cmd == "worker":
        asyncio.run(_run_worker())
        return 0

    settings = load_settings()
    storage = open_storage(retention_days=settings.retention_days, incident_retention_days=settings.incident_retention_days)
    if args.cmd == "check":
        monitor = _monitor(settings, storage)
        monitor.register_services()
        result = asyncio.run(monitor.run_cycle(scheduled=False))
        print(json.dumps(result, default=str, indent=2))
        return 0 if result and all(s == "operational" for s in result["statuses"].values()) else 1
    worker = storage.get_worker()
    print(json.dumps({
        "storage": storage.describe(),
        "worker": worker.model_dump(mode="json") if worker else None,
        "services": {s.id: (st.model_dump(mode="json") if (st := storage.get_state(s.id)) else None) for s in settings.services},
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
