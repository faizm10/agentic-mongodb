"""Open Dependabot alerts on a mapped repository -> one OpsRelay incident per repository.

The scheduled check syncs every mapped repository with its workspace's GitHub token (a fine-grained
token needs "Dependabot alerts: read"). While any alert is open the repository has one open incident
listing them: "down" if any is critical or high, "degraded" otherwise. It resolves once every alert
is fixed or dismissed. Each mapping keeps the last sync result, so a token without access shows up
on the Repositories page instead of failing silently.
"""

from __future__ import annotations

from datetime import datetime

from . import repositories, rollup
from .storage.base import Storage, iso

SEVERE = {"critical", "high"}


def _alert(a: dict) -> dict:
    advisory = a.get("security_advisory") or {}
    package = (a.get("dependency") or {}).get("package") or {}
    patched = ((a.get("security_vulnerability") or {}).get("first_patched_version") or {}).get("identifier")
    return {"id": a.get("number"), "number": a.get("number"), "package": package.get("name"), "ecosystem": package.get("ecosystem"),
            "manifest": (a.get("dependency") or {}).get("manifest_path"), "severity": advisory.get("severity") or "unknown",
            "summary": advisory.get("summary") or "", "patched_version": patched, "url": a.get("html_url")}


async def sync(storage: Storage, mapping: dict, now: datetime, transport=None) -> dict:
    try:
        gh = repositories.client(storage, mapping["workspace_id"], transport)
        raw = await gh.get(f"{repositories._repo_path(mapping['repo'])}/dependabot/alerts", state="open", per_page=100)
        result = _apply(storage, mapping, [_alert(a) for a in raw], now)
    except repositories.RepoError as exc:
        result = {"result": "skipped", "reason": str(exc)}
    storage.put_doc("repos", mapping["id"], {**mapping, "dependabot": {"checked_at": iso(now), **result}})
    return result


def _apply(storage: Storage, mapping: dict, alerts: list[dict], now: datetime) -> dict:
    result = rollup.apply(
        storage, mapping, "dependabot", alerts, now, label="Dependabot", noun="open Dependabot alert",
        cleared="Every Dependabot alert is fixed or dismissed.", field="alerts",
        severity=lambda items: "down" if any(a["severity"] in SEVERE for a in items) else "degraded",
        describe=lambda a: f"{a['package']} ({a['severity']})")
    return {**result, "open_alerts": result.pop("open")}
