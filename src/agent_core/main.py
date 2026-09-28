import logging
from typing import Annotated, Any

from docker.client import DockerClient
from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agent_core.api.v1.router import api_router
from agent_core.core.config import get_settings
from agent_core.infrastructure.db.session import get_db_session

logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    description="Production-grade Autonomous AI Software Engineering Agent",
)

# Mount API V1 routes
app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    """Lightweight health check endpoint for container liveness probes."""
    return {"status": "healthy", "project": settings.PROJECT_NAME}


@app.get("/health/ready", tags=["system"])
async def readiness_check(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Deep readiness probe verifying database and Docker sandbox connectivity."""
    checks: dict[str, str] = {}

    # 1. Database Check
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "healthy"
    except Exception as e:
        logger.error("Readiness probe database failure: %s", e)
        checks["database"] = f"unhealthy: {e}"

    # 2. Docker Daemon Check
    try:
        docker_client = DockerClient.from_env()
        docker_ok = docker_client.ping()
        checks["docker"] = "healthy" if docker_ok else "unresponsive"
    except Exception as e:
        logger.error("Readiness probe Docker daemon failure: %s", e)
        checks["docker"] = f"unhealthy: {e}"

    if any("unhealthy" in v or "unresponsive" in v for v in checks.values()):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready", "checks": checks},
        )

    return {
        "status": "ready",
        "checks": checks,
        "project": settings.PROJECT_NAME,
        "version": "0.1.0",
    }
