"""Code commenter service for commenting out single methods/functions/classes."""

# Standard library
import ast
import logging
import os
from typing import Optional, Tuple, Any

# Project imports
from app.services.tree_sitter_parser import MultiLanguageParser

logger = logging.getLogger(__name__)

# Map file extension -> single-line comment prefix
_LANG_COMMENT_PREFIX = {
    ".py": "#",
    ".java": "//",
    ".js": "//",
    ".jsx": "//",
    ".mjs": "//",
    ".ts": "//",
    ".tsx": "//",
    ".go": "//",
}

# Map file extension -> tree-sitter language name
_EXT_TO_TS_LANG = {
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
}


def _comment_block(lines: list, start_idx: int, end_idx: int, prefix: str) -> list:
    """Return a NEW list of lines where lines[start_idx:end_idx] (0-based, end exclusive) are each prefixed with the comment prefix, preserving original indentation. All other lines are left untouched."""
    result = list(lines)
    
    # Clamp indices
    start_idx = max(0, start_idx)
    end_idx = min(len(result), end_idx)
    
    if start_idx >= end_idx:
        return result
    
    # Comment out each line in the range
    for i in range(start_idx, end_idx):
        original = result[i]
        stripped = original.lstrip()
        indent = original[:len(original) - len(stripped)]
        
        if stripped == "":
            # Leave blank lines as-is to preserve spacing
            result[i] = original
        else:
            result[i] = f"{indent}{prefix} {stripped}"
    
    return result


def _find_python_span_ast(content: str, target_name: str, parent_class: Optional[str]) -> Optional[Tuple[int, int]]:
    """Use the Python ast module to find the exact 1-based inclusive line span of a function/method/class. Returns (start_line, end_line) 1-based inclusive, or None if not found or file cannot be parsed. Uses node.lineno and node.end_lineno (Python 3.8+) for precise, bounded ranges that never extend past the element."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        logger.debug(f"Failed to parse Python file for target '{target_name}'; falling back to Tree-sitter")
        return None
    except Exception as e:
        logger.warning(f"Unexpected error parsing Python AST for '{target_name}': {e}")
        return None
    
    def node_span(node):
        """Extract 1-based inclusive line span from AST node."""
        start = node.lineno
        end = getattr(node, "end_lineno", None)
        if end is None:
            return None
        return (start, end)
    
    # If parent_class is specified, search within that class
    if parent_class:
        for cls in ast.walk(tree):
            if isinstance(cls, ast.ClassDef) and cls.name == parent_class:
                for item in cls.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == target_name:
                        return node_span(item)
        return None
    
    # Search for top-level function/class first
    for item in tree.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and item.name == target_name:
            return node_span(item)
    
    # Fallback: search entire tree (for nested classes, etc.)
    for item in ast.walk(tree):
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and item.name == target_name:
            return node_span(item)
    
    return None


def _find_span_tree_sitter(ml_parser: Any, content: str, ts_lang: str, target_name: str, target_type: str, parent_class: Optional[str]) -> Optional[Tuple[int, int]]:
    """Use the project's MultiLanguageParser (Tree-sitter) to locate the named element and return its 1-based inclusive line span (start_line, end_line). Returns None if the parser cannot locate the element. The end line is taken from the matched node boundary and is the element's true end, never EOF."""
    if ml_parser is None:
        return None
    
    try:
        # Try with element_type parameter first
        try:
            element = ml_parser.find_element(content, ts_lang, target_name, element_type=target_type)
        except TypeError:
            # Fallback if signature doesn't support element_type
            element = ml_parser.find_element(content, ts_lang, target_name)
        
        if not element:
            return None
        
        # Extract line span from element
        # Element should have start_line and end_line attributes (1-based)
        start_line = getattr(element, "start_line", None)
        end_line = getattr(element, "end_line", None)
        
        if start_line is None or end_line is None:
            logger.debug(f"Tree-sitter element for '{target_name}' missing line information")
            return None
        
        return (start_line, end_line)
    
    except Exception as e:
        logger.debug(f"Tree-sitter lookup failed for '{target_name}' in {ts_lang}: {e}")
        return None


def comment_out_element(
    file_path: str,
    file_content: str,
    element_name: str,
    element_type: str = "function",
    parent_class: Optional[str] = None,
    ml_parser: Optional[Any] = None,
) -> Tuple[bool, str, str]:
    """Comment out a single named element (method/function/class) in the given file content.
    
    Args:
        file_path: Full path to the file (used to determine language).
        file_content: The complete file content as a string.
        element_name: Name of the function/method/class to comment out.
        element_type: Type of element ("function", "method", "class"). Default: "function".
        parent_class: If element is a method, the name of the containing class. Default: None.
        ml_parser: Optional MultiLanguageParser instance for Tree-sitter fallback.
    
    Returns:
        Tuple of (success: bool, new_content: str, error_message: str).
        - If success=True: new_content contains the file with the element commented out; error_message is "".
        - If success=False: new_content is the original file_content; error_message describes the failure.
    """
    # Determine file extension
    _, ext = os.path.splitext(file_path)
    ext = ext.lower()
    
    # Get comment prefix
    comment_prefix = _LANG_COMMENT_PREFIX.get(ext)
    if not comment_prefix:
        return False, file_content, f"Unsupported file extension: {ext}"
    
    # Find the element's line span
    span = None
    
    if ext == ".py":
        # Try Python AST first
        span = _find_python_span_ast(file_content, element_name, parent_class)
        
        # Fallback to Tree-sitter if AST fails
        if span is None and ml_parser is not None:
            span = _find_span_tree_sitter(ml_parser, file_content, "python", element_name, element_type, parent_class)
    else:
        # Use Tree-sitter for non-Python languages
        ts_lang = _EXT_TO_TS_LANG.get(ext)
        if ts_lang is None:
            return False, file_content, f"No Tree-sitter language mapping for {ext}"
        
        span = _find_span_tree_sitter(ml_parser, file_content, ts_lang, element_name, element_type, parent_class)
    
    if span is None:
        return False, file_content, f"Could not locate {element_type} '{element_name}' in {file_path}"
    
    start_line, end_line = span
    
    # Convert 1-based inclusive line numbers to 0-based indices for list slicing
    # start_line (1-based) -> start_idx (0-based) = start_line - 1
    # end_line (1-based inclusive) -> end_idx (0-based exclusive) = end_line
    start_idx = start_line - 1
    end_idx = end_line
    
    # Split content into lines (preserving line endings for reconstruction)
    lines = file_content.splitlines(keepends=True)
    
    # Comment out the lines
    commented_lines = _comment_block(lines, start_idx, end_idx, comment_prefix)
    
    # Reconstruct file content
    new_content = "".join(commented_lines)
    
    logger.info(f"Commented out {element_type} '{element_name}' in {file_path} (lines {start_line}-{end_line})")
    
    return True, new_content, ""