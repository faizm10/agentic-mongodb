"""End-to-end: Shopping discount regression -> reproduce -> diagnose -> candidates -> verified fix."""

import subprocess

from agents import repair_ci_failure

from .support import (
    CART_PATH,
    FAILING_TEST,
    FLAKY_LOG,
    LONGER_FIX,
    WRONG_FIX,
    build_flaky_repo,
    candidate,
    diagnosis_draft,
    patch_draft,
    stub_llm,
)


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

    # Same test: fails on every baseline rerun of the original commit, passes after the patch.
    assert len(result.baseline) == 2
    for run in result.baseline:
        assert run.returncode == 1
        assert run.command[-1] == FAILING_TEST
        assert f"FAILED {FAILING_TEST}" in run.output_tail
        assert "assert 190.0 == 180.0" in run.output_tail
    assert len(result.reproduce_after_patch) == 2
    assert all(run.passed and run.command[-1] == FAILING_TEST for run in result.reproduce_after_patch)
    assert result.patched_suite.passed
    assert "3 passed" in result.patched_suite.output_tail
    assert not result.flaky
    assert result.started_at <= result.finished_at

    assert _checkout_state(shopping.repo) == before, "developer checkout must be untouched"

    for label, run in [("baseline", result.baseline[0]), ("patched", result.reproduce_after_patch[0]),
                       ("suite", result.patched_suite)]:
        print(f"\n{label:9} $ {' '.join(run.command)}\n{run.output_tail}")
    print(f"patch:\n{result.patch}")


def test_diagnosis_sees_local_reproduction_and_traceback_frames(shopping):
    llm = stub_llm()

    result = repair_ci_failure(shopping.request(), llm=llm)

    _, _, prompt = llm.calls[0]
    assert "<reproduction>" in prompt and "where 190.0 = total()" in prompt
    assert "shopping/tests/test_cart.py:22" in prompt
    assert "  25|         return round(self.subtotal() - self.discount_percent, 2)" in prompt
    frames = result.diagnosis.traceback_frames
    assert [(f.path, f.line, f.in_source) for f in frames] == [("shopping/tests/test_cart.py", 22, False)]


def test_smallest_passing_candidate_wins(shopping):
    llm = stub_llm(patch=patch_draft(
        candidate(WRONG_FIX, summary="wrong"),
        candidate(LONGER_FIX, summary="longer"),
        candidate(summary="minimal"),
    ))

    result = repair_ci_failure(shopping.request(), llm=llm)

    assert result.outcome == "verified", result.reason
    assert [(a.summary, a.passed) for a in result.attempts] == [("wrong", False), ("longer", True), ("minimal", True)]
    assert [a.changed_lines for a in result.attempts] == [2, 4, 3]
    assert "+        discount = self.subtotal() * self.discount_percent / 100" in result.patch
    assert "rate" not in result.patch
    assert "chose candidate 1.3 (3 changed lines) of 3 tried" in result.reason
    assert llm.count("_PatchDraft") == 1


def test_failed_candidate_is_fed_back_for_a_second_round(shopping):
    llm = stub_llm(patch=[patch_draft(candidate(WRONG_FIX)), patch_draft(candidate())])

    result = repair_ci_failure(shopping.request(), llm=llm)

    assert result.outcome == "verified", result.reason
    assert [(a.round, a.passed) for a in result.attempts] == [(1, False), (2, True)]
    assert llm.count("_PatchDraft") == 2
    second_prompt = [user for name, _, user in llm.calls if name == "_PatchDraft"][1]
    assert "<previous_attempt>" in second_prompt
    assert "the originally failing tests still fail after the patch" in second_prompt
    assert "assert -1800.0 == 180.0" in second_prompt  # real output from the failed candidate


def test_patch_that_still_fails_needs_review_after_all_rounds(shopping):
    llm = stub_llm(patch=patch_draft(candidate(WRONG_FIX)))

    result = repair_ci_failure(shopping.request(), llm=llm)

    assert result.outcome == "needs_review"
    assert result.reason.startswith("no candidate passed after 2 round(s)")
    assert "the originally failing tests still fail after the patch" in result.reason
    assert result.patch is None
    assert result.baseline[0].returncode == 1
    first = result.attempts[0]
    assert not first.passed and not first.reproduce_runs[-1].passed
    assert first.patch and first.changed_files == [CART_PATH]
    # Round 2 proposed the same diff again, which is rejected as a duplicate, not re-run.
    assert result.attempts[1].reason == "patch rejected: duplicate of an earlier candidate"


def test_patch_targeting_tests_is_rejected_before_running_anything(shopping):
    request = shopping.request(source_files={
        CART_PATH: shopping.request().source_files[CART_PATH],
        "shopping/tests/test_cart.py": "assert cart.total() == 180.0\n",
    })
    sneaky = patch_draft(candidate(path="shopping/tests/test_cart.py", find="180.0", replace="190.0"))
    llm = stub_llm(patch=sneaky)

    result = repair_ci_failure(request, llm=llm)

    assert result.outcome == "needs_review"
    assert "not supplied as editable source" in result.reason
    assert result.attempts[0].reproduce_runs == []
    assert llm.count("_PatchDraft") == 1  # nothing testable to learn from, so no second round


def test_flaky_failure_needs_review_without_calling_the_model(tmp_path):
    flaky = build_flaky_repo(tmp_path)
    llm = stub_llm()

    result = repair_ci_failure(flaky.request(failed_test_logs=FLAKY_LOG), llm=llm)

    assert result.outcome == "needs_review"
    assert result.flaky
    assert result.reason.startswith("flaky:")
    assert [run.passed for run in result.baseline] == [False, True]
    assert llm.calls == []


def test_low_confidence_diagnosis_stops_before_patching(shopping):
    llm = stub_llm(diagnosis=diagnosis_draft(confidence=0.3))

    result = repair_ci_failure(shopping.request(), llm=llm)

    assert result.outcome == "needs_review"
    assert "confidence 0.30" in result.reason
    assert [name for name, _, _ in llm.calls] == ["_DiagnosisDraft"]


def test_log_without_failing_tests_needs_review_without_calling_the_model(shopping):
    llm = stub_llm()

    result = repair_ci_failure(shopping.request(failed_test_logs="Process completed with exit code 1."), llm=llm)

    assert result.outcome == "needs_review"
    assert "no failing pytest node ids" in result.reason
    assert llm.calls == []
