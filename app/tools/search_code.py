# app/tools/search_code.py
"""
Search Code Tool - Search for functions/classes in project index.

Features:
- Search by function/class/method name
- Returns file locations and line numbers
- Context snippets for each match
- XML-wrapped results
"""

from __future__ import annotations
import logging
import re
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """Single search result"""
    file_path: str
    name: str
    result_type: str  # "class", "function", "method"
    line_start: int
    line_end: int
    context: str  # Brief code context
    parent: Optional[str] = None  # Parent class for methods
    description: Optional[str] = None  # AI-generated description if available
    methods: List[str] = field(default_factory=list)  # Methods if this is a class


@dataclass
class SearchCodeResult:
    """Result of search_code operation"""
    success: bool
    query: str
    results: List[SearchResult] = field(default_factory=list)
    total_found: int = 0
    error: Optional[str] = None


def search_code_tool(
    query: str,
    index: Dict[str, Any],
    project_dir: str,
    search_type: str = "all",
    max_results: int = 20,
) -> str:
    """
    Search for code elements in project index.
    
    Searches through the semantic index to find:
    - Classes matching the query
    - Functions matching the query
    - Methods matching the query (with parent class info)
    
    Args:
        query: Name to search for (function, class, or method name)
        index: Project semantic index (regular or compressed format)
        project_dir: Path to project root
        search_type: "all", "class", "function", or "method"
        max_results: Maximum results to return
        
    Returns:
        XML-wrapped search results
    """
    logger.info(f"search_code_tool: Searching for '{query}' (type: {search_type})")
    
    if not query:
        return _format_error("query is required")
    
    if not index:
        return _format_error("No index available. Please index the project first.")
    
    # Normalize query
    query_lower = query.lower()
    
    # Use unified search function
    results = _search_unified_index(query_lower, index, search_type)
    
    # Limit results
    if len(results) > max_results:
        results = results[:max_results]
    
    # Format output
    if not results:
        return _format_no_results(query)
    
    return _format_results_xml(query, results)


def _search_unified_index(
    query: str,
    index: Dict[str, Any],
    search_type: str
) -> List[SearchResult]:
    """
    Unified search that works with both regular and compressed indices.
    
    Supports:
    - Regular index: {"files": {"path.py": {"classes": [...], "functions": [...]}}}
    - Compressed index: {"compressed": true, "classes": [...], "functions": [...]}
    """
    results = []
    is_compressed = index.get("compressed", False)
    
    if is_compressed:
        results = _search_compressed_index_unified(query, index, search_type)
    else:
        results = _search_regular_index_unified(query, index, search_type)
    
    return results


def _search_regular_index_unified(
    query: str,
    index: Dict[str, Any],
    search_type: str
) -> List[SearchResult]:
    """Search in regular (non-compressed) index format"""
    results = []
    
    files_data = index.get("files", {})
    
    for file_path, file_data in files_data.items():
        # Search classes
        if search_type in ("all", "class"):
            for cls in file_data.get("classes", []):
                cls_name = cls.get("name", "")
                if query in cls_name.lower():
                    lines = cls.get("lines", "0-0")
                    start, end = _parse_lines(lines)
                    
                    # Parse methods (can be strings or dicts)
                    methods_raw = cls.get("methods", [])
                    methods = _parse_methods_list(methods_raw)
                    
                    results.append(SearchResult(
                        file_path=file_path,
                        name=cls_name,
                        result_type="class",
                        line_start=start,
                        line_end=end,
                        context=f"class {cls_name}",
                        description=cls.get("description", ""),
                        methods=methods
                    ))
        
        # Search standalone functions
        if search_type in ("all", "function"):
            for func in file_data.get("functions", []):
                func_name = func.get("name", "")
                if query in func_name.lower():
                    lines = func.get("lines", "0-0")
                    start, end = _parse_lines(lines)
                    
                    results.append(SearchResult(
                        file_path=file_path,
                        name=func_name,
                        result_type="function",
                        line_start=start,
                        line_end=end,
                        context=f"def {func_name}(...)",
                        description=func.get("description", "")
                    ))
        
        # Search methods
        if search_type in ("all", "method"):
            for cls in file_data.get("classes", []):
                cls_name = cls.get("name", "")
                methods_raw = cls.get("methods", [])
                
                for method_item in methods_raw:
                    if isinstance(method_item, str):
                        method_name = method_item
                        method_lines = cls.get("lines", "0-0")
                    elif isinstance(method_item, dict):
                        method_name = method_item.get("name", "")
                        method_lines = method_item.get("lines", cls.get("lines", "0-0"))
                    else:
                        continue
                    
                    if query in method_name.lower():
                        start, end = _parse_lines(method_lines)
                        
                        results.append(SearchResult(
                            file_path=file_path,
                            name=method_name,
                            result_type="method",
                            line_start=start,
                            line_end=end,
                            context=f"def {method_name}(...) in class {cls_name}",
                            parent=cls_name,
                            description=method_item.get("description", "") if isinstance(method_item, dict) else ""
                        ))
    
    return results


def _search_compressed_index_unified(
    query: str,
    index: Dict[str, Any],
    search_type: str
) -> List[SearchResult]:
    """Search in compressed index format"""
    results = []
    
    # Search classes
    if search_type in ("all", "class"):
        for cls in index.get("classes", []):
            cls_name = cls.get("name", "")
            if query in cls_name.lower():
                lines = cls.get("lines", "0-0")
                start, end = _parse_lines(lines)
                
                # Parse methods from comma-separated string
                methods_str = cls.get("methods", "")
                methods = [m.strip() for m in methods_str.split(",") if m.strip()] if methods_str else []
                
                results.append(SearchResult(
                    file_path=cls.get("file", ""),
                    name=cls_name,
                    result_type="class",
                    line_start=start,
                    line_end=end,
                    context=f"class {cls_name}",
                    description=cls.get("description", ""),
                    methods=methods
                ))
    
    # Search functions
    if search_type in ("all", "function"):
        for func in index.get("functions", []):
            func_name = func.get("name", "")
            if query in func_name.lower():
                lines = func.get("lines", "0-0")
                start, end = _parse_lines(lines)
                
                results.append(SearchResult(
                    file_path=func.get("file", ""),
                    name=func_name,
                    result_type="function",
                    line_start=start,
                    line_end=end,
                    context=f"def {func_name}(...)",
                    description=func.get("description", "")
                ))
    
    # Search methods (in compressed index, methods are stored as strings in class)
    if search_type in ("all", "method"):
        for cls in index.get("classes", []):
            cls_name = cls.get("name", "")
            methods_str = cls.get("methods", "")
            
            if methods_str:
                methods = [m.strip() for m in methods_str.split(",") if m.strip()]
                for method_name in methods:
                    if query in method_name.lower():
                        lines = cls.get("lines", "0-0")
                        start, end = _parse_lines(lines)
                        
                        results.append(SearchResult(
                            file_path=cls.get("file", ""),
                            name=method_name,
                            result_type="method",
                            line_start=start,
                            line_end=end,
                            context=f"def {method_name}(...) in class {cls_name}",
                            parent=cls_name,
                            description=None  # No method descriptions in compressed index
                        ))
    
    return results


def _parse_methods_list(methods_raw) -> List[str]:
    """Parse methods from various formats (list of strings, list of dicts, or string)"""
    if isinstance(methods_raw, str):
        # Comma-separated string (compressed index)
        return [m.strip() for m in methods_raw.split(",") if m.strip()]
    elif isinstance(methods_raw, list):
        methods = []
        for item in methods_raw:
            if isinstance(item, str):
                methods.append(item)
            elif isinstance(item, dict):
                name = item.get("name", "")
                if name:
                    methods.append(name)
        return methods
    return []


def _parse_lines(lines_input) -> tuple:
    """
    Parse lines information from various formats.
    
    Supports:
    - String: "10-25", "10"
    - Integer: 10
    - Dict: {"start": 10, "end": 25} or {"line_start": 10, "line_end": 25}
    """
    try:
        if isinstance(lines_input, str):
            if "-" in lines_input:
                parts = lines_input.split("-")
                return int(parts[0]), int(parts[1])
            else:
                line_num = int(lines_input)
                return line_num, line_num
        elif isinstance(lines_input, int):
            return lines_input, lines_input
        elif isinstance(lines_input, dict):
            # Try different key formats
            if "start" in lines_input and "end" in lines_input:
                return int(lines_input["start"]), int(lines_input["end"])
            elif "line_start" in lines_input and "line_end" in lines_input:
                return int(lines_input["line_start"]), int(lines_input["line_end"])
            else:
                # Try to find any numeric values
                for key, value in lines_input.items():
                    if isinstance(value, (int, str)):
                        try:
                            line_num = int(value)
                            return line_num, line_num
                        except (ValueError, TypeError):
                            continue
        return 0, 0
    except (ValueError, TypeError, AttributeError) as e:
        logger.warning(f"Failed to parse lines '{lines_input}': {e}")
        return 0, 0


def _format_results_xml(query: str, results: List[SearchResult]) -> str:
    """Format search results as XML"""
    parts = []
    
    # Header
    parts.append(f'<!-- Search results for: "{query}" -->')
    parts.append(f'<!-- Found: {len(results)} matches -->')
    parts.append("")
    parts.append(f'<search_results query="{_escape_xml(query)}" count="{len(results)}">')
    
    # Group by file
    by_file: Dict[str, List[SearchResult]] = {}
    for result in results:
        if result.file_path not in by_file:
            by_file[result.file_path] = []
        by_file[result.file_path].append(result)
    
    for file_path, file_results in by_file.items():
        parts.append(f'  <file path="{_escape_xml(file_path)}">')
        
        for r in file_results:
            attrs = [
                f'type="{r.result_type}"',
                f'name="{_escape_xml(r.name)}"',
                f'lines="{r.line_start}-{r.line_end}"',
            ]
            
            if r.parent:
                attrs.append(f'parent="{_escape_xml(r.parent)}"')
            
            parts.append(f'    <result {" ".join(attrs)}>')
            parts.append(f'      <context>{_escape_xml(r.context)}</context>')
            
            if r.description:
                parts.append(f'      <description>{_escape_xml(r.description)}</description>')
            
            # Add methods if this is a class
            if r.result_type == "class" and r.methods:
                methods_str = ", ".join(r.methods[:5])  # Show first 5 methods
                if len(r.methods) > 5:
                    methods_str += f" ... (+{len(r.methods) - 5} more)"
                parts.append(f'      <methods>{_escape_xml(methods_str)}</methods>')
            
            parts.append(f'    </result>')
        
        parts.append(f'  </file>')
    
    parts.append('</search_results>')
    
    return "\n".join(parts)


def _format_no_results(query: str) -> str:
    """Format message when no results found"""
    return f"""<!-- Search results for: "{query}" -->
<!-- Found: 0 matches -->

<search_results query="{_escape_xml(query)}" count="0">
  <message>No results found for "{_escape_xml(query)}"</message>
  <suggestions>
    <suggestion>Try a partial name (e.g., "auth" instead of "authenticate")</suggestion>
    <suggestion>Check spelling</suggestion>
    <suggestion>Use search_type="all" to search classes, functions, and methods</suggestion>
    <suggestion>The index might be out of date. Try re-indexing the project.</suggestion>
  </suggestions>
</search_results>"""


def _format_error(message: str) -> str:
    """Format error message"""
    return f"""<!-- ERROR -->
<error>
  <message>{_escape_xml(message)}</message>
</error>"""


def _escape_xml(text: str) -> str:
    """Escape special XML characters"""
    if text is None:
        return ""
    
    text = str(text)
    # Escape in the correct order: & first
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    text = text.replace('"', '&quot;')
    text = text.replace("'", '&apos;')
    return text


# Convenience function for backward compatibility
def search_code_tool_legacy(
    query: str,
    index: Dict[str, Any],
    project_dir: str,
    search_type: str = "all",
    max_results: int = 20,
) -> str:
    """Legacy interface for backward compatibility"""
    return search_code_tool(query, index, project_dir, search_type, max_results)