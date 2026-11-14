"""OpsRelay AI diagnosis and code repair (investigator -> patcher -> verifier)."""

from .contracts import (
    AppConfig,
    CandidateAttempt,
    Diagnosis,
    Evidence,
    PatchResult,
    PriorIncident,
    RepairRequest,
    RepairResult,
    TestRun,
    TracebackFrame,
    load_app_config,
)
from .investigator import investigate
from .llm import OpenAIStructuredLLM, StructuredLLM
from .patcher import propose_patches, validate_patch
from .pipeline import repair_ci_failure
from .verifier import VerificationSession, verify_patch

__all__ = [
    "AppConfig",
    "CandidateAttempt",
    "Diagnosis",
    "Evidence",
    "OpenAIStructuredLLM",
    "PatchResult",
    "PriorIncident",
    "RepairRequest",
    "RepairResult",
    "StructuredLLM",
    "TestRun",
    "TracebackFrame",
    "VerificationSession",
    "investigate",
    "load_app_config",
    "propose_patches",
    "repair_ci_failure",
    "validate_patch",
    "verify_patch",
]
