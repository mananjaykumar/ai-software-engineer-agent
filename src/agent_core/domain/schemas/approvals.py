"""Schemas for Human-in-the-Loop (HITL) approval gates and PR dispatch."""

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from agent_core.agent.state import FilePatch, VerificationResult


class ApprovalStatus(StrEnum):
    """Lifecycle status of an agent approval proposal."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISPATCHED = "dispatched"


class ApprovalDecisionRequest(BaseModel):
    """Request payload submitted by a human reviewer."""

    reviewer: str = Field(..., description="GitHub handle or email of reviewer", min_length=1)
    feedback: str | None = Field(default=None, description="Optional feedback or rejection reason")


class PullRequestResult(BaseModel):
    """Details of a successfully dispatched GitHub Pull Request."""

    pr_number: int = Field(..., description="GitHub Pull Request number")
    pr_url: str = Field(..., description="Web URL to open Pull Request on GitHub")
    branch_name: str = Field(..., description="Target feature branch containing the commit")
    title: str = Field(..., description="PR title")
    body: str = Field(..., description="PR markdown body")


class ApprovalDetail(BaseModel):
    """Full detail view of an approval request for review."""

    id: uuid.UUID
    repo_id: uuid.UUID
    thread_id: str
    issue_id: int
    title: str
    base_commit_sha: str
    target_branch: str
    feature_branch: str
    remediation_plan: str
    patches: list[FilePatch]
    verification_result: VerificationResult
    status: ApprovalStatus
    reviewer: str | None = None
    rejection_reason: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    created_at: datetime
    updated_at: datetime


class ApprovalSummary(BaseModel):
    """Lightweight summary of pending approvals for list view."""

    id: uuid.UUID
    thread_id: str
    issue_id: int
    title: str
    status: ApprovalStatus
    target_branch: str
    feature_branch: str
    created_at: datetime
