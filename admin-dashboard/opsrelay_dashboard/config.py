from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "dashboard.toml"


@dataclass
class AppEntry:
    name: str
    label: str
    health_url: str
    check_interval_seconds: float = 15.0
    workflow: str | None = None


@dataclass
class DashboardConfig:
    apps: list[AppEntry]
    repairs_dir: Path
    uptime_log: Path | None
    github_repo: str | None
    refresh_seconds: int = 10


def load_config(path: Path = DEFAULT_CONFIG) -> DashboardConfig:
    with path.open("rb") as f:
        raw = tomllib.load(f)
    base = path.parent
    apps = []
    for name, entry in raw.get("apps", {}).items():
        app = AppEntry(name=name, **entry)
        if urlparse(app.health_url).scheme not in ("http", "https"):
            raise ValueError(f"{name}: health_url must be http(s), got {app.health_url!r}")
        apps.append(app)
    return DashboardConfig(
        apps=apps,
        repairs_dir=base / raw.get("repairs_dir", ".data/repairs"),
        uptime_log=base / raw["uptime_log"] if raw.get("uptime_log") else None,
        github_repo=raw.get("github_repo"),
        refresh_seconds=int(raw.get("refresh_seconds", 10)),
    )
