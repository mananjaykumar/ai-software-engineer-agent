"""Agent state definitions, transitions, and schemas for the LangGraph state machine."""

import uuid
from enum import StrEnum
from typing import Any, TypedDict

from pydantic import BaseModel, Field

from agent_core.domain.schemas.localization import IssuePayload, LocalizationResult


class AgentStepStatus(StrEnum):
    """Lifecycle statuses for agent state graph execution."""

    INITIALIZED = "initialized"
    DRIFT_CHECKED = "drift_checked"
    DRIFT_DETECTED = "drift_detected"
    LOCALIZED = "localized"
    PLANNED = "planned"
    PATCHED = "patched"
    VERIFIED = "verified"
    TEST_FAILED = "test_failed"
    AWAITING_APPROVAL = "awaiting_approval"
    FAILED = "failed"


class FilePatch(BaseModel):
    """Represents a unified diff or code modification target."""

    file_path: str = Field(..., description="Target file relative path")
    original_snippet: str = Field(..., description="Original code section being replaced")
    replacement_snippet: str = Field(..., description="New code replacing original snippet")
    rationale: str = Field(..., description="Reasoning for this specific code edit")


class VerificationResult(BaseModel):
    """Execution output from the isolated test sandbox."""

    passed: bool = Field(..., description="Whether all executed tests passed cleanly")
    exit_code: int = Field(..., description="Process exit code from test command")
    stdout: str = Field(default="", description="Standard output from test runner")
    stderr: str = Field(default="", description="Standard error from test runner")
    failing_tests: list[str] = Field(
        default_factory=list,
        description="Names of failing test cases",
    )


class AgentState(TypedDict, total=False):
    """Type-safe execution state passed between LangGraph nodes."""

    # 1. Target Identity & Context
    repo_id: uuid.UUID
    issue_payload: IssuePayload
    base_commit_sha: str
    target_branch: str

    # 2. Cognitive Artifacts
    localization_result: LocalizationResult | None
    remediation_plan: str | None
    patches: list[FilePatch]
    verification_result: VerificationResult | None

    # 3. Execution Control & Self-Healing Loop
    status: AgentStepStatus
    iteration_count: int
    max_iterations: int
    error_message: str | None
    drift_detected: bool

    # 4. Message History / Tool Context
    execution_trace: list[dict[str, Any]]
