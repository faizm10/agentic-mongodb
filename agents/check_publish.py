"""Manual end-to-end test of the PR publisher against the REAL GitHub repo (Developer 1).

WARNING: this creates a real branch ``agent/fix-<incident id>`` and a real pull request
in your repo. Close the PR and delete the branch afterwards.

It builds a mock "AI patch" from the repo itself: it reads --path at the tip of main
(real file, real sha) and appends a harmless comment line.

Run from the repo root:

    # 1. publish once
    python -m agents.check_publish

    # 2. publish twice with the same incident id: must show the SAME PR, no duplicate
    python -m agents.check_publish --repeat 2

    # 3. real lost response: GitHub creates the PR, we drop the reply, publisher must recover
    python -m agents.check_publish --simulate-lost-response

    # re-run an earlier incident (e.g. after a crash) by passing its id
    python -m agents.check_publish --incident-id smoke-1700000000

A new incident id is generated each run unless you pass --incident-id.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time

import httpx

from .github_client import GitHubClient, GitHubError
from .github_publisher import PullRequestPublisher
from .github_types import STATUS_VERIFIED, PatchFile, PublishRequest


class LostResponseTransport(httpx.HTTPTransport):
    """Sends the first ``POST .../pulls`` to GitHub for real, then pretends the reply never arrived."""

    def __init__(self) -> None:
        super().__init__()
        self.fired = False

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = super().handle_request(request)
        if not self.fired and request.method == "POST" and request.url.path.endswith("/pulls"):
            self.fired = True
            response.close()
            print(">>> simulated: GitHub created the PR, but the response was lost", file=sys.stderr)
            raise httpx.ReadTimeout("simulated lost response", request=request)
        return response


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--path", default="shopping/backend/app/__init__.py",
                        help="repo file to modify in the mock patch (must exist on main)")
    parser.add_argument("--incident-id", default=f"smoke-{int(time.time())}")
    parser.add_argument("--repeat", type=int, default=1, help="publish this many times in a row")
    parser.add_argument("--simulate-lost-response", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    transport = LostResponseTransport() if args.simulate_lost_response else None
    try:
        with GitHubClient(transport=transport) as client:
            base = client.get_branch(client.config.base_branch)
            if base is None:
                print(f"base branch {client.config.base_branch!r} not found", file=sys.stderr)
                return 1
            original = client.get_file(args.path, ref=base.sha)
            patched = original.content.rstrip("\n") + f"\n# OpsRelay publisher smoke test {args.incident_id}\n"

            request = PublishRequest(
                incident_id=args.incident_id,
                base_sha=base.sha,
                files=[PatchFile(path=args.path, content=patched)],
                root_cause="Mock root cause: this is a publisher smoke test, not a real incident.",
                tests_passed=True,
                application="shopping",
                summary="Smoke test of the PR publisher",
                test_summary="mock: 9 passed",
            )
            print(f"incident {args.incident_id}: patching {args.path} against {base.sha[:7]}")

            publisher = PullRequestPublisher(client, retry_delay=2.0)
            results = []
            for i in range(args.repeat):
                result = publisher.create_or_find_pr(request)
                results.append(result)
                print(f"\n--- run {i + 1} ---")
                print(json.dumps(result.to_dict(), indent=2))

            numbers = {r.pr_number for r in results}
            ok = all(r.status == STATUS_VERIFIED for r in results) and len(numbers) == 1
            print("\nRESULT:", "OK - one verified PR" if ok else "PROBLEM - see output above")
            if ok:
                print(f"Clean up: close PR #{results[0].pr_number} and delete branch {results[0].branch}")
            return 0 if ok else 1
    except GitHubError as exc:
        print(f"GitHub error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
