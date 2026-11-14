"""Read repair results (``RepairResult.model_dump_json()`` files) for display.

Mo's orchestrator owns persistence. Until results live in MongoDB, the dashboard
reads JSON files from a directory; swap ``RepairStore`` for a MongoDB-backed one
with the same two methods.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
MAX_RESULTS = 50


class RepairStore:
    def __init__(self, directory: Path):
        self.directory = directory

    def list(self) -> list[dict]:
        results = []
        for path in self._files()[:MAX_RESULTS]:
            data = self._read(path)
            if data is not None:
                results.append(_summary(path.stem, data))
        return results

    def get(self, repair_id: str) -> dict | None:
        if not _ID_RE.match(repair_id) or repair_id.startswith("."):
            return None
        path = self.directory / f"{repair_id}.json"
        data = self._read(path) if path.is_file() else None
        if data is None:
            return None
        return {**_summary(repair_id, data), "detail": _detail(data)}

    def _files(self) -> list[Path]:
        if not self.directory.is_dir():
            return []
        files = [p for p in self.directory.glob("*.json") if _ID_RE.match(p.stem)]
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)

    @staticmethod
    def _read(path: Path) -> dict | None:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and "outcome" in data else None


def _summary(repair_id: str, data: dict) -> dict:
    diagnosis = data.get("diagnosis") or {}
    attempts = data.get("attempts") or []
    return {
        "id": repair_id,
        "app": data.get("app"),
        "commit_sha": (data.get("commit_sha") or "")[:7],
        "outcome": data.get("outcome"),
        "reason": data.get("reason"),
        "flaky": bool(data.get("flaky")),
        "finished_at": data.get("finished_at"),
        "started_at": data.get("started_at"),
        "likely_cause": diagnosis.get("likely_cause"),
        "confidence": diagnosis.get("confidence"),
        "changed_files": data.get("changed_files") or [],
        "candidates_tried": len(attempts),
        "rounds": len({a.get("round") for a in attempts}),
    }


def _run(run: dict | None) -> dict | None:
    if not run:
        return None
    return {
        "command": " ".join(run.get("command") or []),
        "passed": run.get("passed"),
        "timed_out": run.get("timed_out"),
        "duration_seconds": run.get("duration_seconds"),
        "output_tail": (run.get("output_tail") or "")[-3000:],
    }


def _detail(data: dict) -> dict:
    diagnosis = data.get("diagnosis") or {}
    return {
        "explanation": diagnosis.get("explanation"),
        "evidence": diagnosis.get("evidence") or [],
        "suspect_files": diagnosis.get("suspect_files") or [],
        "failing_tests": diagnosis.get("failing_tests") or [],
        "traceback_frames": diagnosis.get("traceback_frames") or [],
        "review_reason": diagnosis.get("review_reason"),
        "patch": data.get("patch"),
        "baseline": [_run(r) for r in data.get("baseline") or []],
        "after": [_run(r) for r in data.get("reproduce_after_patch") or []],
        "suite": _run(data.get("patched_suite")),
        "attempts": [
            {
                "round": a.get("round"), "index": a.get("index"), "summary": a.get("summary"),
                "passed": a.get("passed"), "reason": a.get("reason"), "changed_lines": a.get("changed_lines"),
            }
            for a in data.get("attempts") or []
        ],
    }
