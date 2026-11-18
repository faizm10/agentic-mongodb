"""Failed GitHub Actions runs on a mapped repository's base branch -> one OpsRelay incident per repository.

The scheduled check reads the latest completed run of each workflow on the base branch (only the
mapped workflow when one is set) with the workspace's GitHub token (a fine-grained token needs
"Actions: read"). While any of those latest runs failed, the repository has one open incident listing
them; it resolves once each workflow's latest run passes. The agent worker diagnoses these incidents
for repositories with the agent in diagnose mode.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from . import repositories, rollup
from .storage.base import Storage, iso

FAILED = {"failure", "timed_out", "startup_failure"}


def _run(r: dict) -> dict:
    return {"id": r["id"], "workflow": r.get("name") or r.get("path") or "workflow", "sha": r.get("head_sha"),
            "attempt": r.get("run_attempt") or 1, "conclusion": r.get("conclusion"), "url": r.get("html_url"),
            "at": r.get("updated_at")}


async def sync(storage: Storage, mapping: dict, now: datetime, transport=None) -> dict:
    try:
        gh = repositories.client(storage, mapping["workspace_id"], transport)
        repo = repositories._repo_path(mapping["repo"])
        path = f"{repo}/actions/workflows/{quote(wf, safe='')}/runs" if (wf := mapping.get("workflow")) else f"{repo}/actions/runs"
        raw = await gh.get(path, branch=mapping["base_branch"], status="completed", per_page=50)
        latest: dict[int, dict] = {}
        for r in raw.get("workflow_runs", []):  # newest first
            latest.setdefault(r["workflow_id"], r)
        result = _apply(storage, mapping, [_run(r) for r in latest.values() if r.get("conclusion") in FAILED], now)
    except repositories.RepoError as exc:
        result = {"result": "skipped", "reason": str(exc)}
    storage.put_doc("repos", mapping["id"], {**mapping, "actions": {"checked_at": iso(now), **result}})
    return result


def _apply(storage: Storage, mapping: dict, runs: list[dict], now: datetime) -> dict:
    result = rollup.apply(
        storage, mapping, "actions", runs, now, label="GitHub Actions", noun="failing workflow",
        cleared="Every workflow's latest run on the base branch passed.", field="runs",
        severity=lambda items: "degraded", describe=lambda r: f"{r['workflow']} failed at {(r['sha'] or '')[:7]}")
    return {**result, "failing": result.pop("open")}
