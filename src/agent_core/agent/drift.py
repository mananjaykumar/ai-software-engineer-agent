"""Base commit drift detection for guarding against race conditions on remote branches."""

from abc import ABC, abstractmethod
from typing import Any

import httpx

from agent_core.services.github.client import GitHubAppClient


class BaseDriftDetector(ABC):
    """Abstract interface for checking remote repository branch head SHAs."""

    @abstractmethod
    async def get_remote_head_sha(self, owner: str, repo: str, branch: str) -> str:
        """Retrieves the latest commit SHA for a target branch from remote."""
        pass

    async def check_drift(
        self,
        owner: str,
        repo: str,
        branch: str,
        expected_sha: str,
    ) -> tuple[bool, str]:
        """Compares expected base commit SHA against remote HEAD.

        Returns (is_drifted, current_head_sha).
        """
        current_sha = await self.get_remote_head_sha(owner, repo, branch)
        is_drifted = current_sha.strip().lower() != expected_sha.strip().lower()
        return is_drifted, current_sha


class GitHubDriftDetector(BaseDriftDetector):
    """Production drift detector querying the GitHub Commits REST API."""

    def __init__(
        self,
        github_client: GitHubAppClient | None = None,
        installation_id: int | None = None,
    ) -> None:
        self.github_client = github_client or GitHubAppClient()
        self.installation_id = installation_id

    async def get_remote_head_sha(self, owner: str, repo: str, branch: str) -> str:
        """Fetches commit SHA for branch from GitHub API."""
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.installation_id:
            token = await self.github_client.get_installation_access_token(self.installation_id)
            headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}",
                headers=headers,
                timeout=10.0,
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            return str(data["sha"])


class MockDriftDetector(BaseDriftDetector):
    """Deterministic mock drift detector for unit and integration testing."""

    def __init__(self, remote_sha: str = "mock_head_sha_12345") -> None:
        self.remote_sha = remote_sha

    async def get_remote_head_sha(self, owner: str, repo: str, branch: str) -> str:
        return self.remote_sha
