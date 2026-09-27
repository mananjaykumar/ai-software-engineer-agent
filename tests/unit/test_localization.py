"""Unit tests for the Problem Formulation and Bug Localization Agent."""

import uuid
from unittest.mock import AsyncMock

import pytest

from agent_core.agent.analyzer import IssueAnalyzer
from agent_core.agent.localizer import BugLocalizer
from agent_core.domain.schemas.localization import IssuePayload
from agent_core.services.retrieval.schemas import RetrievalMode, ScoredChunk
from agent_core.services.retrieval.search import TriModalSearchEngine


@pytest.fixture
def analyzer() -> IssueAnalyzer:
    return IssueAnalyzer()


SAMPLE_ISSUE_TEXT = """
When calling calculate_total with None, the checkout process crashes with:
Traceback (most recent call last):
  File "services/billing.py", line 42, in calculate_total
    return amount * (Decimal("1.0") + self.tax_rate)
TypeError: unsupported operand type(s) for *: 'NoneType' and 'Decimal'

Also related to `apply_discount` and OrderManager.process_order().
"""


def test_analyzer_extract_stack_trace(analyzer: IssueAnalyzer) -> None:
    payload = IssuePayload(
        issue_id=142,
        title="Checkout calculation crash with NoneType",
        body=SAMPLE_ISSUE_TEXT,
        repo_id=uuid.uuid4(),
    )
    entities = analyzer.analyze_issue(payload)

    # 1. Verify Stack Trace File Paths & Line Numbers
    assert "services/billing.py" in entities.file_paths
    assert 42 in entities.line_numbers

    # 2. Verify Symbols Extracted
    assert "calculate_total" in entities.symbols
    assert "apply_discount" in entities.symbols
    assert "OrderManager.process_order" in entities.symbols
    assert "process_order" in entities.symbols

    # 3. Verify Error Type Extracted
    assert "TypeError" in entities.error_types

    # 4. Verify Keywords
    assert "checkout" in entities.keywords
    assert "calculation" in entities.keywords


def test_analyzer_formulate_queries(analyzer: IssueAnalyzer) -> None:
    payload = IssuePayload(
        issue_id=142,
        title="Checkout calculation crash",
        body=SAMPLE_ISSUE_TEXT,
        repo_id=uuid.uuid4(),
    )
    entities = analyzer.analyze_issue(payload)
    queries = analyzer.formulate_search_queries(payload, entities)

    # Must contain exact symbols first
    assert "calculate_total" in queries
    # Must contain error combination query
    assert any("TypeError" in q for q in queries)
    # Must contain the clean title query
    assert "Checkout calculation crash" in queries


@pytest.mark.asyncio
async def test_bug_localizer_scoring_and_ranking(analyzer: IssueAnalyzer) -> None:
    repo_id = uuid.uuid4()
    payload = IssuePayload(
        issue_id=142,
        title="Checkout calculation crash with NoneType",
        body=SAMPLE_ISSUE_TEXT,
        repo_id=repo_id,
    )

    # Mock TriModalSearchEngine returning chunks from 2 files
    mock_search_engine = AsyncMock(spec=TriModalSearchEngine)

    billing_chunk = ScoredChunk(
        chunk_id=uuid.uuid4(),
        repo_id=repo_id,
        file_path="services/billing.py",
        start_line=30,
        end_line=50,
        content="def calculate_total(amount): ...",
        score=0.045,  # RRF score
        retrieval_mode=RetrievalMode.HYBRID_RRF,
        matched_symbols=["calculate_total"],
    )

    utils_chunk = ScoredChunk(
        chunk_id=uuid.uuid4(),
        repo_id=repo_id,
        file_path="utils/helpers.py",
        start_line=1,
        end_line=20,
        content="def format_currency(val): ...",
        score=0.015,
        retrieval_mode=RetrievalMode.HYBRID_RRF,
        matched_symbols=[],
    )

    mock_search_engine.search_hybrid_rrf.return_value = [billing_chunk, utils_chunk]

    localizer = BugLocalizer(search_engine=mock_search_engine, analyzer=analyzer)
    result = await localizer.localize_bug(payload=payload, max_candidate_files=2)

    # 1. Verify Localization Result structure
    assert result.issue_id == 142
    assert len(result.candidate_files) == 2

    # 2. Verify Ranking: services/billing.py must rank #1 due to stack trace and symbol boosts
    top_file = result.candidate_files[0]
    assert top_file.file_path == "services/billing.py"
    assert top_file.confidence_score > result.candidate_files[1].confidence_score
    assert "Referenced directly in stack trace" in top_file.rationale
    assert "calculate_total" in top_file.matched_symbols

    # 3. Verify Diagnostic Hypothesis
    assert result.root_cause_hypothesis is not None
    assert "TypeError" in result.root_cause_hypothesis
    assert "services/billing.py" in result.root_cause_hypothesis
    assert "calculate_total" in result.root_cause_hypothesis
    assert "42" in result.root_cause_hypothesis
