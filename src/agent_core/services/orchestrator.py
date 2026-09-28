"""End-to-End Orchestrator executing autonomous issue remediation and HITL proposal creation."""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_core.agent.drift import BaseDriftDetector, GitHubDriftDetector
from agent_core.agent.graph import build_agent_graph
from agent_core.agent.localizer import BugLocalizer
from agent_core.agent.nodes import AgentNodes, BasePlanPatchGenerator, MockPlanPatchGenerator
from agent_core.agent.state import AgentState, AgentStepStatus
from agent_core.domain.models import Repository
from agent_core.domain.schemas.localization import IssuePayload
from agent_core.domain.schemas.orchestrator import OrchestrationResult
from agent_core.services.approvals import ApprovalService
from agent_core.services.retrieval.search import TriModalSearchEngine
from agent_core.services.sandbox.runner import DockerSandboxRunner

logger = logging.getLogger(__name__)


class AutonomousRemediationOrchestrator:
    """Coordinates bug diagnosis, cyclic patch generation, sandbox testing, and HITL gate."""

    def __init__(
        self,
        drift_detector: BaseDriftDetector | None = None,
        localizer: BugLocalizer | None = None,
        generator: BasePlanPatchGenerator | None = None,
        sandbox_runner: DockerSandboxRunner | None = None,
        approval_service: ApprovalService | None = None,
    ) -> None:
        self.drift_detector = drift_detector or GitHubDriftDetector()
        self.localizer = localizer
        self.generator = generator or MockPlanPatchGenerator()
        self.sandbox_runner = sandbox_runner or DockerSandboxRunner()
        self.approval_service = approval_service or ApprovalService()

    async def orchestrate_repair(
        self,
        session: AsyncSession,
        repo_id: uuid.UUID,
        issue_payload: IssuePayload,
        base_commit_sha: str,
        target_branch: str = "main",
        workspace_files: dict[str, str] | None = None,
        max_iterations: int = 3,
        thread_id: str | None = None,
    ) -> OrchestrationResult:
        """Runs full autonomous diagnostic, patch, verify, and governance registration pipeline."""
        tid = thread_id or f"remediate-{uuid.uuid4().hex[:12]}"

        # 1. Fetch Repository Metadata
        repo_stmt = select(Repository).where(Repository.id == repo_id)
        repo_res = await session.execute(repo_stmt)
        repo = repo_res.scalars().first()
        if not repo:
            raise ValueError(f"Repository with ID '{repo_id}' not found.")

        # 2. Configure Sandbox Files
        if workspace_files:
            self.sandbox_runner.set_workspace_files(workspace_files)

        # 3. Assemble LangGraph Execution Nodes
        localizer = self.localizer or BugLocalizer(
            search_engine=TriModalSearchEngine(session=session)
        )
        nodes = AgentNodes(
            repo_owner=repo.owner,
            repo_name=repo.name,
            drift_detector=self.drift_detector,
            localizer=localizer,
            generator=self.generator,
            sandbox_runner=self.sandbox_runner,
        )
        graph = build_agent_graph(nodes)

        # 4. Invoke Cyclic Repair State Machine
        initial_state: AgentState = {
            "repo_id": repo_id,
            "issue_payload": issue_payload,
            "base_commit_sha": base_commit_sha,
            "target_branch": target_branch,
            "iteration_count": 0,
            "max_iterations": max_iterations,
        }

        logger.info("Executing autonomous repair state machine for thread: %s", tid)
        final_state = await graph.ainvoke(initial_state)
        step_status = final_state.get("status", AgentStepStatus.FAILED)

        # 5. Handle Terminal Outcome
        if step_status == AgentStepStatus.AWAITING_APPROVAL:
            verif = final_state["verification_result"]
            patches = final_state.get("patches", [])
            plan = final_state.get("remediation_plan", "")

            # Persist proposal in PostgreSQL HITL governance gate
            proposal = await self.approval_service.create_approval_proposal(
                session=session,
                repo_id=repo_id,
                thread_id=tid,
                issue_id=issue_payload.issue_id,
                title=issue_payload.title,
                base_commit_sha=final_state.get("base_commit_sha", base_commit_sha),
                target_branch=final_state.get("target_branch", target_branch),
                feature_branch=f"ai-fix-issue-{issue_payload.issue_id}",
                remediation_plan=plan,
                patches=patches,
                verification_result=verif,
            )

            return OrchestrationResult(
                thread_id=tid,
                status="awaiting_approval",
                final_step_status=AgentStepStatus.AWAITING_APPROVAL,
                approval_id=proposal.id,
                patches=patches,
                verification_result=verif,
                remediation_plan=plan,
                error_message=None,
            )

        err = final_state.get("error_message") or "Remediation failed to pass sandbox."
        return OrchestrationResult(
            thread_id=tid,
            status="failed",
            final_step_status=step_status,
            approval_id=None,
            patches=final_state.get("patches", []),
            verification_result=final_state.get("verification_result"),
            remediation_plan=final_state.get("remediation_plan"),
            error_message=err,
        )
