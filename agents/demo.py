"""Write sample repair results for the admin dashboard, using the Shopping fixture and a stub model.

    agents/.venv/bin/python -m agents.demo --out admin-dashboard/.data/repairs

Produces three real pipeline runs (no OpenAI call): a verified fix chosen from
three candidates over two rounds, a flaky test, and a patch that never passes.
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from .pipeline import repair_ci_failure
from .tests.support import (
    FLAKY_LOG,
    LONGER_FIX,
    WRONG_FIX,
    build_flaky_repo,
    build_shopping_regression,
    candidate,
    patch_draft,
    stub_llm,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="directory for RepairResult JSON files")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        shopping = build_shopping_regression(Path(tmp) / "verified")
        runs = {
            "demo-shopping-flaky": lambda: repair_ci_failure(
                build_flaky_repo(Path(tmp) / "flaky").request(failed_test_logs=FLAKY_LOG), stub_llm()),
            "demo-shopping-unfixed": lambda: repair_ci_failure(
                shopping.request(), stub_llm(patch=patch_draft(candidate(WRONG_FIX, summary="Multiply by (1 - percent)")))),
            "demo-shopping-verified": lambda: repair_ci_failure(shopping.request(), stub_llm(patch=[
                patch_draft(candidate(WRONG_FIX, summary="Multiply by (1 - percent)")),
                patch_draft(
                    candidate(LONGER_FIX, summary="Compute a rate, then the discount"),
                    candidate(summary="Apply discount_percent as a percentage of the subtotal"),
                ),
            ])),
        }
        for name, run in runs.items():
            result = run()
            path = args.out / f"{name}.json"
            path.write_text(result.model_dump_json(indent=2))
            print(f"{result.outcome:13} {path}  ({result.reason})")


if __name__ == "__main__":
    main()
