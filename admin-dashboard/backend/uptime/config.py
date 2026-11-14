"""Monitor configuration: services.toml plus environment overrides.

Precedence (highest first):
  OPSRELAY_MONITOR_INTERVAL                      -> every service's interval_seconds
  OPSRELAY_<ID>_FRONTEND_URL / _BACKEND_URL      -> per service (ID upper-cased, '-' -> '_')
  services.toml (OPSRELAY_SERVICES_CONFIG to point elsewhere)
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .models import ServiceConfig

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "services.toml"
DEFAULT_INTERVAL_SECONDS = 30

try:  # optional .env support, same convention as agents/mongodb.py
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")  # admin-dashboard/.env
    load_dotenv()
except ImportError:  # pragma: no cover
    pass


@dataclass
class MonitorSettings:
    services: list[ServiceConfig]
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    retention_days: int = 35
    incident_retention_days: int = 180
    stale_after_intervals: float = 3.0
    lease_ttl_seconds: int = 0  # 0 -> 2 * interval + 10
    extra: dict = field(default_factory=dict)

    @property
    def lease_ttl(self) -> int:
        return self.lease_ttl_seconds or (2 * self.interval_seconds + 10)


def load_settings(path: str | Path | None = None) -> MonitorSettings:
    cfg_path = Path(path or os.environ.get("OPSRELAY_SERVICES_CONFIG") or DEFAULT_CONFIG)
    raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    monitor = raw.get("monitor", {})
    interval = int(os.environ.get("OPSRELAY_MONITOR_INTERVAL") or monitor.get("interval_seconds", DEFAULT_INTERVAL_SECONDS))

    services = []
    for entry in raw.get("services", []):
        env_key = entry["id"].upper().replace("-", "_")
        merged = {
            **entry,
            "interval_seconds": interval,
            "frontend_url": os.environ.get(f"OPSRELAY_{env_key}_FRONTEND_URL", entry["frontend_url"]),
            "backend_url": os.environ.get(f"OPSRELAY_{env_key}_BACKEND_URL", entry["backend_url"]),
        }
        services.append(ServiceConfig(**merged))
    if len({s.id for s in services}) != len(services):
        raise ValueError(f"duplicate service ids in {cfg_path}")

    return MonitorSettings(
        services=services,
        interval_seconds=interval,
        retention_days=int(os.environ.get("OPSRELAY_CHECK_RETENTION_DAYS") or monitor.get("retention_days", 35)),
        incident_retention_days=int(monitor.get("incident_retention_days", 180)),
        stale_after_intervals=float(monitor.get("stale_after_intervals", 3.0)),
        lease_ttl_seconds=int(os.environ.get("OPSRELAY_LEASE_TTL") or monitor.get("lease_ttl_seconds", 0)),
    )
