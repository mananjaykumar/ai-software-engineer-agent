"""Unit tests for GitHub PR Dispatcher and automated markdown reporting."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agent_core.agent.state import FilePatch, VerificationResult
from agent_core.services.github.dispatcher import GitHubPRDispatcher


def test_format_pr_body() -> None:
    dispatcher = GitHubPRDispatcher()
    patches = [
        FilePatch(
            file_path="src/agent_core/main.py",
            original_snippet="def old(): pass",
            replacement_snippet="def new(): pass",
            rationale="Fix broken auth handler",
        )
    ]
    verif = VerificationResult(
        passed=True,
        stdout="================ 5 passed in 0.42s ================",
        stderr="",
        exit_code=0,
        failing_tests=[],
    )

    body = dispatcher.format_pr_body(
        issue_id=42,
        problem_statement="Null pointer in auth middleware",
        remediation_plan="Add nil check before token validation",
        patches=patches,
        verification_result=verif,
    )

    assert "Closes #42" in body
    assert "Null pointer in auth middleware" in body
    assert "Add nil check before token validation" in body
    assert "Outcome**: PASSED ✅" in body
    assert "network_mode='none'" in body
    assert "src/agent_core/main.py" in body
    assert "Fix broken auth handler" in body
    assert "Human-in-the-Loop Governance Gate" in body


@pytest.mark.asyncio
async def test_dispatch_pull_request_mock_mode() -> None:
    dispatcher = GitHubPRDispatcher()
    result = await dispatcher.dispatch_pull_request(
        owner="test-owner",
        repo="test-repo",
        base_branch="main",
        feature_branch="ai-fix-issue-42",
        title="fix: resolve null pointer in auth",
        body="Automated PR body",
        files_to_commit={"src/main.py": "def new(): pass"},
    )

    assert result.pr_number == 101
    assert result.pr_url == "https://github.com/test-owner/test-repo/pull/101"
    assert result.branch_name == "ai-fix-issue-42"
    assert result.title == "fix: resolve null pointer in auth"


@pytest.mark.asyncio
async def test_dispatch_pull_request_live_api() -> None:
    mock_gh_client = MagicMock()
    mock_gh_client.get_installation_access_token = AsyncMock(return_value="ghs_test_token_123")

    dispatcher = GitHubPRDispatcher(github_client=mock_gh_client)
    dispatcher.settings.GITHUB_PRIVATE_KEY = MagicMock()
    dispatcher.settings.GITHUB_PRIVATE_KEY.get_secret_value.return_value = (
        "-----BEGIN RSA PRIVATE KEY-----\nMIIE..."
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/git/ref/heads/main" in url:
            return httpx.Response(200, json={"object": {"sha": "commit_sha_base_123"}})
        if "/git/refs" in url:
            return httpx.Response(
                201,
                json={"ref": "refs/heads/ai-fix", "object": {"sha": "commit_sha_base_123"}},
            )
        if "/contents/src/main.py" in url and request.method == "GET":
            return httpx.Response(200, json={"sha": "old_blob_sha_456"})
        if "/contents/src/main.py" in url and request.method == "PUT":
            return httpx.Response(200, json={"content": {"sha": "new_blob_sha_789"}})
        if "/pulls" in url and request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "number": 55,
                    "html_url": "https://github.com/test-owner/test-repo/pull/55",
                },
            )
        return httpx.Response(404, json={"message": "Not found"})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def client_factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    patch_target = "agent_core.services.github.dispatcher.httpx.AsyncClient"
    with patch(patch_target, side_effect=client_factory):
        result = await dispatcher.dispatch_pull_request(
            owner="test-owner",
            repo="test-repo",
            base_branch="main",
            feature_branch="ai-fix-issue-42",
            title="fix: resolve null pointer in auth",
            body="Automated PR body",
            files_to_commit={"src/main.py": "def new(): pass"},
            installation_id=98765,
        )

    assert result.pr_number == 55
    assert result.pr_url == "https://github.com/test-owner/test-repo/pull/55"
    assert result.branch_name == "ai-fix-issue-42"
