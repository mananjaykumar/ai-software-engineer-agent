import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from agent_core.core.config import get_settings
from agent_core.main import app
from agent_core.services.github.client import GitHubAppClient

client = TestClient(app)


def test_webhook_unauthorized_without_signature() -> None:
    response = client.post(
        "/api/v1/webhooks/github",
        json={"action": "opened"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid webhook signature"


def test_webhook_ping_event_accepted() -> None:
    settings = get_settings()
    secret = settings.GITHUB_WEBHOOK_SECRET.get_secret_value()

    payload = {"zen": "Keep it logically awesome."}
    payload_bytes = json.dumps(payload).encode("utf-8")

    # Generate valid signature
    sig = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()

    response = client.post(
        "/api/v1/webhooks/github",
        content=payload_bytes,
        headers={
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": f"sha256={sig}",
            "X-GitHub-Delivery": "delivery-uuid-1234",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "ok"
    assert response.json()["message"] == "PONG"


def test_webhook_issue_event_accepted() -> None:
    settings = get_settings()
    secret = settings.GITHUB_WEBHOOK_SECRET.get_secret_value()

    payload = {
        "action": "opened",
        "issue": {
            "id": 1001,
            "number": 42,
            "title": "Bug in authentication flow",
            "state": "open",
            "html_url": "https://github.com/test-owner/test-repo/issues/42",
        },
        "repository": {
            "id": 2001,
            "name": "test-repo",
            "full_name": "test-owner/test-repo",
            "owner": {"id": 1, "login": "test-owner"},
            "default_branch": "main",
        },
        "sender": {"id": 1, "login": "test-owner"},
    }
    payload_bytes = json.dumps(payload).encode("utf-8")

    # Generate signature
    sig = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()

    response = client.post(
        "/api/v1/webhooks/github",
        content=payload_bytes,
        headers={
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": f"sha256={sig}",
            "X-GitHub-Delivery": "delivery-issue-5678",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
    assert response.json()["event"] == "issues"


async def test_github_app_client_jwt_and_caching() -> None:
    github_client = GitHubAppClient()

    # 1. Test JWT creation
    jwt_token = github_client.create_app_jwt()
    assert isinstance(jwt_token, str)
    assert len(jwt_token) > 20

    # 2. Test Installation Token (mock mode in dev)
    token = await github_client.get_installation_access_token(installation_id=12345)
    assert token.startswith("ghs_mock_token")

    # 3. Test In-Memory Cache (should return cached token instantly)
    cached_token = await github_client.get_installation_access_token(installation_id=12345)
    assert cached_token == token
