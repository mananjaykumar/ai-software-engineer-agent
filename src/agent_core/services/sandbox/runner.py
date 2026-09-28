"""Docker sandbox runner integrating with the LangGraph state machine."""

import uuid

from agent_core.agent.nodes import BaseSandboxRunner
from agent_core.agent.state import FilePatch, VerificationResult
from agent_core.services.sandbox.manager import DockerSandboxManager


class DockerSandboxRunner(BaseSandboxRunner):
    """Executes patches in an air-gapped Docker container for LangGraph verify_node."""

    def __init__(
        self,
        manager: DockerSandboxManager | None = None,
        workspace_files: dict[str, str] | None = None,
    ) -> None:
        self.manager = manager or DockerSandboxManager()
        self.workspace_files = workspace_files or {}

    def set_workspace_files(self, files: dict[str, str]) -> None:
        """Sets or refreshes base repository workspace files."""
        self.workspace_files = dict(files)

    async def run_verification(
        self,
        repo_id: uuid.UUID,
        base_commit_sha: str,
        patches: list[FilePatch],
    ) -> VerificationResult:
        """Runs the test suite inside the air-gapped container."""
        return await self.manager.run_in_sandbox(
            base_files=self.workspace_files,
            patches=patches,
        )
