"""Issue analyzer and entity extractor for parsing stack traces and bug reports."""

import re

from agent_core.domain.schemas.localization import ExtractedEntities, IssuePayload


class IssueAnalyzer:
    """Extracts code symbols, file paths, line numbers, error types, and keywords from issues."""

    # Regex to capture Python stack traces: File "path/to/file.py", line 42, in func_name
    STACK_TRACE_PATTERN = re.compile(
        r'File ["\'](?P<file>[^"\']+)["\'], line (?P<line>\d+), in (?P<func>\w+)'
    )

    # Regex to capture standard Python Exception classes (e.g. TypeError, ValueError)
    ERROR_TYPE_PATTERN = re.compile(
        r"\b([A-Z]\w*(?:Error|Exception)|AssertionError|HTTPException)\b"
    )

    # Regex to capture backticked code tokens: `symbol` or `Class.method`
    BACKTICK_PATTERN = re.compile(r"`([a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)?)`")

    # Regex to capture dotted identifiers: ClassName.method_name
    DOTTED_SYMBOL_PATTERN = re.compile(r"\b([A-Z]\w*\.[a-zA-Z_]\w*)\b")

    # Regex to capture explicit function calls: function_name()
    FUNCTION_CALL_PATTERN = re.compile(r"\b([a-zA-Z_]\w*)\(\)")

    # Stopwords to filter out when generating search keywords
    STOPWORDS = {
        "a",
        "an",
        "the",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "and",
        "or",
        "not",
        "is",
        "are",
        "was",
        "were",
        "be",
        "this",
        "that",
        "it",
        "when",
        "how",
        "why",
        "what",
        "where",
        "if",
        "then",
        "else",
        "we",
        "i",
        "my",
        "app",
        "crash",
        "crashes",
        "error",
        "issue",
        "bug",
        "failing",
        "failed",
        "fails",
    }

    def analyze_issue(self, payload: IssuePayload) -> ExtractedEntities:
        """Parses the issue title and body to extract all technical entities."""
        full_text = f"{payload.title}\n{payload.body}"

        symbols: set[str] = set()
        file_paths: set[str] = set()
        line_numbers: set[int] = set()
        error_types: set[str] = set()

        # 1. Extract from stack traces
        for match in self.STACK_TRACE_PATTERN.finditer(full_text):
            file_paths.add(match.group("file"))
            line_numbers.add(int(match.group("line")))
            symbols.add(match.group("func"))

        # 2. Extract error types
        for err in self.ERROR_TYPE_PATTERN.findall(full_text):
            error_types.add(err)

        # 3. Extract backticked symbols
        for sym in self.BACKTICK_PATTERN.findall(full_text):
            symbols.add(sym)

        # 4. Extract dotted symbols (e.g. OrderService.calculate_total)
        for dotted in self.DOTTED_SYMBOL_PATTERN.findall(full_text):
            symbols.add(dotted)

        # 5. Extract function calls foo()
        for func in self.FUNCTION_CALL_PATTERN.findall(full_text):
            symbols.add(func)

        # 6. Extract meaningful keywords
        words = re.findall(r"\b[a-zA-Z_]{3,}\b", payload.title.lower())
        keywords = [w for w in words if w not in self.STOPWORDS]

        return ExtractedEntities(
            symbols=sorted(list(symbols)),
            file_paths=sorted(list(file_paths)),
            line_numbers=sorted(list(line_numbers)),
            error_types=sorted(list(error_types)),
            keywords=sorted(list(set(keywords))),
        )

    def formulate_search_queries(
        self,
        payload: IssuePayload,
        entities: ExtractedEntities,
    ) -> list[str]:
        """Formulates a prioritized list of search queries from extracted entities."""
        queries: list[str] = []

        # 1. Exact symbol queries (Highest Priority)
        for sym in entities.symbols:
            if sym not in queries:
                queries.append(sym)

        # 2. Error type + Symbol combination
        if entities.error_types and entities.symbols:
            combo = f"{entities.symbols[0]} {entities.error_types[0]}"
            if combo not in queries:
                queries.append(combo)

        # 3. Natural language issue title (Semantic Query)
        clean_title = payload.title.strip()
        if clean_title and clean_title not in queries:
            queries.append(clean_title)

        return queries
