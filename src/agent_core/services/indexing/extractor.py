"""AST symbol extractor and visitor for Python source code using Tree-sitter."""

from tree_sitter import Node

from agent_core.services.indexing.parser import PythonParser
from agent_core.services.indexing.schemas import ParsedSymbol, SymbolType


class ASTExtractor:
    """Extracts symbols (functions, methods, classes) and imports from a Python CST."""

    def __init__(self, parser: PythonParser | None = None) -> None:
        self.parser = parser or PythonParser()

    def extract(self, source_code: str | bytes) -> tuple[list[str], list[ParsedSymbol]]:
        """Parses source code and extracts top-level imports and all AST symbols.

        Args:
            source_code: Python source code as either a string or raw bytes.

        Returns:
            A tuple of (imports, symbols).
        """
        tree, source_bytes = self.parser.parse(source_code)
        imports: list[str] = []
        symbols: list[ParsedSymbol] = []

        self._visit_module(tree.root_node, source_bytes, imports, symbols)
        return imports, symbols

    def _visit_module(
        self,
        root_node: Node,
        source_bytes: bytes,
        imports: list[str],
        symbols: list[ParsedSymbol],
    ) -> None:
        for child in root_node.children:
            if child.type in ("import_statement", "import_from_statement"):
                import_text = self.parser.get_node_text(child, source_bytes).strip()
                if import_text:
                    imports.append(import_text)

            elif child.type == "function_definition":
                symbol = self._extract_function(
                    child, source_bytes, scope_path=None, is_method=False
                )
                if symbol:
                    symbols.append(symbol)

            elif child.type == "class_definition":
                self._extract_class(child, source_bytes, symbols)

            elif child.type == "decorated_definition":
                inner_func = self.parser.find_child_by_type(child, "function_definition")
                if inner_func:
                    symbol = self._extract_function(
                        inner_func,
                        source_bytes,
                        scope_path=None,
                        is_method=False,
                        decorator_node=child,
                    )
                    if symbol:
                        symbols.append(symbol)
                    continue

                inner_class = self.parser.find_child_by_type(child, "class_definition")
                if inner_class:
                    self._extract_class(inner_class, source_bytes, symbols, decorator_node=child)

    def _extract_function(
        self,
        node: Node,
        source_bytes: bytes,
        scope_path: str | None,
        is_method: bool,
        decorator_node: Node | None = None,
    ) -> ParsedSymbol | None:
        name_node = self.parser.find_child_by_type(node, "identifier")
        if not name_node:
            return None

        name = self.parser.get_node_text(name_node, source_bytes)
        full_scope = f"{scope_path}.{name}" if scope_path else name
        block_node = self.parser.find_child_by_type(node, "block")

        if block_node:
            signature = (
                source_bytes[node.start_byte : block_node.start_byte].decode("utf-8").strip()
            )
        else:
            signature = self.parser.get_node_text(node, source_bytes).strip()

        docstring = self._extract_docstring(block_node, source_bytes) if block_node else None

        start_node = decorator_node or node
        start_line, _ = self.parser.get_line_bounds(start_node)
        _, end_line = self.parser.get_line_bounds(node)

        return ParsedSymbol(
            name=name,
            symbol_type=SymbolType.METHOD if is_method else SymbolType.FUNCTION,
            scope_path=full_scope,
            signature=signature,
            docstring=docstring,
            start_line=start_line,
            end_line=end_line,
        )

    def _extract_class(
        self,
        node: Node,
        source_bytes: bytes,
        symbols: list[ParsedSymbol],
        decorator_node: Node | None = None,
    ) -> None:
        name_node = self.parser.find_child_by_type(node, "identifier")
        if not name_node:
            return

        class_name = self.parser.get_node_text(name_node, source_bytes)
        block_node = self.parser.find_child_by_type(node, "block")

        if block_node:
            signature = (
                source_bytes[node.start_byte : block_node.start_byte].decode("utf-8").strip()
            )
        else:
            signature = self.parser.get_node_text(node, source_bytes).strip()

        docstring = self._extract_docstring(block_node, source_bytes) if block_node else None

        start_node = decorator_node or node
        start_line, _ = self.parser.get_line_bounds(start_node)
        _, end_line = self.parser.get_line_bounds(node)

        symbols.append(
            ParsedSymbol(
                name=class_name,
                symbol_type=SymbolType.CLASS,
                scope_path=class_name,
                signature=signature,
                docstring=docstring,
                start_line=start_line,
                end_line=end_line,
            )
        )

        if block_node:
            for child in block_node.children:
                if child.type == "function_definition":
                    method_symbol = self._extract_function(
                        child, source_bytes, scope_path=class_name, is_method=True
                    )
                    if method_symbol:
                        symbols.append(method_symbol)
                elif child.type == "decorated_definition":
                    inner_func = self.parser.find_child_by_type(child, "function_definition")
                    if inner_func:
                        method_symbol = self._extract_function(
                            inner_func,
                            source_bytes,
                            scope_path=class_name,
                            is_method=True,
                            decorator_node=child,
                        )
                        if method_symbol:
                            symbols.append(method_symbol)

    def _extract_docstring(self, block_node: Node, source_bytes: bytes) -> str | None:
        for child in block_node.children:
            if child.type == "expression_statement":
                for subchild in child.children:
                    if subchild.type == "string":
                        raw_text = self.parser.get_node_text(subchild, source_bytes).strip()
                        if raw_text.startswith(('"""', "'''")):
                            return raw_text[3:-3].strip()
                        if raw_text.startswith(('"', "'")):
                            return raw_text[1:-1].strip()
                        return raw_text
                break
            elif child.type in ("comment", "\n"):
                continue
            else:
                break
        return None
