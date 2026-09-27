"""Data schemas for code search queries, retrieval results, and ranking."""

import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RetrievalMode(StrEnum):
    """Retrieval strategies used to source code chunks."""

    AST = "ast"
    LEXICAL = "lexical"
    VECTOR = "vector"
    HYBRID_RRF = "hybrid_rrf"


class ScoredChunk(BaseModel):
    """Represents a code chunk returned from search with relevancy scoring."""

    model_config = ConfigDict(frozen=True)

    chunk_id: uuid.UUID
    repo_id: uuid.UUID
    file_path: str
    start_line: int
    end_line: int
    content: str
    score: float
    retrieval_mode: RetrievalMode
    matched_symbols: list[str] = Field(default_factory=list)


class SearchQuery(BaseModel):
    """Query parameters for repository code search."""

    model_config = ConfigDict(frozen=True)

    repo_id: uuid.UUID
    query: str
    target_file: str | None = None
    limit: int = 10
