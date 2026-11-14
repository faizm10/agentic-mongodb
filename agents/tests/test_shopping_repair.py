"""End-to-end: Shopping discount regression -> diagnosis -> patch -> verified fix."""

import subprocess

from agents import repair_ci_failure

from .support import CART_PATH, FAILING_TEST, patch_draft, stub_llm


def _checkout_state(repo):
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True).stdout
    return run("rev-parse", "HEAD"), run("status", "--porcelain"), run("worktree", "list")


def test_shopping_discount_failure_reproduced_then_fixed(shopping):
    before = _checkout_state(shopping.repo)

    result = repair_ci_failure(shopping.request(), llm=stub_llm())

    assert result.outcome == "verified", result.reason
    assert result.changed_files == [CART_PATH]
    assert "-        return round(self.subtotal() - self.discount_percent, 2)" in result.patch
    assert "+        discount = self.subtotal() * self.discount_percent / 100" in result.patch

    # Same test: fails on the original commit, passes after the patch.
    assert result.baseline.returncode == 1
    assert f"FAILED {FAILING_TEST}" in result.baseline.output_tail
    assert "assert 190.0 == 180.0" in result.baseline.output_tail
    assert result.baseline.command[-1] == FAILING_TEST
    assert result.reproduce_after_patch.passed
    assert result.reproduce_after_patch.command[-1] == FAILING_TEST
    assert result.patched_suite.passed
    assert "3 passed" in result.patched_suite.output_tail

    assert _checkout_state(shopping.repo) == before, "developer checkout must be untouched"

    print(f"\nbaseline  $ {' '.join(result.baseline.command)}\n{result.baseline.output_tail}")
    print(f"patched   $ {' '.join(result.reproduce_after_patch.command)}\n{result.reproduce_after_patch.output_tail}")
    print(f"suite     $ {' '.join(result.patched_suite.command)}\n{result.patched_suite.output_tail}")
    print(f"patch:\n{result.patch}")


def test_patch_that_still_fails_needs_review(shopping):
    wrong = "        return round(self.subtotal() * (1 - self.discount_percent), 2)\n"

    result = repair_ci_failure(shopping.request(), llm=stub_llm(patch=patch_draft(replace=wrong)))

    assert result.outcome == "needs_review"
    assert result.reason == "the originally failing tests still fail after the patch"
    assert result.baseline.returncode == 1
    assert not result.reproduce_after_patch.passed
    assert not result.patched_suite.passed
    assert result.patch and result.changed_files == [CART_PATH]


def test_patch_targeting_tests_is_rejected_before_running_anything(shopping):
    request = shopping.request(source_files={
        CART_PATH: shopping.request().source_files[CART_PATH],
        "shopping/tests/test_cart.py": "assert cart.total() == 180.0\n",
    })
    sneaky = patch_draft(
        path="shopping/tests/test_cart.py", find="180.0", replace="190.0",
    )

    result = repair_ci_failure(request, llm=stub_llm(patch=sneaky))

    assert result.outcome == "needs_review"
    assert result.reason.startswith("patch rejected:")
    assert "not supplied as editable source" in result.reason
    assert result.baseline is None


def test_diagnosis_needing_review_stops_before_patching(shopping):
    llm = stub_llm()
    result = repair_ci_failure(shopping.request(failed_test_logs="Process completed with exit code 1."), llm=llm)

    assert result.outcome == "needs_review"
    assert "no failing pytest node ids" in result.reason
    assert [name for name, _, _ in llm.calls] == ["_DiagnosisDraft"]
