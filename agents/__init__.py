"""OpsRelay AI diagnosis and code repair (investigator -> patcher -> verifier)."""

from .contracts import (
    AppConfig,
    CandidateAttempt,
    Diagnosis,
    Evidence,
    LiveCheckResult,
    PatchResult,
    PriorIncident,
    RepairRequest,
    RepairResult,
    TestRun,
    TracebackFrame,
    load_app_config,
)
from .investigator import investigate
from .live_check import run_live_check
from .live_incident import (
    LiveIncident,
    LiveIncidentResult,
    RecoveryResult,
    confirm_recovery,
    diagnose_incident,
)
from .llm import OpenAIStructuredLLM, StructuredLLM
from .patcher import Repairer, propose_patches, validate_patch
from .pipeline import repair_ci_failure
from .verifier import VerificationSession, verify_patch

__all__ = [
    "AppConfig",
    "CandidateAttempt",
    "Diagnosis",
    "Evidence",
    "LiveCheckResult",
    "LiveIncident",
    "LiveIncidentResult",
    "OpenAIStructuredLLM",
    "PatchResult",
    "PriorIncident",
    "RecoveryResult",
    "Repairer",
    "RepairRequest",
    "RepairResult",
    "StructuredLLM",
    "TestRun",
    "TracebackFrame",
    "VerificationSession",
    "confirm_recovery",
    "diagnose_incident",
    "investigate",
    "load_app_config",
    "propose_patches",
    "repair_ci_failure",
    "run_live_check",
    "validate_patch",
    "verify_patch",
]
