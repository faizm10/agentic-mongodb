"""Mo's orchestrator calls Repairer().repair(failed_run, similar) and expects his PatchResult dict."""

import pytest

from agents import Repairer

from .support import CART_PATH, CI_LOG, stub_llm


def failed_run(shopping, **overrides):
    # Same shape as Mo's FailedRun (agents/fakes.py SAMPLE_RUN on feature/mongodb-workflow-recovery).
    run = {"repo": "faizm10/agentic-mongodb", "app": "shopping", "run_id": 9_000_001, "run_attempt": 1,
           "commit_sha": shopping.bad_sha, "failed_logs": CI_LOG,
           "failed_tests": ["shopping/tests/test_cart.py::test_percentage_discount_applied_to_total"]}
    run.update(overrides)
    return run


SIMILAR = [{"memory_id": "mem-1", "score": 0.91, "summary": "Discount applied as flat amount",
            "root_cause": "percent treated as currency"}]


def test_repair_returns_mo_patch_result(shopping):
    result = Repairer(repo_path=str(shopping.repo), llm=stub_llm()).repair(failed_run(shopping), SIMILAR)

    assert result["tests_passed"] is True and result["outcome"] == "verified"
    assert result["changed_files"] == [CART_PATH]
    assert set(result["files"]) == {CART_PATH}
    assert "discount = self.subtotal() * self.discount_percent / 100" in result["files"][CART_PATH]
    assert "+        discount = self.subtotal() * self.discount_percent / 100" in result["diff"]
    assert "3 passed" in result["test_output"]
    assert result["summary"] == "Apply discount_percent as a percentage of the subtotal."
    assert result["root_cause"].startswith("Cart.total subtracts discount_percent")


def test_request_is_built_from_the_repo_at_the_failed_commit(shopping):
    request = Repairer(repo_path=str(shopping.repo)).request_for(failed_run(shopping), SIMILAR)

    assert set(request.source_files) == {CART_PATH, "shopping/src/shopping/__init__.py"}
    assert "return round(self.subtotal() - self.discount_percent, 2)" in request.source_files[CART_PATH]
    assert "+        return round(self.subtotal() - self.discount_percent, 2)" in request.recent_diff
    assert request.similar_incidents[0].id == "mem-1" and request.similar_incidents[0].similarity == 0.91


def test_unverified_repair_reports_tests_not_passed(shopping):
    from .support import WRONG_FIX, candidate, patch_draft

    llm = stub_llm(patch=patch_draft(candidate(WRONG_FIX)))
    result = Repairer(repo_path=str(shopping.repo), llm=llm).repair(failed_run(shopping), [])

    assert result["tests_passed"] is False and result["outcome"] == "needs_review"
    assert result["files"] == {} and result["diff"] == ""


def test_rejects_option_like_commit_sha(shopping):
    with pytest.raises(ValueError):
        Repairer(repo_path=str(shopping.repo)).request_for(failed_run(shopping, commit_sha="--output=/tmp/x"), [])
