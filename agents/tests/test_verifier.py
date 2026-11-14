from agents import load_app_config, verify_patch
from agents.patcher import build_diff
from agents.verifier import _sandbox_env

from .support import BUGGY_LINE, CART_BUGGY, CART_PATH, FAILING_TEST, FIXED_LINES, _Edit


def fix_diff() -> str:
    return build_diff({CART_PATH: CART_BUGGY}, [_Edit(path=CART_PATH, find=BUGGY_LINE, replace=FIXED_LINES)])


def test_needs_review_when_baseline_does_not_reproduce(shopping):
    result = verify_patch("shopping", str(shopping.repo), shopping.good_sha, fix_diff(), [FAILING_TEST])

    assert result.outcome == "needs_review"
    assert result.reason == "baseline did not reproduce the CI failure"
    assert result.baseline.passed
    assert result.patched_suite is None


def test_patch_that_does_not_apply_needs_review(shopping):
    stale = fix_diff().replace("discount_percent, 2)", "discount_pct, 2)", 1)

    result = verify_patch("shopping", str(shopping.repo), shopping.bad_sha, stale, [FAILING_TEST])

    assert result.outcome == "needs_review"
    assert result.reason.startswith("patch does not apply")


def test_patched_run_is_killed_on_timeout(shopping):
    app = load_app_config("shopping").model_copy(update={"timeout_seconds": 3})
    hang = "        import time; time.sleep(60)\n" + FIXED_LINES
    diff = build_diff({CART_PATH: CART_BUGGY}, [_Edit(path=CART_PATH, find=BUGGY_LINE, replace=hang)])

    result = verify_patch(app, str(shopping.repo), shopping.bad_sha, diff, [FAILING_TEST])

    assert result.outcome == "needs_review"
    assert result.reproduce_after_patch.timed_out
    assert result.reproduce_after_patch.returncode is None
    assert result.reproduce_after_patch.duration_seconds < 10


def test_unsafe_test_ids_are_refused(shopping):
    result = verify_patch(
        "shopping", str(shopping.repo), shopping.bad_sha, fix_diff(),
        ["-p", "tests/../../etc/x.py", "conftest.py"],
    )

    assert result.outcome == "needs_review"
    assert "refusing unsafe or out-of-scope test ids" in result.reason
    assert result.baseline is None


def test_unknown_commit_needs_review(shopping):
    result = verify_patch("shopping", str(shopping.repo), "deadbeef", fix_diff(), [FAILING_TEST])

    assert result.outcome == "needs_review"
    assert result.reason.startswith("could not export commit deadbeef")


def test_sandbox_env_drops_credentials(tmp_path, monkeypatch):
    for name in ("OPENAI_API_KEY", "MONGODB_URI", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(name, "secret")

    env = _sandbox_env(tmp_path)

    assert set(env) == {
        "PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "PYTHONDONTWRITEBYTECODE",
        "PYTHONHASHSEED", "PYTHONNOUSERSITE", "NO_COLOR",
    }
    assert env["HOME"].startswith(str(tmp_path))
