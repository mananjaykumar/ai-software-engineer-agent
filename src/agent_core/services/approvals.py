"""Service layer for Human-in-the-Loop (HITL) approvals and PR dispatch coordination."""

import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_core.agent.state import FilePatch, VerificationResult
from agent_core.domain.models import ApprovalRequest, Repository
from agent_core.domain.schemas.approvals import (
    ApprovalDetail,
    ApprovalStatus,
    ApprovalSummary,
    PullRequestResult,
)
from agent_core.services.github.dispatcher import GitHubPRDispatcher


class ApprovalService:
    """Manages the persistence, review lifecycle, and automated dispatch of AI patch proposals."""

    def __init__(self, pr_dispatcher: GitHubPRDispatcher | None = None) -> None:
        self.pr_dispatcher = pr_dispatcher or GitHubPRDispatcher()

    async def create_approval_proposal(
        self,
        session: AsyncSession,
        repo_id: uuid.UUID,
        thread_id: str,
        issue_id: int,
        title: str,
        base_commit_sha: str,
        target_branch: str,
        feature_branch: str,
        remediation_plan: str,
        patches: list[FilePatch],
        verification_result: VerificationResult,
    ) -> ApprovalRequest:
        """Persists a new approval request in PostgreSQL once sandbox verification passes."""
        patches_json = json.dumps([p.model_dump() for p in patches])
        verif_json = json.dumps(verification_result.model_dump())

        approval = ApprovalRequest(
            repo_id=repo_id,
            thread_id=thread_id,
            issue_id=issue_id,
            title=title,
            base_commit_sha=base_commit_sha,
            target_branch=target_branch,
            feature_branch=feature_branch,
            remediation_plan=remediation_plan,
            patch_diff=patches_json,
            test_summary=verif_json,
            status=ApprovalStatus.PENDING,
        )
        session.add(approval)
        await session.commit()
        await session.refresh(approval)
        return approval

    async def list_pending_approvals(self, session: AsyncSession) -> list[ApprovalSummary]:
        """Lists all pending approval requests awaiting human review."""
        stmt = (
            select(ApprovalRequest)
            .where(ApprovalRequest.status == ApprovalStatus.PENDING)
            .order_by(ApprovalRequest.created_at.desc())
        )
        result = await session.execute(stmt)
        requests = result.scalars().all()
        return [
            ApprovalSummary(
                id=r.id,
                thread_id=r.thread_id,
                issue_id=r.issue_id,
                title=r.title,
                status=ApprovalStatus(r.status),
                target_branch=r.target_branch,
                feature_branch=r.feature_branch,
                created_at=r.created_at,
            )
            for r in requests
        ]

    async def get_approval_detail(
        self,
        session: AsyncSession,
        thread_id: str,
    ) -> ApprovalDetail | None:
        """Retrieves full approval details including plan, diffs, and test output."""
        stmt = select(ApprovalRequest).where(ApprovalRequest.thread_id == thread_id)
        result = await session.execute(stmt)
        r = result.scalars().first()
        if not r:
            return None

        patches_data = json.loads(r.patch_diff)
        patches = [FilePatch(**p) for p in patches_data]
        verif_data = json.loads(r.test_summary)
        verif = VerificationResult(**verif_data)

        return ApprovalDetail(
            id=r.id,
            repo_id=r.repo_id,
            thread_id=r.thread_id,
            issue_id=r.issue_id,
            title=r.title,
            base_commit_sha=r.base_commit_sha,
            target_branch=r.target_branch,
            feature_branch=r.feature_branch,
            remediation_plan=r.remediation_plan,
            patches=patches,
            verification_result=verif,
            status=ApprovalStatus(r.status),
            reviewer=r.reviewer,
            rejection_reason=r.rejection_reason,
            pr_number=r.pr_number,
            pr_url=r.pr_url,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )

    async def approve_and_dispatch(
        self,
        session: AsyncSession,
        thread_id: str,
        reviewer: str,
    ) -> tuple[ApprovalRequest, PullRequestResult]:
        """Approves the proposal, creates branch, commits patches, and opens a GitHub PR."""
        stmt = select(ApprovalRequest).where(ApprovalRequest.thread_id == thread_id)
        result = await session.execute(stmt)
        req = result.scalars().first()
        if not req:
            raise ValueError(f"Approval request for thread '{thread_id}' not found.")
        if req.status != ApprovalStatus.PENDING:
            raise ValueError(f"Approval request '{thread_id}' is already {req.status}.")

        # Retrieve repository for owner/name metadata
        repo_stmt = select(Repository).where(Repository.id == req.repo_id)
        repo_res = await session.execute(repo_stmt)
        repo = repo_res.scalars().first()
        if not repo:
            raise ValueError(f"Repository '{req.repo_id}' not found.")

        patches_data = json.loads(req.patch_diff)
        patches = [FilePatch(**p) for p in patches_data]
        verif_data = json.loads(req.test_summary)
        verif = VerificationResult(**verif_data)

        # Build commit mapping: file_path -> replacement_snippet
        files_to_commit = {p.file_path: p.replacement_snippet for p in patches}

        pr_body = self.pr_dispatcher.format_pr_body(
            issue_id=req.issue_id,
            problem_statement=req.title,
            remediation_plan=req.remediation_plan,
            patches=patches,
            verification_result=verif,
        )

        pr_result = await self.pr_dispatcher.dispatch_pull_request(
            owner=repo.owner,
            repo=repo.name,
            base_branch=req.target_branch,
            feature_branch=req.feature_branch,
            title=f"fix: {req.title} (#{req.issue_id})",
            body=pr_body,
            files_to_commit=files_to_commit,
        )

        # Update database record
        req.status = ApprovalStatus.APPROVED
        req.reviewer = reviewer
        req.pr_number = pr_result.pr_number
        req.pr_url = pr_result.pr_url

        await session.commit()
        await session.refresh(req)
        return req, pr_result

    async def reject_proposal(
        self,
        session: AsyncSession,
        thread_id: str,
        reviewer: str,
        reason: str | None = None,
    ) -> ApprovalRequest:
        """Rejects the proposal, halting PR dispatch and recording reviewer feedback."""
        stmt = select(ApprovalRequest).where(ApprovalRequest.thread_id == thread_id)
        result = await session.execute(stmt)
        req = result.scalars().first()
        if not req:
            raise ValueError(f"Approval request for thread '{thread_id}' not found.")
        if req.status != ApprovalStatus.PENDING:
            raise ValueError(f"Approval request '{thread_id}' is already {req.status}.")

        req.status = ApprovalStatus.REJECTED
        req.reviewer = reviewer
        req.rejection_reason = reason or "Rejected by reviewer without comment."

        await session.commit()
        await session.refresh(req)
        return req
