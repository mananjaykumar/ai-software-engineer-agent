"""Unit tests for Tree-sitter AST parsing, symbol extraction, and skeletal chunking."""

import pytest

from agent_core.services.indexing.chunker import SkeletalChunker
from agent_core.services.indexing.extractor import ASTExtractor
from agent_core.services.indexing.parser import PythonParser
from agent_core.services.indexing.schemas import SymbolType, compute_content_hash


@pytest.fixture
def parser() -> PythonParser:
    return PythonParser()


@pytest.fixture
def extractor(parser: PythonParser) -> ASTExtractor:
    return ASTExtractor(parser=parser)


@pytest.fixture
def chunker(extractor: ASTExtractor) -> SkeletalChunker:
    return SkeletalChunker(extractor=extractor)


SAMPLE_PYTHON_CODE = '''import os
from typing import Optional, List

@decorator
class OrderManager:
    """Manages order lifecycles and transactions."""

    def __init__(self, db_url: str):
        self.db_url = db_url

    @property
    def is_connected(self) -> bool:
        """Returns True if database connection is active."""
        return True

    async def process_order(self, order_id: str, items: List[str]) -> bool:
        """Processes an order asynchronously."""
        if not items:
            return False
        return True

def calculate_tax(subtotal: float, rate: float = 0.05) -> float:
    """Calculates tax for a given subtotal."""
    return subtotal * rate
'''


def test_python_parser_basic(parser: PythonParser) -> None:
    code = "def add(a: int, b: int) -> int:\n    return a + b\n"
    tree, source_bytes = parser.parse(code)

    assert tree.root_node.type == "module"
    func_node = tree.root_node.children[0]
    assert func_node.type == "function_definition"

    text = parser.get_node_text(func_node, source_bytes)
    assert "def add(a: int, b: int) -> int:" in text

    start_line, end_line = parser.get_line_bounds(func_node)
    assert start_line == 1
    assert end_line == 2


def test_ast_extractor_symbols_and_imports(extractor: ASTExtractor) -> None:
    imports, symbols = extractor.extract(SAMPLE_PYTHON_CODE)

    # 1. Verify Imports
    assert "import os" in imports
    assert "from typing import Optional, List" in imports

    # 2. Verify Symbols Count (OrderManager, __init__, is_connected, process_order, calculate_tax)
    assert len(symbols) == 5

    # 3. Verify Class Symbol
    cls_sym = next(s for s in symbols if s.name == "OrderManager")
    assert cls_sym.symbol_type == SymbolType.CLASS
    assert cls_sym.scope_path == "OrderManager"
    assert cls_sym.docstring == "Manages order lifecycles and transactions."
    assert "class OrderManager:" in (cls_sym.signature or "")

    # 4. Verify Methods Hierarchical Scopes
    init_sym = next(s for s in symbols if s.name == "__init__")
    assert init_sym.symbol_type == SymbolType.METHOD
    assert init_sym.scope_path == "OrderManager.__init__"

    async_sym = next(s for s in symbols if s.name == "process_order")
    assert async_sym.symbol_type == SymbolType.METHOD
    assert async_sym.scope_path == "OrderManager.process_order"
    assert "async def process_order" in (async_sym.signature or "")
    assert async_sym.docstring == "Processes an order asynchronously."

    # 5. Verify Standalone Function
    func_sym = next(s for s in symbols if s.name == "calculate_tax")
    assert func_sym.symbol_type == SymbolType.FUNCTION
    assert func_sym.scope_path == "calculate_tax"
    assert "def calculate_tax" in (func_sym.signature or "")


def test_skeletal_chunker_class_and_methods(chunker: SkeletalChunker) -> None:
    result = chunker.chunk_file("services/order.py", SAMPLE_PYTHON_CODE)

    # 4 Chunks: 1 Class Outline + 3 Methods + 1 Standalone Function = 5 Chunks
    assert len(result.chunks) == 5

    # 1. Verify Class Outline Chunk
    outline_chunk = result.chunks[0]
    assert outline_chunk.symbols[0].name == "OrderManager"
    assert "import os" in outline_chunk.content
    assert "class OrderManager:" in outline_chunk.content
    assert '"""Manages order lifecycles and transactions."""' in outline_chunk.content
    # Sibling methods must be stubbed with ...
    assert "def __init__(self, db_url: str): ..." in outline_chunk.content
    assert "def is_connected(self) -> bool: ..." in outline_chunk.content
    assert (
        "async def process_order(self, order_id: str, items: List[str]) -> bool: ..."
        in outline_chunk.content
    )

    # 2. Verify Method Skeletal Chunk for process_order
    proc_chunk = next(
        c for c in result.chunks if c.symbols and c.symbols[0].name == "process_order"
    )
    assert "import os" in proc_chunk.content
    assert "class OrderManager:" in proc_chunk.content
    # Sibling methods must be stubbed
    assert "def __init__(self, db_url: str): ..." in proc_chunk.content
    assert "def is_connected(self) -> bool: ..." in proc_chunk.content
    # Target method body must be expanded
    assert "if not items:" in proc_chunk.content
    assert "return True" in proc_chunk.content


def test_skeletal_chunker_standalone_and_fallback(chunker: SkeletalChunker) -> None:
    # 1. Standalone function chunk
    result = chunker.chunk_file("services/order.py", SAMPLE_PYTHON_CODE)
    tax_chunk = next(c for c in result.chunks if c.symbols and c.symbols[0].name == "calculate_tax")
    assert "import os" in tax_chunk.content
    assert "def calculate_tax" in tax_chunk.content
    assert "return subtotal * rate" in tax_chunk.content

    # 2. Fallback for non-code / constants file
    config_code = 'APP_NAME = "AgentCore"\nDEBUG = True\nPORT = 8000\n'
    fallback_res = chunker.chunk_file("config.py", config_code)
    assert len(fallback_res.chunks) == 1
    assert fallback_res.chunks[0].start_line == 1
    assert fallback_res.chunks[0].end_line == 3
    assert fallback_res.chunks[0].content == config_code


def test_content_hash_deterministic() -> None:
    text_a = "def hello():\n    return 'world'\n"
    text_b = "def hello():\n    return 'world'\n"
    text_c = "def hello():\n    return 'world' \n"  # trailing space

    hash_a = compute_content_hash(text_a)
    hash_b = compute_content_hash(text_b)
    hash_c = compute_content_hash(text_c)

    assert len(hash_a) == 64  # SHA-256 hex length
    assert hash_a == hash_b
    assert hash_a != hash_c
