"""Repository ingestion service coordinating AST chunking, embeddings, and persistence."""

import uuid

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_core.domain.models import CodeChunk, CodeSymbol
from agent_core.services.indexing.chunker import SkeletalChunker
from agent_core.services.retrieval.embeddings import OpenAIEmbeddingsClient


class IngestionResult(BaseModel):
    """Metrics returned following repository file ingestion."""

    model_config = ConfigDict(frozen=True)

    repo_id: uuid.UUID
    files_processed: int
    chunks_created: int
    chunks_skipped: int
    symbols_created: int


class RepositoryIngestionService:
    """Coordinates AST parsing, skeletal chunking, embedding generation, and DB storage."""

    def __init__(
        self,
        session: AsyncSession,
        chunker: SkeletalChunker | None = None,
        embeddings_client: OpenAIEmbeddingsClient | None = None,
    ) -> None:
        self.session = session
        self.chunker = chunker or SkeletalChunker()
        self.embeddings_client = embeddings_client or OpenAIEmbeddingsClient()

    async def index_file(
        self,
        repo_id: uuid.UUID,
        file_path: str,
        source_code: str,
    ) -> IngestionResult:
        """Parses, deduplicates, embeds, and persists chunks for a single source file."""
        analysis = self.chunker.chunk_file(file_path=file_path, source_code=source_code)
        if not analysis.chunks:
            return IngestionResult(
                repo_id=repo_id,
                files_processed=1,
                chunks_created=0,
                chunks_skipped=0,
                symbols_created=0,
            )

        # 1. Query existing content hashes in DB for this repository to skip unchanged chunks
        chunk_hashes = [c.content_hash for c in analysis.chunks]
        stmt = select(CodeChunk.content_hash).where(
            CodeChunk.repo_id == repo_id,
            CodeChunk.content_hash.in_(chunk_hashes),
        )
        existing_result = await self.session.execute(stmt)
        existing_hashes = set(existing_result.scalars().all())

        new_chunks = [c for c in analysis.chunks if c.content_hash not in existing_hashes]
        skipped_count = len(analysis.chunks) - len(new_chunks)

        if not new_chunks:
            return IngestionResult(
                repo_id=repo_id,
                files_processed=1,
                chunks_created=0,
                chunks_skipped=skipped_count,
                symbols_created=0,
            )

        # 2. Generate embeddings in batch for all new chunks
        chunk_contents = [c.content for c in new_chunks]
        embeddings = await self.embeddings_client.embed_batch(chunk_contents)

        # 3. Persist CodeChunks and CodeSymbols atomically
        total_symbols_created = 0
        for parsed_chunk, emb in zip(new_chunks, embeddings, strict=True):
            db_chunk = CodeChunk(
                repo_id=repo_id,
                file_path=parsed_chunk.file_path,
                start_line=parsed_chunk.start_line,
                end_line=parsed_chunk.end_line,
                content=parsed_chunk.content,
                content_hash=parsed_chunk.content_hash,
                embedding=emb,
            )
            self.session.add(db_chunk)
            await self.session.flush()

            for sym in parsed_chunk.symbols:
                db_symbol = CodeSymbol(
                    chunk_id=db_chunk.id,
                    name=sym.name,
                    symbol_type=sym.symbol_type.value,
                    scope_path=sym.scope_path,
                    signature=sym.signature,
                    docstring=sym.docstring,
                )
                self.session.add(db_symbol)
                total_symbols_created += 1

        await self.session.commit()

        return IngestionResult(
            repo_id=repo_id,
            files_processed=1,
            chunks_created=len(new_chunks),
            chunks_skipped=skipped_count,
            symbols_created=total_symbols_created,
        )

    async def index_repository_files(
        self,
        repo_id: uuid.UUID,
        file_map: dict[str, str],
    ) -> IngestionResult:
        """Indexes multiple files for a repository."""
        total_chunks_created = 0
        total_chunks_skipped = 0
        total_symbols_created = 0

        for file_path, source_code in file_map.items():
            res = await self.index_file(repo_id, file_path, source_code)
            total_chunks_created += res.chunks_created
            total_chunks_skipped += res.chunks_skipped
            total_symbols_created += res.symbols_created

        return IngestionResult(
            repo_id=repo_id,
            files_processed=len(file_map),
            chunks_created=total_chunks_created,
            chunks_skipped=total_chunks_skipped,
            symbols_created=total_symbols_created,
        )
