"""Domain schemas for autonomous end-to-end remediation orchestration."""

import uuid

from pydantic import BaseModel, Field

from agent_core.agent.state import AgentStepStatus, FilePatch, VerificationResult
from agent_core.domain.schemas.localization import IssuePayload


class OrchestrationRequest(BaseModel):
    """Execution parameters for autonomous remediation pipeline."""

    repo_id: uuid.UUID = Field(..., description="Target repository primary key")
    issue_payload: IssuePayload = Field(..., description="Structured issue description and logs")
    base_commit_sha: str = Field(..., description="Commit SHA that was checked out")
    target_branch: str = Field(default="main", description="Target base branch for remediation")
    max_iterations: int = Field(default=3, description="Maximum self-healing attempts")
    workspace_files: dict[str, str] = Field(
        default_factory=dict,
        description="Source and test files to mount in sandbox",
    )


class OrchestrationResult(BaseModel):
    """Aggregated output of the autonomous remediation workflow."""

    thread_id: str = Field(..., description="Unique correlation thread ID")
    status: str = Field(..., description="Overall workflow outcome status")
    final_step_status: AgentStepStatus = Field(..., description="Terminal state machine status")
    approval_id: uuid.UUID | None = Field(default=None, description="Created approval proposal ID")
    patches: list[FilePatch] = Field(default_factory=list, description="Synthesized file patches")
    verification_result: VerificationResult | None = Field(
        default=None,
        description="Final sandbox verification report",
    )
    remediation_plan: str | None = Field(default=None, description="Synthesized remediation plan")
    error_message: str | None = Field(default=None, description="Failure reason if unviable")
