"""Failure monitor (Developer 1) - polls GitHub Actions and reports new CI failures.

Role in the project
-------------------
This is the agent's "smoke detector". Every ~20 seconds it asks GitHub for failed
workflow runs on the main branch, keeps only the two apps we care about, and turns
each new failure into a ``FailureEvent`` for the orchestrator.

The ``seen`` set lives in memory only, so it stops us from reporting the same
incident twice *while running*. Durable de-duplication across restarts is
Developer 3's job (MongoDB).

Run it by hand from the repo root:
    python -m agents.monitor --once
    python -m agents.monitor --interval 20
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import threading
from typing import Callable, Optional

from .github_client import (
    GitHubAuthError,
    GitHubClient,
    GitHubError,
    GitHubRateLimited,
)
from .github_types import (
    AGENT_BRANCH_PREFIX,
    FailureEvent,
    application_for_workflow_path,
    make_incident_id,
)

log = logging.getLogger("agents.monitor")

DEFAULT_INTERVAL_SECONDS = 20.0


class FailureMonitor:
    def __init__(
        self,
        client: Optional[GitHubClient] = None,
        *,
        branch: Optional[str] = None,
        per_page: int = 30,
    ):
        self.client = client or GitHubClient()
        self.branch = branch or self.client.config.base_branch
        self.per_page = per_page
        self._seen: set[str] = set()

    # -- filtering ---------------------------------------------------------- #

    def _event_from_run(self, run: dict) -> Optional[FailureEvent]:
        """Turn a raw run into a FailureEvent, or None if we should ignore it."""
        head_branch = run.get("head_branch") or ""
        if head_branch.startswith(AGENT_BRANCH_PREFIX):
            return None                      # the agent's own fix branches
        if head_branch != self.branch:
            return None                      # only watch the main branch
        if run.get("status") != "completed" or run.get("conclusion") != "failure":
            return None
        path = run.get("path") or ""
        application = application_for_workflow_path(path)
        if application is None:
            return None                      # unrelated workflow
        attempt = int(run.get("run_attempt") or 1)
        run_id = int(run["id"])
        return FailureEvent(
            incident_id=make_incident_id(run_id, attempt),
            application=application,
            workflow_run_id=run_id,
            run_attempt=attempt,
            commit_sha=run["head_sha"],
            workflow_name=run.get("name", ""),
            workflow_file=path.split("@", 1)[0].rsplit("/", 1)[-1],
            branch=head_branch,
            workflow_url=run.get("html_url", ""),
            created_at=run.get("created_at"),
        )

    # -- public API --------------------------------------------------------- #

    def detect_failures(self) -> list[FailureEvent]:
        """One poll. Returns only failures not reported before (oldest first)."""
        runs = self.client.list_workflow_runs(
            branch=self.branch, status="failure", per_page=self.per_page
        )
        events = []
        for run in runs:
            event = self._event_from_run(run)
            if event and event.incident_id not in self._seen:
                events.append(event)
        events.sort(key=lambda e: e.created_at or "")
        for event in events:
            self._seen.add(event.incident_id)
        return events

    def forget(self, incident_id: str) -> None:
        """Make an incident reportable again (e.g. the orchestrator failed to take it)."""
        self._seen.discard(incident_id)

    def baseline(self) -> int:
        """Mark everything that has already failed as seen; returns how many."""
        return len(self.detect_failures())

    def run_forever(
        self,
        on_failure: Callable[[FailureEvent], None],
        *,
        interval: float = DEFAULT_INTERVAL_SECONDS,
        stop: Optional[threading.Event] = None,
    ) -> None:
        """Poll until ``stop`` is set. Survives transient GitHub errors."""
        stop = stop or threading.Event()
        while not stop.is_set():
            wait = interval
            try:
                events = self.detect_failures()
            except GitHubAuthError:
                raise                         # a bad token will not fix itself
            except GitHubRateLimited as exc:
                log.warning("Rate limited, backing off: %s", exc)
                wait = max(interval, 60.0)
            except GitHubError as exc:
                log.warning("Poll failed (will retry): %s", exc)
            else:
                for event in events:
                    try:
                        on_failure(event)
                    except Exception:
                        log.exception("Handler failed for %s; will re-report", event.incident_id)
                        self.forget(event.incident_id)
            stop.wait(wait)


# --------------------------------------------------------------------------- #
# Module-level interface for the orchestrator:  detect_failures() -> list[FailureEvent]
# --------------------------------------------------------------------------- #

_default_monitor: Optional[FailureMonitor] = None


def detect_failures() -> list[FailureEvent]:
    global _default_monitor
    if _default_monitor is None:
        _default_monitor = FailureMonitor()
    return _default_monitor.detect_failures()


# --------------------------------------------------------------------------- #
# Manual runner
# --------------------------------------------------------------------------- #

def _main() -> None:
    parser = argparse.ArgumentParser(description="Poll GitHub Actions for failed CI runs.")
    parser.add_argument("--once", action="store_true", help="poll once, print, and exit")
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.getenv("GITHUB_POLL_INTERVAL", DEFAULT_INTERVAL_SECONDS)),
        help="seconds between polls (default 20)",
    )
    parser.add_argument(
        "--ignore-existing",
        action="store_true",
        help="do not report failures that already exist when the monitor starts",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    monitor = FailureMonitor()
    if args.ignore_existing:
        log.info("Ignoring %d existing failure(s)", monitor.baseline())

    def show(event: FailureEvent) -> None:
        print(json.dumps(event.to_dict(), indent=2), flush=True)

    if args.once:
        events = monitor.detect_failures()
        for event in events:
            show(event)
        print(f"-- {len(events)} new failure(s) --")
        return

    log.info("Watching %s/%s on '%s' every %ss (Ctrl+C to stop)",
             monitor.client.config.owner, monitor.client.config.repo,
             monitor.branch, args.interval)
    try:
        monitor.run_forever(show, interval=args.interval)
    except KeyboardInterrupt:
        log.info("Stopped.")


if __name__ == "__main__":
    _main()
