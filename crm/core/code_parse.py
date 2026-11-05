"""Parse source code with tree-sitter, imported on first use.

tree-sitter and its grammars are a runtime dependency of `crm inventory convert`
only (ADR 0034). Importing them at module load would add to the cold start of
every other command, so every import here sits inside a function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node

#: Grammar names `parse` accepts.
LANGUAGES = ("javascript", "c_sharp")


def parse(language: str, source: bytes) -> Node:
    """Return the root node of *source* parsed as *language* (one of `LANGUAGES`).

    tree-sitter recovers from syntax errors, so a broken file still yields a tree
    whose `error_count` is above zero.
    """
    from tree_sitter import Language, Parser

    if language == "javascript":
        import tree_sitter_javascript as grammar
    elif language == "c_sharp":
        import tree_sitter_c_sharp as grammar
    else:
        raise ValueError(f"unknown language {language!r}; expected one of {LANGUAGES}")
    return Parser(Language(grammar.language())).parse(source).root_node


def error_count(root: Node) -> int:
    """Count the error and missing nodes under *root*."""
    count = 0
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            count += 1
        stack.extend(node.children)
    return count
