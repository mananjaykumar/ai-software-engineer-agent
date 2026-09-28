"""Unit tests for the LangGraph state machine, self-healing loop, and drift guards."""

import uuid
from unittest.mock import AsyncMock

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver

from agent_core.agent.drift import MockDriftDetector
from agent_core.agent.graph import build_agent_graph
from agent_core.agent.localizer import BugLocalizer
from agent_core.agent.nodes import (
    AgentNodes,
    MockPlanPatchGenerator,
    MockSandboxRunner,
)
from agent_core.agent.state import AgentState, AgentStepStatus
from agent_core.domain.schemas.localization import (
    CandidateFile,
    ExtractedEntities,
    IssuePayload,
    LocalizationResult,
)


@pytest.fixture
def sample_payload() -> IssuePayload:
    return IssuePayload(
        issue_id=42,
        title="Crash when calculating total with None",
        body="TypeError: unsupported operand type(s) for * in calculate_total",
        repo_id=uuid.uuid4(),
    )


@pytest.fixture
def sample_localization(sample_payload: IssuePayload) -> LocalizationResult:
    return LocalizationResult(
        repo_id=sample_payload.repo_id,
        issue_id=sample_payload.issue_id,
        problem_statement=sample_payload.title,
        extracted_entities=ExtractedEntities(
            symbols=["calculate_total"],
            file_paths=["services/billing.py"],
            line_numbers=[42],
            error_types=["TypeError"],
            keywords=["crash", "calculating"],
        ),
        candidate_files=[
            CandidateFile(
                file_path="services/billing.py",
                confidence_score=0.95,
                rationale="Direct stack trace match",
                relevant_chunks=[],
                matched_symbols=["calculate_total"],
            )
        ],
        root_cause_hypothesis="TypeError in services/billing.py inside calculate_total",
    )


@pytest.mark.asyncio
async def test_agent_graph_happy_path(
    sample_payload: IssuePayload,
    sample_localization: LocalizationResult,
) -> None:
    """Verifies complete linear pass: drift check -> diagnose -> plan -> patch -> verify ->
    approve."""
    mock_localizer = AsyncMock(spec=BugLocalizer)
    mock_localizer.localize_bug.return_value = sample_localization

    drift = MockDriftDetector(remote_sha="sha_valid_123")
    sandbox = MockSandboxRunner(default_passed=True)
    generator = MockPlanPatchGenerator()

    nodes = AgentNodes(
        localizer=mock_localizer,
        drift_detector=drift,
        sandbox_runner=sandbox,
        generator=generator,
    )
    app = build_agent_graph(nodes)

    initial_state: AgentState = {
        "repo_id": sample_payload.repo_id,
        "issue_payload": sample_payload,
        "base_commit_sha": "sha_valid_123",
        "target_branch": "main",
        "iteration_count": 0,
        "max_iterations": 3,
        "drift_detected": False,
        "patches": [],
        "execution_trace": [],
    }

    final_state = await app.ainvoke(initial_state)

    assert final_state["status"] == AgentStepStatus.AWAITING_APPROVAL
    assert final_state["iteration_count"] == 1
    assert len(final_state["patches"]) == 1
    assert final_state["patches"][0].file_path == "services/billing.py"
    assert final_state["verification_result"] is not None
    assert final_state["verification_result"].passed is True


@pytest.mark.asyncio
async def test_agent_graph_self_healing_retry_loop(
    sample_payload: IssuePayload,
    sample_localization: LocalizationResult,
) -> None:
    """Verifies cyclical loop: iteration 1 fails tests, agent refines plan and passes iteration
    2."""
    mock_localizer = AsyncMock(spec=BugLocalizer)
    mock_localizer.localize_bug.return_value = sample_localization

    drift = MockDriftDetector(remote_sha="sha_valid_123")
    # Fail on first attempt, succeed on second attempt
    sandbox = MockSandboxRunner(default_passed=True, fail_iterations={1})
    generator = MockPlanPatchGenerator()

    nodes = AgentNodes(
        localizer=mock_localizer,
        drift_detector=drift,
        sandbox_runner=sandbox,
        generator=generator,
    )
    app = build_agent_graph(nodes)

    initial_state: AgentState = {
        "repo_id": sample_payload.repo_id,
        "issue_payload": sample_payload,
        "base_commit_sha": "sha_valid_123",
        "target_branch": "main",
        "iteration_count": 0,
        "max_iterations": 3,
        "drift_detected": False,
        "patches": [],
        "execution_trace": [],
    }

    final_state = await app.ainvoke(initial_state)

    # Must self-heal and conclude successfully on iteration 2
    assert final_state["status"] == AgentStepStatus.AWAITING_APPROVAL
    assert final_state["iteration_count"] == 2
    assert sandbox.call_count == 2
    assert "Refined plan addressing failure" in final_state.get("remediation_plan", "")


@pytest.mark.asyncio
async def test_agent_graph_max_iterations_failure(
    sample_payload: IssuePayload,
    sample_localization: LocalizationResult,
) -> None:
    """Verifies that exceeding max_iterations halts the cycle with status FAILED."""
    mock_localizer = AsyncMock(spec=BugLocalizer)
    mock_localizer.localize_bug.return_value = sample_localization

    drift = MockDriftDetector(remote_sha="sha_valid_123")
    # Consistently fail all verification attempts
    sandbox = MockSandboxRunner(default_passed=False)
    generator = MockPlanPatchGenerator()

    nodes = AgentNodes(
        localizer=mock_localizer,
        drift_detector=drift,
        sandbox_runner=sandbox,
        generator=generator,
    )
    app = build_agent_graph(nodes)

    initial_state: AgentState = {
        "repo_id": sample_payload.repo_id,
        "issue_payload": sample_payload,
        "base_commit_sha": "sha_valid_123",
        "target_branch": "main",
        "iteration_count": 0,
        "max_iterations": 2,
        "drift_detected": False,
        "patches": [],
        "execution_trace": [],
    }

    final_state = await app.ainvoke(initial_state)

    assert final_state["status"] == AgentStepStatus.FAILED
    assert final_state["iteration_count"] == 2
    assert "Exceeded max repair iterations" in (final_state.get("error_message") or "")


@pytest.mark.asyncio
async def test_agent_graph_drift_detection_abort(
    sample_payload: IssuePayload,
) -> None:
    """Verifies that remote branch drift immediately aborts execution without code modification."""
    mock_localizer = AsyncMock(spec=BugLocalizer)
    # Remote HEAD differs from base commit SHA
    drift = MockDriftDetector(remote_sha="diverged_remote_head_999")
    sandbox = MockSandboxRunner(default_passed=True)
    generator = MockPlanPatchGenerator()

    nodes = AgentNodes(
        localizer=mock_localizer,
        drift_detector=drift,
        sandbox_runner=sandbox,
        generator=generator,
    )
    app = build_agent_graph(nodes)

    initial_state: AgentState = {
        "repo_id": sample_payload.repo_id,
        "issue_payload": sample_payload,
        "base_commit_sha": "expected_base_sha_111",
        "target_branch": "main",
        "iteration_count": 0,
        "max_iterations": 3,
        "drift_detected": False,
        "patches": [],
        "execution_trace": [],
    }

    final_state = await app.ainvoke(initial_state)

    # Must abort at drift check
    assert final_state["drift_detected"] is True
    assert final_state["status"] == AgentStepStatus.DRIFT_DETECTED
    assert "Remote branch 'main' drifted" in (final_state.get("error_message") or "")
    # Subsystems downstream must never have run
    assert final_state.get("localization_result") is None
    assert final_state["iteration_count"] == 0
    assert len(final_state["patches"]) == 0
    mock_localizer.localize_bug.assert_not_called()


@pytest.mark.asyncio
async def test_agent_graph_checkpointing_memory_saver(
    sample_payload: IssuePayload,
    sample_localization: LocalizationResult,
) -> None:
    """Verifies LangGraph checkpoint persistence across execution steps."""
    mock_localizer = AsyncMock(spec=BugLocalizer)
    mock_localizer.localize_bug.return_value = sample_localization

    drift = MockDriftDetector(remote_sha="sha_valid_123")
    sandbox = MockSandboxRunner(default_passed=True)
    generator = MockPlanPatchGenerator()

    nodes = AgentNodes(
        localizer=mock_localizer,
        drift_detector=drift,
        sandbox_runner=sandbox,
        generator=generator,
    )

    checkpointer = MemorySaver()
    app = build_agent_graph(nodes, checkpointer=checkpointer)

    thread_id = "test-session-thread-42"
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

    initial_state: AgentState = {
        "repo_id": sample_payload.repo_id,
        "issue_payload": sample_payload,
        "base_commit_sha": "sha_valid_123",
        "target_branch": "main",
        "iteration_count": 0,
        "max_iterations": 3,
        "drift_detected": False,
        "patches": [],
        "execution_trace": [],
    }

    final_state = await app.ainvoke(initial_state, config=config)
    assert final_state["status"] == AgentStepStatus.AWAITING_APPROVAL

    # Verify checkpointer recorded the final state
    checkpoint_tuple = checkpointer.get_tuple(config)
    assert checkpoint_tuple is not None
    checkpoint_state = checkpoint_tuple.checkpoint["channel_values"]
    assert checkpoint_state["status"] == AgentStepStatus.AWAITING_APPROVAL
