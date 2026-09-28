"""Unit and integration tests for the isolated air-gapped Docker sandbox."""

import uuid
from unittest.mock import MagicMock

import pytest

from agent_core.agent.nodes import BaseSandboxRunner
from agent_core.agent.state import FilePatch
from agent_core.services.sandbox.config import SandboxConfig
from agent_core.services.sandbox.manager import DockerSandboxManager
from agent_core.services.sandbox.patcher import SandboxPatcher
from agent_core.services.sandbox.runner import DockerSandboxRunner


def test_sandbox_patcher_apply_patches() -> None:
    """Verifies that in-memory patch replacement updates targeted file content."""
    base_files = {
        "services/billing.py": "def calc(x):\n    return x * 2\n",
        "utils/helpers.py": "def helper():\n    pass\n",
    }
    patches = [
        FilePatch(
            file_path="services/billing.py",
            original_snippet="return x * 2",
            replacement_snippet="if x is None:\n        return 0\n    return x * 2",
            rationale="guard against None",
        ),
        FilePatch(
            file_path="new_module.py",
            original_snippet="",
            replacement_snippet="# newly added file\n",
            rationale="add module",
        ),
    ]

    updated = SandboxPatcher.apply_patches(base_files, patches)

    assert "if x is None:" in updated["services/billing.py"]
    assert "new_module.py" in updated
    assert updated["new_module.py"] == "# newly added file\n"
    assert "def helper():" in updated["utils/helpers.py"]


def test_sandbox_patcher_missing_snippet_raises() -> None:
    """Verifies that an unmatchable original snippet raises a clear ValueError."""
    base_files = {"app.py": "print('hello')\n"}
    patch = FilePatch(
        file_path="app.py",
        original_snippet="non_existent_code_snippet()",
        replacement_snippet="replacement()",
        rationale="should fail",
    )
    with pytest.raises(ValueError, match="Original snippet for patch in 'app.py' not found"):
        SandboxPatcher.apply_patches(base_files, [patch])


def test_sandbox_patcher_tar_archive_roundtrip() -> None:
    """Verifies in-memory tar archive serialization and deserialization."""
    files = {
        "src/core/app.py": "x = 10\n",
        "tests/test_app.py": "assert True\n",
    }
    tar_bytes = SandboxPatcher.create_tar_archive(files)
    assert len(tar_bytes) > 0

    extracted = SandboxPatcher.extract_tar_archive(tar_bytes)
    assert extracted["src/core/app.py"] == "x = 10\n"
    assert extracted["tests/test_app.py"] == "assert True\n"


def test_sandbox_patcher_extract_failing_tests() -> None:
    """Verifies regex extraction of failing pytest identifiers."""
    sample_output = """
=================================== FAILURES ===================================
___________________________ test_calculate_total ___________________________
FAILED tests/unit/test_billing.py::test_calculate_total - TypeError: unsupported
FAILED tests/integration/test_orders.py::OrderSuite::test_checkout[case-1] - Error
=========================== short test summary info ============================
    """
    failing = SandboxPatcher.extract_failing_tests(sample_output, "")
    assert "tests/unit/test_billing.py::test_calculate_total" in failing
    assert "tests/integration/test_orders.py::OrderSuite::test_checkout[case-1]" in failing


def test_docker_sandbox_manager_security_options() -> None:
    """Verifies that containers are created with strict air-gap and cgroups security constraints."""
    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_client.containers.create.return_value = mock_container
    mock_container.wait.return_value = {"StatusCode": 0}
    mock_container.logs.return_value = b"All tests passed"

    config = SandboxConfig(
        image="python:3.12-slim",
        mem_limit="512m",
        nano_cpus=1_000_000_000,
        pids_limit=100,
        network_mode="none",
    )
    manager = DockerSandboxManager(config=config, docker_client=mock_client)

    result = manager._run_container_sync(
        base_files={"main.py": "print('ok')\n"},
        patches=[],
        command=["python3", "main.py"],
        timeout=10,
    )

    assert result.passed is True
    assert result.exit_code == 0

    # Verify security flags passed to Docker API
    create_kwargs = mock_client.containers.create.call_args[1]
    assert create_kwargs["network_mode"] == "none"  # Air-gapped
    assert create_kwargs["mem_limit"] == "512m"  # RAM cap
    assert create_kwargs["nano_cpus"] == 1_000_000_000  # CPU cap
    assert create_kwargs["pids_limit"] == 100  # Fork bomb guard
    assert create_kwargs["cap_drop"] == ["ALL"]  # Drop all capabilities
    assert create_kwargs["security_opt"] == ["no-new-privileges:true"]  # No SUID escalation
    assert create_kwargs["user"] == "1000:1000"  # Non-root

    # Verify cleanup guarantee
    mock_container.remove.assert_called_once_with(force=True)


def test_docker_sandbox_manager_timeout_handling() -> None:
    """Verifies that hanging executions are terminated and report exit code 124."""
    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_client.containers.create.return_value = mock_container
    mock_container.wait.side_effect = TimeoutError("Read timed out")

    manager = DockerSandboxManager(docker_client=mock_client)

    result = manager._run_container_sync(
        base_files={"main.py": "while True: pass\n"},
        patches=[],
        command=["python3", "main.py"],
        timeout=5,
    )

    assert result.passed is False
    assert result.exit_code == 124
    assert "TIMEOUT" in result.failing_tests
    assert "Execution timed out after 5s" in result.stderr
    mock_container.remove.assert_called_once_with(force=True)


@pytest.mark.asyncio
async def test_docker_sandbox_runner_protocol() -> None:
    """Verifies that DockerSandboxRunner conforms to LangGraph BaseSandboxRunner."""
    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_client.containers.create.return_value = mock_container
    mock_container.wait.return_value = {"StatusCode": 0}
    mock_container.logs.return_value = b"Passed"

    manager = DockerSandboxManager(docker_client=mock_client)
    runner = DockerSandboxRunner(manager=manager, workspace_files={"main.py": "print('ok')"})

    assert isinstance(runner, BaseSandboxRunner)

    result = await runner.run_verification(
        repo_id=uuid.uuid4(),
        base_commit_sha="commit_sha_123",
        patches=[],
    )

    assert result.passed is True
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_docker_sandbox_live_airgap_execution() -> None:
    """Live integration test: runs real code inside python:3.12-slim and asserts network block."""
    manager = DockerSandboxManager()
    base_files = {
        "test_airgap.py": (
            "import urllib.request\n"
            "try:\n"
            "    urllib.request.urlopen('https://example.com', timeout=1)\n"
            "    print('UNEXPECTED_NETWORK_SUCCESS')\n"
            "except Exception as e:\n"
            "    print(f'BLOCKED_BY_AIRGAP: {type(e).__name__}')\n"
        )
    }

    result = await manager.run_in_sandbox(
        base_files=base_files,
        patches=[],
        command=["python3", "test_airgap.py"],
        timeout=10,
    )

    assert result.passed is True
    assert result.exit_code == 0
    assert "BLOCKED_BY_AIRGAP" in result.stdout
