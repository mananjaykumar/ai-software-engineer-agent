"""Unit tests for system liveness and deep readiness probes."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from agent_core.main import app


@pytest.mark.asyncio
async def test_health_liveness() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_health_readiness_healthy() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.get("/health/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["checks"]["database"] == "healthy"
        assert data["checks"]["docker"] == "healthy"


@pytest.mark.asyncio
async def test_health_readiness_docker_failure() -> None:
    mock_docker_cls = MagicMock()
    mock_client = MagicMock()
    mock_client.ping.side_effect = RuntimeError("Docker socket unavailable")
    mock_docker_cls.from_env.return_value = mock_client

    with patch("agent_core.main.DockerClient", mock_docker_cls):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/health/ready")
            assert resp.status_code == 503
            data = resp.json()["detail"]
            assert data["status"] == "not_ready"
            assert "unhealthy" in data["checks"]["docker"]
