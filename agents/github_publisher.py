"""Publish a verified patch as a branch + pull request - safely, and at most once.

Role in the project
-------------------
Developer 2 hands us a patch whose tests passed. We turn it into a real GitHub pull
request for a human to review (we never merge). Developer 3's workflow engine can call
``create_or_find_pr`` again after ANY crash or timeout: the function first asks GitHub
what already exists, only does the missing work, then re-reads GitHub to prove it.

    from agents.github_publisher import create_or_find_pr
    result = create_or_find_pr(patch_dict_or_PublishRequest)
    result.status      # "verified" | "failed" | "unknown"
    result.to_dict()   # {"status", "branch", "pr_number", "pr_url", "commit_sha", ...}

Status meaning
--------------
verified  the PR exists on GitHub, is open (or was closed by a human), and its head
          contains exactly the patched files. Store it.
failed    permanent: the patch cannot be published (tests not passed, stale base,
          unsafe path, GitHub refused). Retrying will not help.
unknown   GitHub could not be confirmed (timeout, 5xx, rate limit). Safe to call again.

How duplicates are prevented
----------------------------
* The branch name is deterministic (``agent/fix-<incident id>``), so a second attempt
  looks at the same branch and the same PR instead of making new ones.
* Every step is "read, then write only if needed": PR exists? branch exists? branch
  already contains the patch? An interrupted run therefore resumes where it stopped.
* After an ambiguous failure (lost response) we re-read GitHub before ever retrying.
* All files are committed in ONE commit via the git data API, so there is no
  half-written state with some files committed and others not.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Optional, Union

from .github_client import (
    GitHubClient,
    GitHubError,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubUnavailable,
    GitHubValidationError,
    get_client,
)
from .github_types import (
    STATUS_FAILED,
    STATUS_UNKNOWN,
    STATUS_VERIFIED,
    GitHubOperationResult,
    PatchFile,
    PublishRequest,
    PullRequestInfo,
    RepoFile,
    fix_branch_name,
)

log = logging.getLogger("agents.publisher")

_INCIDENT_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
FORBIDDEN_PREFIXES = (".git/", ".github/")   # the agent may never edit CI or git internals
# The only files a patch may CREATE: regression tests the repair agent wrote and verified
# (they fail on the base commit and pass with the fix). Everything else is an in-place edit.
NEW_TEST_FILE_RE = re.compile(r"^(?:shopping|message-platform)/(?:[a-z0-9_-]+/)*tests/test_opsrelay_[a-z0-9_]+\.py$")
MAX_TEST_SUMMARY_CHARS = 3000


class PublishRejected(Exception):
    """The patch cannot be published. Permanent: retrying will not help."""


def validate_request(req: PublishRequest) -> Optional[str]:
    """Cheap local checks done BEFORE touching GitHub. Returns a problem, or ``None``."""
    if not req.tests_passed:
        return "patch has not passed its required tests; refusing to publish"
    if not req.incident_id or not _INCIDENT_ID_RE.match(req.incident_id):
        return f"invalid incident id {req.incident_id!r}"
    if not req.base_sha or not _SHA_RE.match(req.base_sha.lower()):
        return f"invalid base sha {req.base_sha!r}"
    if not req.files:
        return "patch contains no files"
    seen: set[str] = set()
    for f in req.files:
        path = f.path or ""
        base = path.rsplit("/", 1)[-1]
        if (
            not path
            or path.startswith("/")
            or "\\" in path
            or ".." in path.split("/")
            or path.startswith(FORBIDDEN_PREFIXES)
            or base.startswith(".env")
        ):
            return f"unsafe or invalid path in patch: {path!r}"
        if path in seen:
            return f"duplicate path in patch: {path!r}"
        if path in req.new_files and not NEW_TEST_FILE_RE.match(path):
            return f"only agent regression tests (tests/test_opsrelay_*.py) may be created, not {path!r}"
        if not isinstance(f.content, str):
            return f"content of {path!r} must be text"
        seen.add(path)
    unknown = sorted(set(req.new_files) - seen)
    if unknown:
        return f"new_files not in the patch: {unknown}"
    return None


class PullRequestPublisher:
    def __init__(
        self,
        client: Optional[GitHubClient] = None,
        *,
        max_attempts: int = 3,
        retry_delay: float = 2.0,
        draft: Optional[bool] = None,
    ):
        self.client = client or get_client()
        # Draft by default so a person must mark it ready; GITHUB_DRAFT_PRS=false opts out.
        self.draft = (os.getenv("GITHUB_DRAFT_PRS", "true").strip().lower() not in ("0", "false", "no")
                      if draft is None else draft)
        self.max_attempts = max(2, max_attempts)    # one attempt to write, one to confirm
        self.retry_delay = retry_delay

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #

    def create_or_find_pr(self, request: Union[PublishRequest, dict]) -> GitHubOperationResult:
        if isinstance(request, dict):
            request = PublishRequest.from_dict(request)
        steps: list[str] = []
        problem = validate_request(request)
        if problem:
            return GitHubOperationResult(status=STATUS_FAILED, detail=problem, steps=steps)

        branch = fix_branch_name(request.incident_id)
        try:
            return self._publish(request, branch, steps)
        except PublishRejected as exc:
            return GitHubOperationResult(
                status=STATUS_FAILED, branch=branch, detail=str(exc), steps=steps
            )
        except (GitHubUnavailable, GitHubRateLimited) as exc:
            return GitHubOperationResult(
                status=STATUS_UNKNOWN, branch=branch, steps=steps,
                detail=f"could not confirm the outcome on GitHub; safe to call again: {exc}",
            )
        except GitHubError as exc:
            return GitHubOperationResult(
                status=STATUS_FAILED, branch=branch, detail=str(exc), steps=steps
            )

    # ------------------------------------------------------------------ #
    # Main flow
    # ------------------------------------------------------------------ #

    def _publish(self, req: PublishRequest, branch: str, steps: list[str]) -> GitHubOperationResult:
        # 1. A PR for this incident may already exist (earlier run, or lost response).
        existing = self.client.find_pr(branch)
        if existing:
            return self._adopt_existing_pr(existing, req, branch, steps)

        # 2. Branch: reuse it if a previous attempt created it.
        head = self.client.get_branch(branch)
        if head is None:
            self._check_patch_applies_to_base(req, steps)
            self._ensure_branch(branch, req.base_sha, steps)
        else:
            steps.append(f"branch {branch} already exists at {head.sha[:7]}; reusing it")

        # 3. Commit: skipped when the branch already holds the patch.
        self.commit_patch(branch, req, steps)

        # 4. Pull request: found or created.
        pr = self._ensure_pr(branch, req, steps)

        # 5. Prove it.
        return self._verify(pr, branch, req, steps)

    # ------------------------------------------------------------------ #
    # Step: an existing PR
    # ------------------------------------------------------------------ #

    def _adopt_existing_pr(
        self, pr: PullRequestInfo, req: PublishRequest, branch: str, steps: list[str]
    ) -> GitHubOperationResult:
        steps.append(f"PR #{pr.number} already exists ({pr.state}); not creating another")
        if pr.state != "open":
            return GitHubOperationResult(
                status=STATUS_VERIFIED, branch=branch, pr_number=pr.number, pr_url=pr.url,
                commit_sha=pr.head_sha, pr_state=pr.state, steps=steps,
                detail=f"PR #{pr.number} was {pr.state} by a person; it was not reopened or recreated",
            )
        if not self._contents_match(pr.head_sha, req.files):
            raise PublishRejected(
                f"PR #{pr.number} already exists for {branch} but its files differ from this patch; "
                "needs human review"
            )
        return GitHubOperationResult(
            status=STATUS_VERIFIED, branch=branch, pr_number=pr.number, pr_url=pr.url,
            commit_sha=pr.head_sha, pr_state="open", steps=steps,
            detail=f"PR #{pr.number} already existed and matches the patch",
        )

    # ------------------------------------------------------------------ #
    # Step: is it safe to start a brand-new branch?
    # ------------------------------------------------------------------ #

    def _check_patch_applies_to_base(self, req: PublishRequest, steps: list[str]) -> None:
        base_branch = req.base_branch or self.client.config.base_branch
        paths = [f.path for f in req.files]

        try:
            self.client.get_commit(req.base_sha)
        except GitHubNotFound:
            raise PublishRejected(f"base commit {req.base_sha[:7]} does not exist in the repository")

        current = self._read_files(req.base_sha, paths)
        missing = [p for p, f in current.items() if f is None and p not in req.new_files]
        if missing:
            raise PublishRejected(
                f"patch targets file(s) that do not exist at base commit {req.base_sha[:7]}: "
                f"{missing} (only in-place edits are supported)"
            )
        clashing = [p for p in req.new_files if current.get(p) is not None]
        if clashing:
            raise PublishRejected(f"new test file(s) already exist at {req.base_sha[:7]}: {clashing}")
        if all(current[f.path] is not None and current[f.path].content == f.content  # type: ignore[union-attr]
               for f in req.files):
            raise PublishRejected("patch does not change any file at the base commit")

        base_head = self.client.get_branch(base_branch)
        if base_head is None:
            raise PublishRejected(f"base branch {base_branch!r} was not found")
        if base_head.sha != req.base_sha:
            changed = set(self.client.compare_files(req.base_sha, base_head.sha))
            overlap = sorted(changed & set(paths))
            if overlap:
                raise PublishRejected(
                    f"{base_branch} moved from {req.base_sha[:7]} to {base_head.sha[:7]} and changed "
                    f"{overlap}; regenerate the fix against the new commit"
                )
            steps.append(
                f"{base_branch} moved ({req.base_sha[:7]} -> {base_head.sha[:7]}) "
                "but not in the patched files; continuing"
            )

    # ------------------------------------------------------------------ #
    # Step: branch
    # ------------------------------------------------------------------ #

    def _ensure_branch(self, branch: str, base_sha: str, steps: list[str]) -> str:
        """Create ``branch`` at ``base_sha`` unless it exists. Always confirm by reading."""
        ambiguous = False
        for _ in range(self.max_attempts):
            existing = self.client.get_branch(branch)
            if existing:
                note = " (confirmed after an ambiguous GitHub response)" if ambiguous else ""
                steps.append(f"branch {branch} ready at {existing.sha[:7]}{note}")
                return existing.sha
            try:
                self.client.create_branch(branch, base_sha)
            except GitHubUnavailable:
                ambiguous = True
                steps.append("branch creation response lost; re-checking GitHub")
                self._pause()
            except GitHubValidationError:
                if self.client.get_branch(branch) is None:
                    raise                     # a real rejection, e.g. unknown sha
                ambiguous = True              # it exists after all (race / lost response)
        raise GitHubUnavailable(f"could not confirm that branch {branch} was created")

    # ------------------------------------------------------------------ #
    # Step: commit
    # ------------------------------------------------------------------ #

    def commit_patch(
        self, branch: str, req: PublishRequest, steps: Optional[list[str]] = None
    ) -> str:
        """Make sure ``branch`` contains the patched files in one commit; returns the head sha.

        If the branch already holds exactly these files, nothing is written.
        """
        steps = steps if steps is not None else []
        paths = [f.path for f in req.files]
        for attempt in range(self.max_attempts):
            head = self.client.get_branch(branch)
            if head is None:
                raise PublishRejected(f"branch {branch} does not exist")

            current = self._read_files(head.sha, paths)
            missing = [p for p, f in current.items() if f is None and p not in req.new_files]
            if missing:
                raise PublishRejected(
                    f"patch targets file(s) that do not exist on {branch}: {missing} "
                    "(only in-place edits are supported)"
                )
            if all(current[f.path] is not None and current[f.path].content == f.content  # type: ignore[union-attr]
                   for f in req.files):
                steps.append(f"branch {branch} contains the patch at {head.sha[:7]}")
                return head.sha

            try:
                tree_base = self.client.get_tree_sha(head.sha)
                tree = self.client.create_tree(tree_base, {f.path: f.content for f in req.files})
                commit = self.client.create_git_commit(self._commit_message(req), tree, [head.sha])
            except GitHubUnavailable:
                steps.append(f"commit attempt {attempt + 1}: response lost before the commit existed")
                self._pause()
                continue                      # nothing visible changed; re-read and retry

            try:
                self.client.update_branch(branch, commit)    # fast-forward only
                steps.append(f"committed {len(req.files)} file(s) as {commit[:7]}")
            except (GitHubUnavailable, GitHubValidationError) as exc:
                steps.append(
                    f"commit attempt {attempt + 1}: branch update not confirmed "
                    f"({type(exc).__name__}); re-checking"
                )
                self._pause()
            # loop: re-read the branch, which either proves success or retries
        raise GitHubUnavailable(f"could not confirm the commit on {branch}")

    # ------------------------------------------------------------------ #
    # Step: pull request
    # ------------------------------------------------------------------ #

    def _ensure_pr(self, branch: str, req: PublishRequest, steps: list[str]) -> PullRequestInfo:
        ambiguous = False
        for _ in range(self.max_attempts):
            pr = self.client.find_pr(branch)
            if pr:
                note = " after an ambiguous response" if ambiguous else ""
                steps.append(f"PR #{pr.number} found{note}")
                return pr
            try:
                pr = self.client.create_pr(branch, self._title(req), self._body(req), draft=self.draft,
                                             base=req.base_branch or None)
                steps.append(f"opened PR #{pr.number}")
                return pr
            except GitHubUnavailable:
                ambiguous = True
                steps.append("PR creation response lost; checking whether GitHub created it")
                self._pause()
            except GitHubValidationError as exc:
                pr = self.client.find_pr(branch)
                if pr:
                    steps.append(f"PR #{pr.number} already existed")
                    return pr
                raise PublishRejected(f"GitHub rejected the pull request: {exc}")
        raise GitHubUnavailable(f"could not confirm that a PR was created for {branch}")

    def _verify(
        self, pr: PullRequestInfo, branch: str, req: PublishRequest, steps: list[str]
    ) -> GitHubOperationResult:
        final = self.client.get_pr(pr.number)
        base_branch = req.base_branch or self.client.config.base_branch
        problems = []
        if final.state != "open":
            problems.append(f"PR is {final.state}")
        if final.head_ref != branch:
            problems.append(f"head is {final.head_ref!r}, expected {branch!r}")
        if final.base_ref != base_branch:
            problems.append(f"base is {final.base_ref!r}, expected {base_branch!r}")
        if not self._contents_match(final.head_sha, req.files):
            problems.append("PR head does not contain the patched files")
        if problems:
            raise PublishRejected(f"PR #{final.number} failed verification: " + "; ".join(problems))
        steps.append(f"verified PR #{final.number} at {final.head_sha[:7]}")
        return GitHubOperationResult(
            status=STATUS_VERIFIED, branch=branch, pr_number=final.number, pr_url=final.url,
            commit_sha=final.head_sha, pr_state="open", steps=steps,
            detail="PR verified on GitHub",
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _pause(self) -> None:
        if self.retry_delay:
            time.sleep(self.retry_delay)

    def _read_files(self, ref: str, paths: list[str]) -> dict[str, Optional[RepoFile]]:
        return {p: self.client.get_file_or_none(p, ref=ref) for p in paths}

    def _contents_match(self, ref: str, files: list[PatchFile]) -> bool:
        current = self._read_files(ref, [f.path for f in files])
        return all(current[f.path] is not None and current[f.path].content == f.content for f in files)  # type: ignore[union-attr]

    @staticmethod
    def _one_line(text: str, limit: int) -> str:
        line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
        return line if len(line) <= limit else line[: limit - 1].rstrip() + "..."

    def _commit_message(self, req: PublishRequest) -> str:
        what = self._one_line(req.summary or req.root_cause, 55) or "automated fix"
        subject = f"fix({req.application or 'app'}): {what}"
        return (
            f"{subject}\n\n"
            f"Incident: {req.incident_id}\n"
            f"Base commit: {req.base_sha}\n"
            f"Root cause: {req.root_cause or 'n/a'}\n\n"
            "Automated fix proposed by the OpsRelay incident agent."
        )

    def _title(self, req: PublishRequest) -> str:
        if req.title.strip():
            return self._one_line(req.title, 120)
        what = self._one_line(req.summary or req.root_cause, 70) or "automated fix"
        app = f"{req.application}: " if req.application else ""
        return f"[OpsRelay] {app}{what} (incident {req.incident_id})"

    @staticmethod
    def _details(summary: str, text: str, limit: int) -> str:
        """A collapsed section, so the PR leads with the three things a reviewer needs."""
        text = (text or "").strip().replace("<!--", "").replace("-->", "")
        if not text:
            return ""
        if len(text) > limit:
            text = text[:limit] + "\n[... truncated ...]"
        return f"<details>\n<summary>{summary}</summary>\n\n{text}\n\n</details>\n\n"

    def _body(self, req: PublishRequest) -> str:
        files = "\n".join(f"- `{f.path}`" for f in req.files)
        tests = (req.test_summary or "").strip().replace("```", "'''")
        if len(tests) > MAX_TEST_SUMMARY_CHARS:
            tests = "[... truncated ...]\n" + tests[-MAX_TEST_SUMMARY_CHARS:]
        new_tests = (f"- The agent added {', '.join(f'`{p}`' for p in req.new_files)}: it fails without this fix "
                     "and passes with it. Review it like any other test.\n" if req.new_files else "")
        source = f"[CI run]({req.workflow_url})" if req.workflow_url else "live monitoring"
        checked = "The app's typecheck and build pass" if req.application.endswith("-web") else "The app's tests pass"
        after = ("- After it is merged and deployed, OpsRelay closes the incident once a fresh check passes.\n\n"
                 if not req.base_branch else
                 f"- This targets Dependabot's `{req.base_branch}`. Merge it there, let that PR's CI go green, "
                 "then merge the Dependabot PR.\n\n")
        return (
            f"## What broke\n\n{req.root_cause or 'n/a'}\n\n"
            f"## The fix\n\n{req.summary or 'n/a'}\n\n{files}\n\n"
            "## How it was checked\n\n"
            f"- {checked} with this change, run in an isolated copy of the repo.\n"
            f"{new_tests}"
            f"{after}"
            "**Your part:** review the diff, then mark this draft ready and merge it. "
            "The agent never merges on its own.\n\n"
            f"<sub>OpsRelay · incident `{req.incident_id}` · {req.application or 'app'} · "
            f"base `{req.base_sha[:7]}` · found by {source}</sub>\n\n"
            f"{self._details('Full investigation (evidence and reasoning)', req.investigation, 6000)}"
            f"{self._details('Test output', f'```{chr(10)}{tests}{chr(10)}```' if tests else '', MAX_TEST_SUMMARY_CHARS + 20)}"
            f"{self._details('Similar past incidents', req.context_notes, 3000)}"
            f"<!-- opsrelay-incident: {req.incident_id} -->\n"
        )


# --------------------------------------------------------------------------- #
# Module-level interface
# --------------------------------------------------------------------------- #

def create_or_find_pr(request: Union[PublishRequest, dict]) -> GitHubOperationResult:
    """Publish a verified patch as a PR, or return the PR that already exists for it."""
    return PullRequestPublisher().create_or_find_pr(request)


def commit_patch(branch_name: str, request: PublishRequest) -> str:
    """Ensure ``branch_name`` contains the patch (idempotent). Returns the head sha."""
    return PullRequestPublisher().commit_patch(branch_name, request)
