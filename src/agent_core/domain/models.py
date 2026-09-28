import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agent_core.infrastructure.db.base import BaseEntity


class Repository(BaseEntity):
    __tablename__ = "repositories"

    github_repo_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(100), default="main", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # 1-to-many relationships: One repo has many chunks
    chunks: Mapped[list["CodeChunk"]] = relationship(
        "CodeChunk", back_populates="repository", cascade="all, delete-orphan"
    )
    approval_requests: Mapped[list["ApprovalRequest"]] = relationship(
        "ApprovalRequest", back_populates="repository", cascade="all, delete-orphan"
    )


class CodeChunk(BaseEntity):
    __tablename__ = "code_chunks"

    repo_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    file_path: Mapped[str] = mapped_column(String(1024), index=True, nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)

    # Relationships
    repository: Mapped["Repository"] = relationship("Repository", back_populates="chunks")
    symbols: Mapped[list["CodeSymbol"]] = relationship(
        "CodeSymbol", back_populates="chunk", cascade="all, delete-orphan"
    )


class CodeSymbol(BaseEntity):
    __tablename__ = "code_symbols"

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("code_chunks.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    symbol_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    scope_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    docstring: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationship
    chunk: Mapped["CodeChunk"] = relationship("CodeChunk", back_populates="symbols")


class ApprovalRequest(BaseEntity):
    __tablename__ = "approval_requests"

    repo_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    thread_id: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    issue_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    base_commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    target_branch: Mapped[str] = mapped_column(String(100), default="main", nullable=False)
    feature_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    remediation_plan: Mapped[str] = mapped_column(Text, nullable=False)
    patch_diff: Mapped[str] = mapped_column(Text, nullable=False)
    test_summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending", index=True, nullable=False)
    reviewer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Relationship
    repository: Mapped["Repository"] = relationship(
        "Repository", back_populates="approval_requests"
    )
