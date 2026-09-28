"""End-to-End integration test simulating the entire autonomous bug repair lifecycle."""

import uuid

import httpx
import pytest
from sqlalchemy import select

from agent_core.agent.drift import BaseDriftDetector
from agent_core.agent.nodes import BasePlanPatchGenerator
from agent_core.agent.state import AgentStepStatus, FilePatch, VerificationResult
from agent_core.domain.models import ApprovalRequest, Repository
from agent_core.domain.schemas.localization import (
    CandidateFile,
    ExtractedEntities,
    IssuePayload,
    LocalizationResult,
)
from agent_core.infrastructure.db.session import get_db_session
from agent_core.main import app
from agent_core.services.orchestrator import AutonomousRemediationOrchestrator
from agent_core.services.sandbox.runner import DockerSandboxRunner


class DeterministicPlanPatchGenerator(BasePlanPatchGenerator):
    """Provides exact patch that fixes the calculator test."""

    async def generate_plan(
        self,
        issue: IssuePayload,
        localization: LocalizationResult,
        previous_verification: VerificationResult | None = None,
    ) -> str:
        return "Fix incorrect multiplication in divide function by using division operator."

    async def generate_patches(
        self,
        plan: str,
        localization: LocalizationResult,
        iteration: int,
    ) -> list[FilePatch]:
        return [
            FilePatch(
                file_path="src/calculator.py",
                original_snippet="def divide(a: float, b: float) -> float:\n    return a * b",
                replacement_snippet="def divide(a: float, b: float) -> float:\n    return a / b",
                rationale="Replace multiplication with division",
            )
        ]


class MockDriftDetector(BaseDriftDetector):
    """Bypasses remote GitHub git ref lookup for local integration test."""

    async def get_remote_head_sha(
        self,
        owner: str,
        repo: str,
        branch: str,
    ) -> str:
        return "commit_sha_123456"


@pytest.mark.asyncio
async def test_end_to_end_remediation_pipeline() -> None:
    test_repo_id: uuid.UUID | None = None
    thread_id = f"e2e-thread-{uuid.uuid4().hex[:8]}"

    # 1. Setup repository in PostgreSQL
    async for session in get_db_session():
        repo = Repository(
            github_repo_id=int(uuid.uuid4().int % 10000000),
            owner="enterprise-org",
            name="payment-service",
            default_branch="main",
            is_active=True,
        )
        session.add(repo)
        await session.commit()
        await session.refresh(repo)
        test_repo_id = repo.id
        break

    assert test_repo_id is not None

    # 2. Prepare workspace files with real bug and standard-library unittest
    workspace_files = {
        "src/__init__.py": "",
        "src/calculator.py": ("def divide(a: float, b: float) -> float:\n    return a * b\n"),
        "tests/__init__.py": "",
        "tests/test_calculator.py": (
            "import unittest\n"
            "from src.calculator import divide\n\n"
            "class TestCalculator(unittest.TestCase):\n"
            "    def test_divide(self):\n"
            "        self.assertEqual(divide(10.0, 2.0), 5.0)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n"
        ),
    }

    issue_payload = IssuePayload(
        repo_id=test_repo_id,
        issue_id=777,
        title="divide function returns product instead of quotient",
        body="Calling divide(10.0, 2.0) returned 20.0 instead of 5.0.",
    )

    # 3. Setup Orchestrator with air-gapped DockerSandboxRunner using python3 -m unittest
    sandbox_runner = DockerSandboxRunner(
        workspace_files=workspace_files,
        test_command=["python3", "-m", "unittest", "discover", "tests"],
    )
    generator = DeterministicPlanPatchGenerator()
    drift_detector = MockDriftDetector()

    # Mock localizer returning src/calculator.py as candidate
    class MockLocalizer:
        async def localize_bug(self, payload: IssuePayload) -> LocalizationResult:
            return LocalizationResult(
                repo_id=payload.repo_id,
                issue_id=payload.issue_id,
                problem_statement="divide function returns product instead of quotient",
                extracted_entities=ExtractedEntities(),
                candidate_files=[
                    CandidateFile(
                        file_path="src/calculator.py",
                        confidence_score=0.99,
                        rationale="Target file for calculation",
                    )
                ],
            )

    orchestrator = AutonomousRemediationOrchestrator(
        drift_detector=drift_detector,
        localizer=MockLocalizer(),  # type: ignore[arg-type]
        generator=generator,
        sandbox_runner=sandbox_runner,
    )

    try:
        # 4. Execute Autonomous Remediation Run
        async for session in get_db_session():
            result = await orchestrator.orchestrate_repair(
                session=session,
                repo_id=test_repo_id,
                issue_payload=issue_payload,
                base_commit_sha="commit_sha_123456",
                target_branch="main",
                workspace_files=workspace_files,
                thread_id=thread_id,
            )

            # Assert state machine flipped from failure to passing and staged proposal
            assert result.status == "awaiting_approval"
            assert result.final_step_status == AgentStepStatus.AWAITING_APPROVAL
            assert result.approval_id is not None
            assert len(result.patches) == 1
            assert result.patches[0].file_path == "src/calculator.py"
            assert result.verification_result is not None
            assert result.verification_result.passed is True
            assert result.verification_result.exit_code == 0
            break

        # 5. Verify and Exercise Human-in-the-Loop Governance API
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as ac:
            # Inspection
            detail_resp = await ac.get(f"/api/v1/approvals/{thread_id}")
            assert detail_resp.status_code == 200
            detail = detail_resp.json()
            assert detail["thread_id"] == thread_id
            assert detail["issue_id"] == 777
            assert detail["status"] == "pending"
            assert len(detail["patches"]) == 1

            # Approval & PR Dispatch
            approve_resp = await ac.post(
                f"/api/v1/approvals/{thread_id}/approve",
                json={"reviewer": "principal-architect"},
            )
            assert approve_resp.status_code == 200
            pr_data = approve_resp.json()
            assert pr_data["pr_number"] == 101
            assert pr_data["branch_name"] == "ai-fix-issue-777"
            assert "Closes #777" in pr_data["body"]
            assert "Air-Gapped Sandbox Verification" in pr_data["body"]

    finally:
        # Cleanup
        async for session in get_db_session():
            if test_repo_id:
                app_stmt = select(ApprovalRequest).where(ApprovalRequest.repo_id == test_repo_id)
                app_res = await session.execute(app_stmt)
                for req in app_res.scalars().all():
                    await session.delete(req)

                repo_stmt = select(Repository).where(Repository.id == test_repo_id)
                repo_res = await session.execute(repo_stmt)
                for r in repo_res.scalars().all():
                    await session.delete(r)

                await session.commit()
            break
