from fastapi import FastAPI

from agent_core.api.v1.router import api_router
from agent_core.core.config import get_settings

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
    """Lightweight health check endpoint for container probes."""
    return {"status": "healthy", "project": settings.PROJECT_NAME}
