import uuid  # noqa: F401

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from agent_core.domain.models import CodeChunk, CodeSymbol, Repository
from agent_core.infrastructure.db.session import get_db_session


async def test_full_database_lifecycle() -> None:
    # 1. Borrow a session from our session manager
    async for session in get_db_session():
        # Create a test repository
        repo = Repository(
            github_repo_id=999999,
            owner="test-owner",
            name="test-repo",
            default_branch="main",
            is_active=True,
        )
        session.add(repo)
        await session.flush()  # Flush sends SQL to generate foreign key ID without committing yet

        # Create a test code chunk with a mock 1536-dimension vector
        chunk = CodeChunk(
            repo_id=repo.id,
            file_path="src/main.py",
            start_line=1,
            end_line=10,
            content="def hello_world(): print('hello')",
            content_hash="mock_sha256_hash_value_12345678",
            embedding=[0.05] * 1536,  # 1536 float values for pgvector!
        )
        session.add(chunk)
        await session.flush()

        # Create an AST symbol linked to the chunk
        symbol = CodeSymbol(
            chunk_id=chunk.id,
            name="hello_world",
            symbol_type="function",
            scope_path="hello_world",
            signature="def hello_world()",
        )
        session.add(symbol)
        await session.flush()

        # 2. Query the data back from PostgreSQL
        stmt = (
            select(Repository)
            .where(Repository.github_repo_id == 999999)
            .options(selectinload(Repository.chunks).selectinload(CodeChunk.symbols))
        )
        result = await session.scalar(stmt)
        assert result is not None
        assert result.name == "test-repo"
        assert len(result.chunks) == 1
        assert result.chunks[0].file_path == "src/main.py"
        assert len(result.chunks[0].symbols) == 1
        assert result.chunks[0].symbols[0].name == "hello_world"

        # 3. Test Cascading Deletes
        await session.delete(result)
        await session.flush()

        # Verify chunks and symbols were automatically deleted
        chunks_check = await session.scalars(select(CodeChunk).where(CodeChunk.repo_id == repo.id))
        assert len(chunks_check.all()) == 0
