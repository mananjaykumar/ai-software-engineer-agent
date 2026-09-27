"""Tri-modal search engine combining AST symbol, lexical full-text, and dense vector search."""

import asyncio
import uuid
from collections import defaultdict

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_core.domain.models import CodeChunk, CodeSymbol
from agent_core.services.retrieval.embeddings import OpenAIEmbeddingsClient
from agent_core.services.retrieval.schemas import RetrievalMode, ScoredChunk, SearchQuery


class TriModalSearchEngine:
    """Orchestrates AST exact, lexical full-text, and dense vector code retrieval."""

    def __init__(
        self,
        session: AsyncSession,
        embeddings_client: OpenAIEmbeddingsClient | None = None,
    ) -> None:
        self.session = session
        self.embeddings_client = embeddings_client or OpenAIEmbeddingsClient()

    async def search_ast(
        self,
        repo_id: uuid.UUID,
        symbol_query: str,
        target_file: str | None = None,
        limit: int = 10,
    ) -> list[ScoredChunk]:
        """Finds code chunks matching exact or hierarchical AST symbols."""
        clean_query = symbol_query.strip()
        if not clean_query:
            return []

        # Match exact symbol name or scope_path (e.g. OrderService.calculate_discount)
        stmt = (
            select(CodeChunk, CodeSymbol.name)
            .join(CodeChunk.symbols)
            .where(
                CodeChunk.repo_id == repo_id,
                or_(
                    CodeSymbol.name == clean_query,
                    CodeSymbol.scope_path == clean_query,
                    CodeSymbol.name.ilike(f"%{clean_query}%"),
                ),
            )
        )

        if target_file:
            stmt = stmt.where(CodeChunk.file_path == target_file)

        stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        rows = result.all()

        scored_chunks: list[ScoredChunk] = []
        for chunk, matched_sym in rows:
            is_exact = matched_sym == clean_query
            scored_chunks.append(
                ScoredChunk(
                    chunk_id=chunk.id,
                    repo_id=chunk.repo_id,
                    file_path=chunk.file_path,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    content=chunk.content,
                    score=1.0 if is_exact else 0.8,
                    retrieval_mode=RetrievalMode.AST,
                    matched_symbols=[matched_sym],
                )
            )

        return scored_chunks

    async def search_lexical(
        self,
        repo_id: uuid.UUID,
        query_text: str,
        target_file: str | None = None,
        limit: int = 10,
    ) -> list[ScoredChunk]:
        """Performs PostgreSQL full-text search (tsvector/tsquery) over chunk contents."""
        clean_query = query_text.strip()
        if not clean_query:
            return []

        tsv = func.to_tsvector("english", CodeChunk.content)
        tsq = func.plainto_tsquery("english", clean_query)
        rank = func.ts_rank_cd(tsv, tsq).label("rank")

        stmt = (
            select(CodeChunk, rank)
            .where(
                CodeChunk.repo_id == repo_id,
                or_(
                    tsv.op("@@")(tsq),
                    CodeChunk.content.ilike(f"%{clean_query}%"),
                ),
            )
            .order_by(rank.desc())
            .limit(limit)
        )

        if target_file:
            stmt = stmt.where(CodeChunk.file_path == target_file)

        result = await self.session.execute(stmt)
        rows = result.all()

        return [
            ScoredChunk(
                chunk_id=chunk.id,
                repo_id=chunk.repo_id,
                file_path=chunk.file_path,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                content=chunk.content,
                score=float(rank_score) if rank_score is not None else 0.5,
                retrieval_mode=RetrievalMode.LEXICAL,
                matched_symbols=[],
            )
            for chunk, rank_score in rows
        ]

    async def search_vector(
        self,
        repo_id: uuid.UUID,
        query_vector: list[float],
        target_file: str | None = None,
        limit: int = 10,
    ) -> list[ScoredChunk]:
        """Performs pgvector cosine distance search on code chunks."""
        distance = CodeChunk.embedding.cosine_distance(query_vector).label("distance")

        stmt = (
            select(CodeChunk, distance)
            .where(
                CodeChunk.repo_id == repo_id,
                CodeChunk.embedding.is_not(None),
            )
            .order_by(distance.asc())
            .limit(limit)
        )

        if target_file:
            stmt = stmt.where(CodeChunk.file_path == target_file)

        result = await self.session.execute(stmt)
        rows = result.all()

        return [
            ScoredChunk(
                chunk_id=chunk.id,
                repo_id=chunk.repo_id,
                file_path=chunk.file_path,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                content=chunk.content,
                score=max(0.0, 1.0 - float(dist)),
                retrieval_mode=RetrievalMode.VECTOR,
                matched_symbols=[],
            )
            for chunk, dist in rows
        ]

    async def search_hybrid_rrf(
        self,
        query: SearchQuery,
        k: int = 60,
        weight_ast: float = 2.0,
        weight_lexical: float = 1.0,
        weight_vector: float = 1.0,
    ) -> list[ScoredChunk]:
        """Runs AST, lexical, and vector retrieval concurrently and merges via Reciprocal Rank
        Fusion."""
        # 1. Generate query embedding
        query_vector = await self.embeddings_client.embed_text(query.query)

        # 2. Execute all three modes concurrently
        ast_task = self.search_ast(
            repo_id=query.repo_id,
            symbol_query=query.query,
            target_file=query.target_file,
            limit=query.limit,
        )
        lexical_task = self.search_lexical(
            repo_id=query.repo_id,
            query_text=query.query,
            target_file=query.target_file,
            limit=query.limit,
        )
        vector_task = self.search_vector(
            repo_id=query.repo_id,
            query_vector=query_vector,
            target_file=query.target_file,
            limit=query.limit,
        )

        ast_results, lexical_results, vector_results = await asyncio.gather(
            ast_task, lexical_task, vector_task
        )

        # 3. Reciprocal Rank Fusion (RRF)
        rrf_scores: dict[uuid.UUID, float] = defaultdict(float)
        chunk_map: dict[uuid.UUID, ScoredChunk] = {}
        matched_symbols_map: dict[uuid.UUID, set[str]] = defaultdict(set)

        for rank, chunk in enumerate(ast_results, start=1):
            rrf_scores[chunk.chunk_id] += weight_ast / (k + rank)
            chunk_map[chunk.chunk_id] = chunk
            matched_symbols_map[chunk.chunk_id].update(chunk.matched_symbols)

        for rank, chunk in enumerate(lexical_results, start=1):
            rrf_scores[chunk.chunk_id] += weight_lexical / (k + rank)
            chunk_map[chunk.chunk_id] = chunk

        for rank, chunk in enumerate(vector_results, start=1):
            rrf_scores[chunk.chunk_id] += weight_vector / (k + rank)
            chunk_map[chunk.chunk_id] = chunk

        # 4. Sort candidates by unified RRF score descending
        sorted_chunk_ids = sorted(
            rrf_scores.keys(),
            key=lambda cid: rrf_scores[cid],
            reverse=True,
        )

        final_chunks: list[ScoredChunk] = []
        for cid in sorted_chunk_ids[: query.limit]:
            original = chunk_map[cid]
            final_chunks.append(
                ScoredChunk(
                    chunk_id=original.chunk_id,
                    repo_id=original.repo_id,
                    file_path=original.file_path,
                    start_line=original.start_line,
                    end_line=original.end_line,
                    content=original.content,
                    score=round(rrf_scores[cid], 5),
                    retrieval_mode=RetrievalMode.HYBRID_RRF,
                    matched_symbols=sorted(list(matched_symbols_map[cid])),
                )
            )

        return final_chunks
