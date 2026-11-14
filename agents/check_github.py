"""Manual smoke tests against the REAL GitHub API (Developer 1).

Run from the repo root, one step at a time:

    python -m agents.check_github auth             # 1. token + repo + permissions
    python -m agents.check_github runs             # 2. recent runs and how we classify them
    python -m agents.check_github detect           # 3. what the monitor would report
    python -m agents.check_github context <RUN_ID> # 4. full FailureContext for Developer 2
    python -m agents.check_github file <PATH>      # 5. read one file from the repo
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .github_client import GitHubClient, GitHubError
from .github_types import application_for_workflow_path
from .monitor import FailureMonitor


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = parser.add_subparsers(dest="step", required=True)
    sub.add_parser("auth")
    runs_p = sub.add_parser("runs")
    runs_p.add_argument("--all-branches", action="store_true")
    sub.add_parser("detect")
    ctx_p = sub.add_parser("context")
    ctx_p.add_argument("run_id", type=int)
    ctx_p.add_argument("--logs-only", action="store_true", help="print only the cleaned logs")
    file_p = sub.add_parser("file")
    file_p.add_argument("path")
    file_p.add_argument("--ref")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        with GitHubClient() as client:
            if args.step == "auth":
                _print(client.verify_auth())
            elif args.step == "runs":
                branch = None if args.all_branches else client.config.base_branch
                for run in client.list_workflow_runs(branch=branch, per_page=15):
                    app = application_for_workflow_path(run.get("path")) or "-- ignored --"
                    print(
                        f"run {run['id']:>12}  attempt {run.get('run_attempt', 1)}  "
                        f"{run.get('head_branch', ''):<18} {str(run.get('conclusion')):<10} "
                        f"{run.get('path', ''):<40} -> {app}"
                    )
            elif args.step == "detect":
                events = FailureMonitor(client).detect_failures()
                _print([e.to_dict() for e in events])
                print(f"-- {len(events)} failure(s) detected --")
            elif args.step == "context":
                ctx = client.get_failure_context(args.run_id)
                if args.logs_only:
                    print(ctx.logs)
                else:
                    _print(ctx.to_dict())
            elif args.step == "file":
                f = client.get_file(args.path, args.ref)
                print(f"# blob sha: {f.sha}  ref: {f.ref}\n{f.content}")
    except GitHubError as exc:
        print(f"GitHub error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
