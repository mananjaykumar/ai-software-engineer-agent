"""Bug localization service synthesizing entity extraction and tri-modal retrieval."""

from collections import defaultdict

from agent_core.agent.analyzer import IssueAnalyzer
from agent_core.domain.schemas.localization import (
    CandidateFile,
    ExtractedEntities,
    IssuePayload,
    LocalizationResult,
)
from agent_core.services.retrieval.schemas import ScoredChunk, SearchQuery
from agent_core.services.retrieval.search import TriModalSearchEngine


class BugLocalizer:
    """Executes multi-query retrieval and scores candidate files for bug localization."""

    def __init__(
        self,
        search_engine: TriModalSearchEngine,
        analyzer: IssueAnalyzer | None = None,
    ) -> None:
        self.search_engine = search_engine
        self.analyzer = analyzer or IssueAnalyzer()

    async def localize_bug(
        self,
        payload: IssuePayload,
        max_candidate_files: int = 5,
        chunks_per_query: int = 10,
    ) -> LocalizationResult:
        """Analyzes an issue, executes hybrid retrieval across formulated queries,

        and aggregates file-level confidence scores.
        """
        entities = self.analyzer.analyze_issue(payload)
        queries = self.analyzer.formulate_search_queries(payload, entities)

        # Aggregate retrieved chunks across all formulated queries by file path
        file_chunks_map: dict[str, list[ScoredChunk]] = defaultdict(list)
        seen_chunk_ids: set[str] = set()

        for query_str in queries:
            search_query = SearchQuery(
                repo_id=payload.repo_id,
                query=query_str,
                limit=chunks_per_query,
            )
            results = await self.search_engine.search_hybrid_rrf(search_query)
            for chunk in results:
                chunk_id_str = str(chunk.chunk_id)
                if chunk_id_str not in seen_chunk_ids:
                    seen_chunk_ids.add(chunk_id_str)
                    file_chunks_map[chunk.file_path].append(chunk)

        # Score and rank candidate files
        scored_candidates: list[CandidateFile] = []
        for file_path, chunks in file_chunks_map.items():
            candidate = self._score_candidate_file(file_path, chunks, entities)
            scored_candidates.append(candidate)

        # Sort descending by confidence score
        scored_candidates.sort(key=lambda c: c.confidence_score, reverse=True)
        top_candidates = scored_candidates[:max_candidate_files]

        # Synthesize diagnostic root-cause hypothesis
        hypothesis = self._synthesize_hypothesis(payload, entities, top_candidates)

        return LocalizationResult(
            repo_id=payload.repo_id,
            issue_id=payload.issue_id,
            problem_statement=payload.title,
            extracted_entities=entities,
            candidate_files=top_candidates,
            root_cause_hypothesis=hypothesis,
        )

    def _score_candidate_file(
        self,
        file_path: str,
        chunks: list[ScoredChunk],
        entities: ExtractedEntities,
    ) -> CandidateFile:
        """Calculates file-level confidence score using RRF chunk scores and entity boosts."""
        # Base confidence from top 3 chunks (sum of RRF scores, normalized)
        top_chunks = sorted(chunks, key=lambda c: c.score, reverse=True)[:3]
        base_score = sum(c.score for c in top_chunks)
        confidence = min(0.60, base_score * 5.0)

        # Collect all matched symbols across chunks
        matched_symbols: set[str] = set()
        for c in chunks:
            for s in c.matched_symbols:
                matched_symbols.add(s)

        # Boost 1: File appears in stack trace / parsed file paths
        explicit_file_hit = any(
            fp in file_path or file_path.endswith(fp) for fp in entities.file_paths
        )
        if explicit_file_hit:
            confidence = min(1.0, confidence + 0.35)

        # Boost 2: Exact symbol matches found in this file
        symbol_hit = bool(matched_symbols.intersection(set(entities.symbols)))
        if symbol_hit:
            confidence = min(1.0, confidence + 0.25)

        reasons: list[str] = []
        if explicit_file_hit:
            reasons.append("Referenced directly in stack trace")
        if symbol_hit:
            reasons.append(f"Contains matching symbols: {', '.join(sorted(list(matched_symbols)))}")
        reasons.append(f"{len(chunks)} relevant skeletal chunks retrieved")

        sorted_chunks = sorted(chunks, key=lambda c: c.score, reverse=True)

        return CandidateFile(
            file_path=file_path,
            confidence_score=round(confidence, 3),
            rationale="; ".join(reasons),
            relevant_chunks=sorted_chunks,
            matched_symbols=sorted(list(matched_symbols)),
        )

    def _synthesize_hypothesis(
        self,
        payload: IssuePayload,
        entities: ExtractedEntities,
        top_candidates: list[CandidateFile],
    ) -> str:
        """Generates an initial root-cause diagnosis hypothesis for remediation planning."""
        if not top_candidates:
            return (
                f"No candidate files identified with sufficient confidence for issue "
                f"#{payload.issue_id}."
            )

        primary = top_candidates[0]
        # Prefer symbols matched within the top candidate file
        symbol = (
            primary.matched_symbols[0]
            if primary.matched_symbols
            else (entities.symbols[0] if entities.symbols else None)
        )
        line = entities.line_numbers[0] if entities.line_numbers else None

        if entities.error_types and symbol and line:
            return (
                f"Exception '{entities.error_types[0]}' likely triggered in "
                f"'{primary.file_path}' inside '{symbol}' "
                f"around line {line}."
            )
        if entities.error_types and symbol:
            return (
                f"Exception '{entities.error_types[0]}' likely originating in "
                f"'{primary.file_path}' related to symbol '{symbol}'."
            )
        return f"Suspected defect in '{primary.file_path}' correlating with '{payload.title}'."
