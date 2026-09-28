"""Human-in-the-Loop (HITL) approval endpoints for reviewing and dispatching PRs."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from agent_core.domain.schemas.approvals import (
    ApprovalDecisionRequest,
    ApprovalDetail,
    ApprovalSummary,
    PullRequestResult,
)
from agent_core.infrastructure.db.session import get_db_session
from agent_core.services.approvals import ApprovalService

router = APIRouter()
approval_service = ApprovalService()


@router.get("", response_model=list[ApprovalSummary])
async def list_pending_approvals(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[ApprovalSummary]:
    """Lists all pending AI remediation proposals awaiting human review."""
    return await approval_service.list_pending_approvals(session)


@router.get("/{thread_id}", response_model=ApprovalDetail)
async def get_approval_detail(
    thread_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ApprovalDetail:
    """Retrieves comprehensive details for a specific proposal including diffs and test logs."""
    detail = await approval_service.get_approval_detail(session, thread_id)
    if not detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval request for thread '{thread_id}' not found.",
        )
    return detail


@router.post("/{thread_id}/approve", response_model=PullRequestResult)
async def approve_proposal(
    thread_id: str,
    payload: ApprovalDecisionRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PullRequestResult:
    """Approves an AI patch proposal, creates feature branch, and opens a GitHub Pull Request."""
    try:
        _, pr_result = await approval_service.approve_and_dispatch(
            session=session,
            thread_id=thread_id,
            reviewer=payload.reviewer,
        )
        return pr_result
    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error_msg) from e
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg) from e


@router.post("/{thread_id}/reject", response_model=dict[str, Any])
async def reject_proposal(
    thread_id: str,
    payload: ApprovalDecisionRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Rejects an AI patch proposal, recording reviewer reasoning and preventing PR creation."""
    try:
        req = await approval_service.reject_proposal(
            session=session,
            thread_id=thread_id,
            reviewer=payload.reviewer,
            reason=payload.feedback,
        )
        return {
            "status": req.status,
            "thread_id": req.thread_id,
            "reviewer": req.reviewer,
            "reason": req.rejection_reason,
        }
    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error_msg) from e
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg) from e
