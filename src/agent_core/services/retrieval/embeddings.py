"""Asynchronous client for generating vector embeddings via OpenAI."""

from openai import AsyncOpenAI

from agent_core.core.config import get_settings


class OpenAIEmbeddingsClient:
    """Generates dense vector embeddings using OpenAI's embedding API."""

    def __init__(
        self,
        client: AsyncOpenAI | None = None,
        model: str = "text-embedding-3-small",
    ) -> None:
        self.model = model
        if client is not None:
            self._client = client
        else:
            settings = get_settings()
            self._client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY.get_secret_value())

    async def embed_text(self, text: str) -> list[float]:
        """Generates a 1536-dimensional embedding for a single text query."""
        response = await self._client.embeddings.create(
            input=text,
            model=self.model,
        )
        return response.data[0].embedding

    async def embed_batch(self, texts: list[str], batch_size: int = 100) -> list[list[float]]:
        """Generates embeddings for a batch of code chunks in chunks of `batch_size`."""
        if not texts:
            return []

        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            chunk = texts[i : i + batch_size]
            response = await self._client.embeddings.create(
                input=chunk,
                model=self.model,
            )
            # OpenAI preserves input ordering in response.data
            sorted_data = sorted(response.data, key=lambda d: d.index)
            all_embeddings.extend([item.embedding for item in sorted_data])

        return all_embeddings
