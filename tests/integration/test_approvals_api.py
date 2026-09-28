"""Integration tests for Human-in-the-Loop approvals API and state lifecycle."""

import uuid

import httpx
import pytest
from sqlalchemy import select

from agent_core.agent.state import FilePatch, VerificationResult
from agent_core.domain.models import ApprovalRequest, Repository
from agent_core.infrastructure.db.session import get_db_session
from agent_core.main import app
from agent_core.services.approvals import ApprovalService


@pytest.mark.asyncio
async def test_approvals_api_lifecycle() -> None:
    service = ApprovalService()
    test_repo_id: uuid.UUID | None = None
    thread_1 = f"thread-hitl-{uuid.uuid4().hex[:8]}"
    thread_2 = f"thread-hitl-{uuid.uuid4().hex[:8]}"

    async for session in get_db_session():
        # Setup repository
        repo = Repository(
            github_repo_id=int(uuid.uuid4().int % 10000000),
            owner="test-gov-org",
            name="test-gov-repo",
            default_branch="main",
            is_active=True,
        )
        session.add(repo)
        await session.commit()
        await session.refresh(repo)
        test_repo_id = repo.id

        # Setup proposal 1 (for rejection test)
        patches = [
            FilePatch(
                file_path="src/calc.py",
                original_snippet="def add(a, b): return a - b",
                replacement_snippet="def add(a, b): return a + b",
                rationale="Fix subtraction bug in addition function",
            )
        ]
        verif = VerificationResult(
            passed=True,
            stdout="1 passed in 0.01s",
            stderr="",
            exit_code=0,
            failing_tests=[],
        )

        await service.create_approval_proposal(
            session=session,
            repo_id=test_repo_id,
            thread_id=thread_1,
            issue_id=101,
            title="Fix inverted addition logic",
            base_commit_sha="abcdef1234567890",
            target_branch="main",
            feature_branch=f"ai-fix-{thread_1}",
            remediation_plan="Change minus to plus in calc.py",
            patches=patches,
            verification_result=verif,
        )

        # Setup proposal 2 (for approval test)
        await service.create_approval_proposal(
            session=session,
            repo_id=test_repo_id,
            thread_id=thread_2,
            issue_id=102,
            title="Fix second issue",
            base_commit_sha="abcdef1234567890",
            target_branch="main",
            feature_branch=f"ai-fix-{thread_2}",
            remediation_plan="Apply patch 2",
            patches=patches,
            verification_result=verif,
        )
        break

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        try:
            # 1. Test GET /api/v1/approvals (list pending)
            list_resp = await ac.get("/api/v1/approvals")
            assert list_resp.status_code == 200
            items = list_resp.json()
            assert any(i["thread_id"] == thread_1 for i in items)
            assert any(i["thread_id"] == thread_2 for i in items)

            # 2. Test GET /api/v1/approvals/{thread_id} (detail)
            detail_resp = await ac.get(f"/api/v1/approvals/{thread_1}")
            assert detail_resp.status_code == 200
            detail = detail_resp.json()
            assert detail["thread_id"] == thread_1
            assert detail["issue_id"] == 101
            assert len(detail["patches"]) == 1
            assert detail["status"] == "pending"

            # 3. Test GET 404 for unknown thread
            not_found_resp = await ac.get("/api/v1/approvals/non-existent-thread-xyz")
            assert not_found_resp.status_code == 404

            # 4. Test POST /reject
            reject_resp = await ac.post(
                f"/api/v1/approvals/{thread_1}/reject",
                json={"reviewer": "sec-lead", "feedback": "Inadequate test coverage"},
            )
            assert reject_resp.status_code == 200
            reject_data = reject_resp.json()
            assert reject_data["status"] == "rejected"
            assert reject_data["reviewer"] == "sec-lead"
            assert reject_data["reason"] == "Inadequate test coverage"

            # Attempting second action on rejected proposal returns 400
            re_reject_resp = await ac.post(
                f"/api/v1/approvals/{thread_1}/reject",
                json={"reviewer": "sec-lead"},
            )
            assert re_reject_resp.status_code == 400

            # 5. Test POST /approve
            approve_resp = await ac.post(
                f"/api/v1/approvals/{thread_2}/approve",
                json={"reviewer": "principal-eng"},
            )
            assert approve_resp.status_code == 200
            approve_data = approve_resp.json()
            assert approve_data["pr_number"] == 101
            assert "pull/101" in approve_data["pr_url"]
            assert approve_data["branch_name"] == f"ai-fix-{thread_2}"

            # Attempting second approval returns 400
            re_approve_resp = await ac.post(
                f"/api/v1/approvals/{thread_2}/approve",
                json={"reviewer": "principal-eng"},
            )
            assert re_approve_resp.status_code == 400

        finally:
            # Cleanup
            async for session in get_db_session():
                if test_repo_id:
                    app_stmt = select(ApprovalRequest).where(
                        ApprovalRequest.repo_id == test_repo_id
                    )
                    app_res = await session.execute(app_stmt)
                    for req in app_res.scalars().all():
                        await session.delete(req)

                    repo_stmt = select(Repository).where(Repository.id == test_repo_id)
                    repo_res = await session.execute(repo_stmt)
                    for r in repo_res.scalars().all():
                        await session.delete(r)

                    await session.commit()
                break
