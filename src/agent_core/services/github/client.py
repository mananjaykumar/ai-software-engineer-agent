import logging
import time
from typing import Any

import httpx
import jwt

from agent_core.core.config import get_settings

logger = logging.getLogger(__name__)


class GitHubAppClient:
    """Manages GitHub App authentication, RS256 JWT generation, and installation token caching."""

    def __init__(self) -> None:
        self.settings = get_settings()
        # In-memory token cache: {installation_id: (token_str, expiry_epoch_timestamp)}
        self._token_cache: dict[int, tuple[str, float]] = {}

    def create_app_jwt(self) -> str:
        """Generates an RS256 JWT signed with the App's private key valid for 9 minutes."""
        now = int(time.time())
        payload = {
            "iat": now - 60,  # 60 seconds in the past to account for clock skew
            "exp": now + (9 * 60),  # 9 minutes in future (GitHub max is 10 min)
            "iss": self.settings.GITHUB_APP_ID,
        }
        private_key = self.settings.GITHUB_PRIVATE_KEY.get_secret_value()

        # In testing/dev with mock keys, fallback to HS256 to allow mock runs without real RSA keys
        if "mock" in private_key:
            return str(jwt.encode(payload, "mock_secret_key_32_bytes_long_!!", algorithm="HS256"))

        return str(jwt.encode(payload, private_key, algorithm="RS256"))

    async def get_installation_access_token(self, installation_id: int) -> str:
        """Retrieves a 1-hour repository installation access token, using cache if valid."""
        now = time.time()

        # Check if we have a valid cached token with at least 5 minutes of buffer
        if installation_id in self._token_cache:
            cached_token, expires_at = self._token_cache[installation_id]
            if now < (expires_at - 300):
                return cached_token

        # In dev with mock keys, return a dummy token without hitting GitHub's API
        if "mock" in self.settings.GITHUB_PRIVATE_KEY.get_secret_value():
            mock_token = f"ghs_mock_token_for_installation_{installation_id}"
            self._token_cache[installation_id] = (mock_token, now + 3600)
            return mock_token

        # Exchange JWT for real installation token from GitHub
        jwt_token = self.create_app_jwt()
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.github.com/app/installations/{installation_id}/access_tokens",
                headers=headers,
                timeout=10.0,
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            token: str = data["token"]

            # Cache the token for 55 minutes
            self._token_cache[installation_id] = (token, now + 3300)
            return token
