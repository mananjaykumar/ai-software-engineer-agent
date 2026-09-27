"""Tree-sitter parser wrapper and Concrete Syntax Tree (CST) inspection helpers."""

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser, Tree


class PythonParser:
    """Thread-safe parser wrapper for Python source code using Tree-sitter."""

    def __init__(self) -> None:
        self._language = Language(tspython.language())
        self._parser = Parser(self._language)

    def parse(self, source_code: str | bytes) -> tuple[Tree, bytes]:
        """Parses Python source code into a Tree-sitter Concrete Syntax Tree (CST).

        Args:
            source_code: Python source code as either a UTF-8 string or raw bytes.

        Returns:
            A tuple of (Tree, source_bytes) to ensure byte offsets line up precisely
            during node extraction.
        """
        if isinstance(source_code, str):
            source_bytes = source_code.encode("utf-8")
        else:
            source_bytes = source_code

        tree = self._parser.parse(source_bytes)
        return tree, source_bytes

    @staticmethod
    def get_node_text(node: Node, source_bytes: bytes) -> str:
        """Extracts and decodes the exact text slice corresponding to a CST node."""
        return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    @staticmethod
    def get_line_bounds(node: Node) -> tuple[int, int]:
        """Returns 1-indexed (start_line, end_line) bounds for a CST node."""
        return node.start_point.row + 1, node.end_point.row + 1

    @staticmethod
    def find_child_by_type(node: Node, child_type: str) -> Node | None:
        """Finds the first direct child matching a specified Tree-sitter node type."""
        for child in node.children:
            if child.type == child_type:
                return child
        return None

    @staticmethod
    def find_children_by_type(node: Node, child_type: str) -> list[Node]:
        """Finds all direct children matching a specified Tree-sitter node type."""
        return [child for child in node.children if child.type == child_type]
