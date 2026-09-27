"""Domain schemas for problem formulation, issue entity extraction, and bug localization."""

import uuid

from pydantic import BaseModel, ConfigDict, Field

from agent_core.services.retrieval.schemas import ScoredChunk


class IssuePayload(BaseModel):
    """Raw issue report ingested from GitHub or developer submission."""

    model_config = ConfigDict(frozen=True)

    issue_id: int
    title: str
    body: str
    repo_id: uuid.UUID


class ExtractedEntities(BaseModel):
    """Structured technical entities extracted from issue description and stack traces."""

    model_config = ConfigDict(frozen=True)

    symbols: list[str] = Field(default_factory=list)
    file_paths: list[str] = Field(default_factory=list)
    line_numbers: list[int] = Field(default_factory=list)
    error_types: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class CandidateFile(BaseModel):
    """A ranked repository file suspected of housing the bug."""

    model_config = ConfigDict(frozen=True)

    file_path: str
    confidence_score: float  # Normalized 0.0 to 1.0
    rationale: str
    relevant_chunks: list[ScoredChunk] = Field(default_factory=list)
    matched_symbols: list[str] = Field(default_factory=list)


class LocalizationResult(BaseModel):
    """Enriched diagnostic payload ready for remediation planning."""

    model_config = ConfigDict(frozen=True)

    repo_id: uuid.UUID
    issue_id: int
    problem_statement: str
    extracted_entities: ExtractedEntities
    candidate_files: list[CandidateFile] = Field(default_factory=list)
    root_cause_hypothesis: str | None = None
