import logging
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import ValidationError

from agent_core.core.config import get_settings
from agent_core.domain.schemas.github import GitHubIssueEvent
from agent_core.services.github.security import verify_webhook_signature

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/github", status_code=status.HTTP_202_ACCEPTED)
async def handle_github_webhook(
    request: Request,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
    x_github_event: Annotated[str | None, Header()] = None,
    x_github_delivery: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """Ingests GitHub webhook events, verifies HMAC-SHA256 signatures, and routes payloads."""
    settings = get_settings()

    # 1. Read raw request bytes for HMAC verification
    raw_payload = await request.body()

    # 2. Cryptographic signature check
    is_valid = verify_webhook_signature(
        payload_body=raw_payload,
        secret=settings.GITHUB_WEBHOOK_SECRET.get_secret_value(),
        signature_header=x_hub_signature_256,
    )
    if not is_valid:
        logger.warning(
            "Rejected GitHub webhook: Invalid HMAC signature. Delivery: %s", x_github_delivery
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    # 3. Handle GitHub initial setup ping
    if x_github_event == "ping":
        logger.info("Received GitHub ping event. Delivery: %s", x_github_delivery)
        return {"status": "ok", "message": "PONG"}

    # 4. Handle Issue Events
    if x_github_event == "issues":
        try:
            event = GitHubIssueEvent.model_validate_json(raw_payload)
            logger.info(
                "Received issue event: action=%s, repo=%s, issue=#%d",
                event.action,
                event.repository.full_name,
                event.issue.number,
            )
            # In Phase 8, we enqueue this event to Redis/ARQ for the Agent Orchestrator
        except ValidationError as e:
            logger.error("Failed to parse GitHub issue event payload: %s", e)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid issue event schema",
            ) from e

    return {
        "status": "accepted",
        "event": x_github_event or "unknown",
        "delivery_id": x_github_delivery or "unknown",
    }
