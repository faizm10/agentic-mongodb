"""Single entry point for the orchestrator: diagnose, patch, verify."""

from __future__ import annotations

from .contracts import RepairRequest, RepairResult, load_app_config
from .investigator import investigate
from .llm import OpenAIStructuredLLM, StructuredLLM
from .patcher import propose_patch
from .verifier import verify_patch


def repair_ci_failure(request: RepairRequest, llm: StructuredLLM | None = None) -> RepairResult:
    """Return a verified patch or a ``needs_review`` result with a reason.

    Does no GitHub or MongoDB writes; the caller persists ``result.model_dump()``
    and hands a verified ``result.patch`` to the PR module. Model/API errors are
    raised so the orchestrator can apply its own retry policy.
    """
    app = load_app_config(request.app)
    llm = llm or OpenAIStructuredLLM()

    diagnosis = investigate(request, llm, app)
    if diagnosis.outcome == "needs_review":
        return RepairResult(
            outcome="needs_review", reason=f"diagnosis: {diagnosis.review_reason}",
            app=app.name, commit_sha=request.commit_sha, diagnosis=diagnosis,
        )

    patch = propose_patch(request, diagnosis, llm, app)
    if not patch.accepted or patch.diff is None:
        return RepairResult(
            outcome="needs_review", reason=f"patch rejected: {patch.rejection_reason}",
            app=app.name, commit_sha=request.commit_sha, diagnosis=diagnosis, patch=patch.diff,
        )

    result = verify_patch(app, request.repo_path, request.commit_sha, patch.diff, diagnosis.failing_tests)
    result.diagnosis = diagnosis
    return result
