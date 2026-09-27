"""Skeletal projection chunking engine for Python source code."""

from agent_core.services.indexing.extractor import ASTExtractor
from agent_core.services.indexing.schemas import (
    FileAnalysisResult,
    ParsedChunk,
    ParsedSymbol,
    SymbolType,
    compute_content_hash,
)


class SkeletalChunker:
    """Generates semantically enriched skeletal code chunks from parsed AST symbols."""

    def __init__(self, extractor: ASTExtractor | None = None) -> None:
        self.extractor = extractor or ASTExtractor()

    def chunk_file(self, file_path: str, source_code: str) -> FileAnalysisResult:
        """Analyzes a Python source file and breaks it into contextual skeletal chunks.

        Args:
            file_path: Relative or absolute path to the source file.
            source_code: Complete UTF-8 source code content.

        Returns:
            A FileAnalysisResult containing imports, symbols, and skeletal chunks.
        """
        imports, symbols = self.extractor.extract(source_code)
        source_lines = source_code.splitlines()
        chunks: list[ParsedChunk] = []
        imports_header = "\n".join(imports)

        class_symbols = [s for s in symbols if s.symbol_type == SymbolType.CLASS]
        standalone_funcs = [s for s in symbols if s.symbol_type == SymbolType.FUNCTION]

        # Group methods under their parent class name
        methods_by_class: dict[str, list[ParsedSymbol]] = {c.name: [] for c in class_symbols}
        for s in symbols:
            if s.symbol_type == SymbolType.METHOD and s.scope_path:
                class_name = s.scope_path.split(".")[0]
                if class_name in methods_by_class:
                    methods_by_class[class_name].append(s)

        # 1. Process Classes & Methods (Skeletal Projections)
        for cls in class_symbols:
            class_methods = methods_by_class.get(cls.name, [])

            # Emit Class Outline Chunk
            outline_lines: list[str] = []
            if imports_header:
                outline_lines.append(imports_header)
                outline_lines.append("")

            outline_lines.append(cls.signature or f"class {cls.name}:")
            if cls.docstring:
                outline_lines.append(f'    """{cls.docstring}"""')

            for m in class_methods:
                sig = m.signature or f"def {m.name}(...):"
                if not sig.endswith(":"):
                    sig += ":"
                outline_lines.append(f"    {sig} ...")

            outline_content = "\n".join(outline_lines)
            chunks.append(
                ParsedChunk(
                    file_path=file_path,
                    start_line=cls.start_line,
                    end_line=cls.end_line,
                    content=outline_content,
                    content_hash=compute_content_hash(outline_content),
                    symbols=[cls],
                )
            )

            # Emit Method Skeletal Chunks
            for target_method in class_methods:
                method_envelope_lines: list[str] = []
                if imports_header:
                    method_envelope_lines.append(imports_header)
                    method_envelope_lines.append("")

                method_envelope_lines.append(cls.signature or f"class {cls.name}:")
                if cls.docstring:
                    method_envelope_lines.append(f'    """{cls.docstring}"""')

                for m in class_methods:
                    if m.name == target_method.name:
                        target_code = "\n".join(source_lines[m.start_line - 1 : m.end_line])
                        method_envelope_lines.append(target_code)
                    else:
                        sig = m.signature or f"def {m.name}(...):"
                        if not sig.endswith(":"):
                            sig += ":"
                        method_envelope_lines.append(f"    {sig} ...")

                method_content = "\n".join(method_envelope_lines)
                chunks.append(
                    ParsedChunk(
                        file_path=file_path,
                        start_line=target_method.start_line,
                        end_line=target_method.end_line,
                        content=method_content,
                        content_hash=compute_content_hash(method_content),
                        symbols=[target_method],
                    )
                )

        # 2. Process Standalone Functions
        for func in standalone_funcs:
            func_lines: list[str] = []
            if imports_header:
                func_lines.append(imports_header)
                func_lines.append("")

            target_func_code = "\n".join(source_lines[func.start_line - 1 : func.end_line])
            func_lines.append(target_func_code)
            func_content = "\n".join(func_lines)

            chunks.append(
                ParsedChunk(
                    file_path=file_path,
                    start_line=func.start_line,
                    end_line=func.end_line,
                    content=func_content,
                    content_hash=compute_content_hash(func_content),
                    symbols=[func],
                )
            )

        # 3. Fallback: if no classes and no functions, chunk entire file
        if not chunks and source_code.strip():
            chunks.append(
                ParsedChunk(
                    file_path=file_path,
                    start_line=1,
                    end_line=max(1, len(source_lines)),
                    content=source_code,
                    content_hash=compute_content_hash(source_code),
                    symbols=[],
                )
            )

        return FileAnalysisResult(
            file_path=file_path,
            imports=imports,
            chunks=chunks,
            symbols=symbols,
        )
