"""Node handlers and execution interfaces for the LangGraph state machine."""

import uuid
from abc import ABC, abstractmethod
from typing import Any

from agent_core.agent.drift import BaseDriftDetector
from agent_core.agent.localizer import BugLocalizer
from agent_core.agent.state import (
    AgentState,
    AgentStepStatus,
    FilePatch,
    VerificationResult,
)
from agent_core.domain.schemas.localization import IssuePayload, LocalizationResult


class BaseSandboxRunner(ABC):
    """Abstract interface for running test verification in an isolated sandbox."""

    @abstractmethod
    async def run_verification(
        self,
        repo_id: uuid.UUID,
        base_commit_sha: str,
        patches: list[FilePatch],
    ) -> VerificationResult:
        """Executes the test suite against patched code in an isolated environment."""
        pass


class MockSandboxRunner(BaseSandboxRunner):
    """Deterministic mock sandbox for testing LangGraph execution cycles."""

    def __init__(
        self,
        default_passed: bool = True,
        fail_iterations: set[int] | None = None,
    ) -> None:
        self.default_passed = default_passed
        self.fail_iterations = fail_iterations or set()
        self.call_count = 0

    async def run_verification(
        self,
        repo_id: uuid.UUID,
        base_commit_sha: str,
        patches: list[FilePatch],
    ) -> VerificationResult:
        self.call_count += 1
        passed = False if self.call_count in self.fail_iterations else self.default_passed
        return VerificationResult(
            passed=passed,
            exit_code=0 if passed else 1,
            stdout="All tests passed." if passed else "AssertionError in calculate_total",
            stderr="" if passed else "TypeError: unsupported operand type(s)",
            failing_tests=[] if passed else ["test_calculate_total"],
        )


class BasePlanPatchGenerator(ABC):
    """Abstract interface for synthesizing remediation plans and code patches."""

    @abstractmethod
    async def generate_plan(
        self,
        issue: IssuePayload,
        localization: LocalizationResult,
        previous_verification: VerificationResult | None = None,
    ) -> str:
        """Generates or refines a remediation plan."""
        pass

    @abstractmethod
    async def generate_patches(
        self,
        plan: str,
        localization: LocalizationResult,
        iteration: int,
    ) -> list[FilePatch]:
        """Generates concrete file modifications."""
        pass


class MockPlanPatchGenerator(BasePlanPatchGenerator):
    """Deterministic plan and patch generator for testing."""

    async def generate_plan(
        self,
        issue: IssuePayload,
        localization: LocalizationResult,
        previous_verification: VerificationResult | None = None,
    ) -> str:
        if previous_verification and not previous_verification.passed:
            return (
                f"Refined plan addressing failure in {previous_verification.failing_tests}: "
                f"Add explicit NoneType guard to calculate_total."
            )
        candidate = (
            localization.candidate_files[0].file_path
            if localization.candidate_files
            else "unknown.py"
        )
        return f"Plan for #{issue.issue_id}: Validate input arguments in {candidate}."

    async def generate_patches(
        self,
        plan: str,
        localization: LocalizationResult,
        iteration: int,
    ) -> list[FilePatch]:
        target_file = (
            localization.candidate_files[0].file_path
            if localization.candidate_files
            else "unknown.py"
        )
        return [
            FilePatch(
                file_path=target_file,
                original_snippet="return amount * (Decimal('1.0') + self.tax_rate)",
                replacement_snippet=(
                    "if amount is None:\n"
                    "    raise ValueError('amount cannot be None')\n"
                    "return amount * (Decimal('1.0') + self.tax_rate)"
                ),
                rationale=f"Iteration {iteration}: Guard against NoneType amount.",
            )
        ]


class AgentNodes:
    """Implements individual state transformation nodes in the LangGraph workflow."""

    def __init__(
        self,
        localizer: BugLocalizer,
        drift_detector: BaseDriftDetector,
        sandbox_runner: BaseSandboxRunner,
        generator: BasePlanPatchGenerator,
        repo_owner: str = "default_owner",
        repo_name: str = "default_repo",
    ) -> None:
        self.localizer = localizer
        self.drift_detector = drift_detector
        self.sandbox_runner = sandbox_runner
        self.generator = generator
        self.repo_owner = repo_owner
        self.repo_name = repo_name

    async def drift_check_node(self, state: AgentState) -> dict[str, Any]:
        """Node 1: Guards against race conditions by checking remote branch drift."""
        expected_sha = state.get("base_commit_sha", "")
        branch = state.get("target_branch", "main")
        is_drifted, current_sha = await self.drift_detector.check_drift(
            owner=self.repo_owner,
            repo=self.repo_name,
            branch=branch,
            expected_sha=expected_sha,
        )
        if is_drifted:
            return {
                "drift_detected": True,
                "status": AgentStepStatus.DRIFT_DETECTED,
                "error_message": (
                    f"Remote branch '{branch}' drifted. Expected {expected_sha[:7]}, "
                    f"found {current_sha[:7]}."
                ),
            }
        return {
            "drift_detected": False,
            "status": AgentStepStatus.DRIFT_CHECKED,
        }

    async def diagnose_node(self, state: AgentState) -> dict[str, Any]:
        """Node 2: Runs issue analysis and tri-modal retrieval to rank culprit files."""
        if state.get("localization_result") is not None:
            return {"status": AgentStepStatus.LOCALIZED}

        payload = state["issue_payload"]
        localization = await self.localizer.localize_bug(payload)
        return {
            "localization_result": localization,
            "status": AgentStepStatus.LOCALIZED,
        }

    async def plan_node(self, state: AgentState) -> dict[str, Any]:
        """Node 3: Synthesizes or refines the remediation plan with self-healing context."""
        payload = state["issue_payload"]
        localization = state.get("localization_result")
        assert localization is not None, "Localization must precede planning"

        previous_verif = state.get("verification_result")
        plan = await self.generator.generate_plan(
            issue=payload,
            localization=localization,
            previous_verification=previous_verif,
        )
        return {
            "remediation_plan": plan,
            "status": AgentStepStatus.PLANNED,
        }

    async def patch_node(self, state: AgentState) -> dict[str, Any]:
        """Node 4: Generates concrete file patches and increments iteration counter."""

        plan = state.get("remediation_plan") or ""
        localization = state.get("localization_result")
        assert localization is not None, "Localization required for patching"

        current_iteration = state.get("iteration_count", 0) + 1
        patches = await self.generator.generate_patches(
            plan=plan,
            localization=localization,
            iteration=current_iteration,
        )

        return {
            "patches": patches,
            "iteration_count": current_iteration,
            "status": AgentStepStatus.PATCHED,
        }

    async def verify_node(self, state: AgentState) -> dict[str, Any]:
        """Node 5: Executes test suite in sandbox against generated patches."""
        patches = state.get("patches", [])
        result = await self.sandbox_runner.run_verification(
            repo_id=state["repo_id"],
            base_commit_sha=state.get("base_commit_sha", ""),
            patches=patches,
        )
        status = AgentStepStatus.VERIFIED if result.passed else AgentStepStatus.TEST_FAILED
        return {
            "verification_result": result,
            "status": status,
        }

    async def evaluate_node(self, state: AgentState) -> dict[str, Any]:
        """Node 6: Evaluates sandbox outcome and decides next step or loop termination."""
        verif = state.get("verification_result")
        if verif and verif.passed:
            return {"status": AgentStepStatus.AWAITING_APPROVAL}

        iteration = state.get("iteration_count", 0)
        max_iter = state.get("max_iterations", 3)
        if iteration >= max_iter:
            return {
                "status": AgentStepStatus.FAILED,
                "error_message": (
                    f"Exceeded max repair iterations ({max_iter}) without passing tests."
                ),
            }

        return {"status": AgentStepStatus.TEST_FAILED}
