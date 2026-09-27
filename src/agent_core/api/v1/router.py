from fastapi import APIRouter

from agent_core.api.v1.webhooks import router as webhooks_router

api_router = APIRouter()
api_router.include_router(webhooks_router, prefix="/webhooks", tags=["webhooks"])
