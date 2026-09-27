"""Integration tests for Tri-Modal Search Engine and Repository Ingestion against PostgreSQL."""

import uuid

import pytest
from sqlalchemy import select

from agent_core.domain.models import CodeChunk, CodeSymbol, Repository
from agent_core.infrastructure.db.session import get_db_session
from agent_core.services.retrieval.embeddings import OpenAIEmbeddingsClient
from agent_core.services.retrieval.ingestion import RepositoryIngestionService
from agent_core.services.retrieval.schemas import RetrievalMode, SearchQuery
from agent_core.services.retrieval.search import TriModalSearchEngine


class MockDeterministicEmbeddings(OpenAIEmbeddingsClient):
    """Deterministic mock embedding client for testing pgvector without OpenAI API keys."""

    def __init__(self, dim: int = 1536) -> None:
        self.dim = dim

    async def embed_text(self, text: str) -> list[float]:
        val = (abs(hash(text)) % 1000) / 1000.0
        return [val] * self.dim

    async def embed_batch(self, texts: list[str], batch_size: int = 100) -> list[list[float]]:
        return [await self.embed_text(t) for t in texts]


SAMPLE_BILLING_CODE = '''import os
from decimal import Decimal

class BillingService:
    """Handles order billing and invoicing workflows."""

    def __init__(self, tax_rate: Decimal = Decimal("0.05")):
        self.tax_rate = tax_rate

    def calculate_total(self, amount: Decimal) -> Decimal:
        """Calculates total invoice amount with tax."""
        return amount * (Decimal("1.0") + self.tax_rate)

def format_currency(val: Decimal) -> str:
    """Formats numeric value to currency string."""
    return f"${val:.2f}"
'''


@pytest.mark.asyncio
async def test_repository_ingestion_and_hash_deduplication() -> None:
    """Validates relational persistence and SHA-256 hash deduplication in PostgreSQL."""
    async for session in get_db_session():
        # 1. Create a test repository
        unique_repo_id = int(uuid.uuid4().int % 10000000)
        repo = Repository(
            github_repo_id=unique_repo_id,
            owner="test-owner",
            name="test-billing-repo",
            default_branch="main",
            is_active=True,
        )
        session.add(repo)
        await session.flush()

        embeddings_mock = MockDeterministicEmbeddings()
        ingester = RepositoryIngestionService(session=session, embeddings_client=embeddings_mock)

        # 2. Ingest file for the first time
        result1 = await ingester.index_file(repo.id, "services/billing.py", SAMPLE_BILLING_CODE)
        assert result1.files_processed == 1
        # 1 class outline + 2 methods + 1 standalone function = 4 chunks
        assert result1.chunks_created == 4
        assert result1.chunks_skipped == 0
        assert result1.symbols_created == 4

        # 3. Verify records stored in PostgreSQL
        chunks_stmt = select(CodeChunk).where(CodeChunk.repo_id == repo.id)
        chunks_in_db = (await session.execute(chunks_stmt)).scalars().all()
        assert len(chunks_in_db) == 4

        symbols_stmt = select(CodeSymbol).join(CodeChunk).where(CodeChunk.repo_id == repo.id)
        symbols_in_db = (await session.execute(symbols_stmt)).scalars().all()
        assert len(symbols_in_db) == 4

        # 4. Ingest identical file again (Hash Deduplication Test)
        result2 = await ingester.index_file(repo.id, "services/billing.py", SAMPLE_BILLING_CODE)
        assert result2.files_processed == 1
        assert result2.chunks_created == 0  # 0 new chunks created!
        assert result2.chunks_skipped == 4  # All 4 skipped via content_hash!

        # Clean up test repo
        await session.delete(repo)
        await session.commit()


@pytest.mark.asyncio
async def test_tri_modal_search_and_hybrid_rrf() -> None:
    """Validates AST exact, lexical FTS, pgvector cosine, and Hybrid RRF ranking."""
    async for session in get_db_session():
        # 1. Setup indexed repository
        unique_repo_id = int(uuid.uuid4().int % 10000000)
        repo = Repository(
            github_repo_id=unique_repo_id,
            owner="test-owner",
            name="test-search-repo",
            default_branch="main",
            is_active=True,
        )
        session.add(repo)
        await session.flush()

        embeddings_mock = MockDeterministicEmbeddings()
        ingester = RepositoryIngestionService(session=session, embeddings_client=embeddings_mock)
        await ingester.index_file(repo.id, "services/billing.py", SAMPLE_BILLING_CODE)

        searcher = TriModalSearchEngine(session=session, embeddings_client=embeddings_mock)

        # 2. Test Mode 1: Exact AST Symbol Search
        ast_hits = await searcher.search_ast(repo.id, "calculate_total")
        assert len(ast_hits) >= 1
        top_ast = ast_hits[0]
        assert top_ast.retrieval_mode == RetrievalMode.AST
        assert top_ast.score == 1.0
        assert "calculate_total" in top_ast.matched_symbols
        assert "class BillingService:" in top_ast.content
        assert "def calculate_total" in top_ast.content

        # 3. Test Mode 2: Lexical Full-Text Search (tsvector / tsquery)
        lexical_hits = await searcher.search_lexical(repo.id, "billing workflows")
        assert len(lexical_hits) >= 1
        assert lexical_hits[0].retrieval_mode == RetrievalMode.LEXICAL

        # 4. Test Mode 3: pgvector Dense Vector Search
        query_vector = await embeddings_mock.embed_text("tax calculations")
        vector_hits = await searcher.search_vector(repo.id, query_vector)
        assert len(vector_hits) >= 1
        assert vector_hits[0].retrieval_mode == RetrievalMode.VECTOR
        assert 0.0 <= vector_hits[0].score <= 1.0

        # 5. Test Mode 4: Hybrid Reciprocal Rank Fusion (RRF)
        query = SearchQuery(
            repo_id=repo.id,
            query="calculate_total",
            limit=5,
        )
        hybrid_hits = await searcher.search_hybrid_rrf(query)
        assert len(hybrid_hits) >= 1

        top_hybrid = hybrid_hits[0]
        assert top_hybrid.retrieval_mode == RetrievalMode.HYBRID_RRF
        assert top_hybrid.score > 0.0
        # Target method must be ranked top due to AST weight (w_ast=2.0)
        assert "calculate_total" in top_hybrid.matched_symbols

        # Clean up test repo
        await session.delete(repo)
        await session.commit()
