import hashlib
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SymbolType(StrEnum):
    """Enumeration of AST symbol types recognized by the indexing engine."""

    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"


def compute_content_hash(content: str) -> str:
    """Computes a deterministic SHA-256 hex digest for code content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class ParsedSymbol(BaseModel):
    """Represents a code symbol (class, function, method) extracted from the AST."""

    model_config = ConfigDict(frozen=True)

    name: str
    symbol_type: SymbolType
    scope_path: str | None = None
    signature: str | None = None
    docstring: str | None = None
    start_line: int
    end_line: int


class ParsedChunk(BaseModel):
    """Represents an indexed chunk of code (or skeletal projection envelope)."""

    model_config = ConfigDict(frozen=True)

    file_path: str
    start_line: int
    end_line: int
    content: str
    content_hash: str
    symbols: list[ParsedSymbol] = Field(default_factory=list)


class FileAnalysisResult(BaseModel):
    """Aggregated AST extraction and chunking result for a single source file."""

    model_config = ConfigDict(frozen=True)

    file_path: str
    imports: list[str] = Field(default_factory=list)
    chunks: list[ParsedChunk] = Field(default_factory=list)
    symbols: list[ParsedSymbol] = Field(default_factory=list)
