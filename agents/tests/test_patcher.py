import pytest

from agents import load_app_config, validate_patch, verify_patch
from agents.patcher import build_diff, path_violation

from .support import BUGGY_LINE, CART_BUGGY, CART_PATH, FIXED_LINES, _Edit

SHOPPING = load_app_config("shopping")


def one_line_diff(path: str, old: str = "x = 1", new: str = "x = 2") -> str:
    return f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{old}\n+{new}\n"


def test_build_diff_produces_minimal_valid_patch():
    diff = build_diff({CART_PATH: CART_BUGGY}, [_Edit(path=CART_PATH, find=BUGGY_LINE, replace=FIXED_LINES)])

    result = validate_patch(diff, SHOPPING)

    assert result.accepted, result.rejection_reason
    assert result.changed_files == [CART_PATH]
    body = [ln for ln in diff.splitlines() if not ln.startswith(("---", "+++"))]
    assert sum(ln.startswith("-") for ln in body) == 1
    assert sum(ln.startswith("+") for ln in body) == 2


@pytest.mark.parametrize(
    "path, reason",
    [
        ("shopping/tests/test_cart.py", "tests are outside"),
        ("shopping/src/shopping/test_cart.py", "tests are outside"),
        ("shopping/src/conftest.py", "tests are outside"),
        (".github/workflows/shopping.yml", "CI configuration"),
        ("shopping/src/.github/workflows/x.yml", "CI configuration"),
        ("shopping/src/requirements.txt", "dependency and build manifests"),
        ("shopping/src/pyproject.toml", "dependency and build manifests"),
        ("shopping/src/.env", "secrets and credentials"),
        ("shopping/src/shopping/api_token.py", "secrets and credentials"),
        ("shopping/src/shopping/../../../etc/passwd", "escapes the repo"),
        ("/etc/passwd", "not a repo-relative path"),
        ("shopping/app.py", "outside shopping's allowed source dirs"),
        ("message-platform/src/inbox.py", "outside shopping's allowed source dirs"),
        ("shopping/srcevil/shopping/cart.py", "outside shopping's allowed source dirs"),
    ],
)
def test_rejects_paths_outside_repair_scope(path, reason):
    result = validate_patch(one_line_diff(path), SHOPPING)

    assert not result.accepted
    assert reason in result.rejection_reason
    assert path_violation(path, SHOPPING) is not None


@pytest.mark.parametrize(
    "diff, reason",
    [
        ("", "patch is empty"),
        (f"--- /dev/null\n+++ b/{CART_PATH}\n@@ -0,0 +1 @@\n+x\n", "creating or deleting"),
        (f"--- a/{CART_PATH}\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n", "creating or deleting"),
        (f"diff --git a/{CART_PATH} b/{CART_PATH}\nnew file mode 100644\n", "only in-place edits"),
        (f"diff --git a/{CART_PATH} b/{CART_PATH}\nold mode 100644\nnew mode 100755\n", "only in-place edits"),
        (f"diff --git a/x b/{CART_PATH}\nrename from x\nrename to {CART_PATH}\n", "only in-place edits"),
        (f"diff --git a/{CART_PATH} b/{CART_PATH}\nGIT binary patch\n", "only in-place edits"),
        (f"--- a/{CART_PATH}\n+++ b/shopping/src/other.py\n@@ -1 +1 @@\n-a\n+b\n", "path mismatch"),
        (f"--- a/{CART_PATH}\n+++ b/{CART_PATH}\n@@ -1,3 +1,3 @@\n-a\n+b\n", "hunk is truncated"),
        (f"--- a/{CART_PATH}\n+++ b/{CART_PATH}\n@@ -1 +1 @@\n-a\n+b\n-c\n", "unexpected line"),
        ("@@ -1 +1 @@\n-a\n+b\n", "before any file header"),
        ("rm -rf /\n", "unexpected line"),
    ],
)
def test_rejects_malformed_or_structural_patches(diff, reason):
    result = validate_patch(diff, SHOPPING)

    assert not result.accepted
    assert reason in result.rejection_reason


def test_rejects_oversized_patch():
    body = "".join(f"-a{i}\n+b{i}\n" for i in range(31))
    diff = f"--- a/{CART_PATH}\n+++ b/{CART_PATH}\n@@ -1,31 +1,31 @@\n{body}"

    result = validate_patch(diff, SHOPPING)

    assert not result.accepted
    assert "changes 62 lines; limit is 60" in result.rejection_reason


def test_removed_line_starting_with_dashes_is_not_a_header():
    diff = f"--- a/{CART_PATH}\n+++ b/{CART_PATH}\n@@ -1 +1 @@\n--- a/.env\n+x\n"

    result = validate_patch(diff, SHOPPING)

    assert result.accepted, result.rejection_reason
    assert result.changed_files == [CART_PATH]


@pytest.mark.parametrize(
    "edit, message",
    [
        (_Edit(path=CART_PATH, find="does not exist", replace="x"), "found 0"),
        (_Edit(path=CART_PATH, find="self", replace="this"), "exactly once"),
        (_Edit(path="shopping/src/shopping/other.py", find="a", replace="b"), "not supplied"),
    ],
)
def test_build_diff_rejects_ambiguous_or_unknown_edits(edit, message):
    with pytest.raises(ValueError, match=message):
        build_diff({CART_PATH: CART_BUGGY}, [edit])


def test_verifier_revalidates_scope_before_touching_the_repo(shopping):
    diff = one_line_diff(".github/workflows/shopping.yml", "on: [push]", "on: []")

    result = verify_patch("shopping", str(shopping.repo), shopping.bad_sha, diff, ["tests/test_cart.py"])

    assert result.outcome == "needs_review"
    assert "CI configuration" in result.reason
    assert result.baseline is None
