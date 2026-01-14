# app/utils/compact_index.py
"""
Compact Index utilities for AI Code Agent.

Provides two types of compact representations:
1. File-level compact index (for Orchestrator) - shows project structure
2. Chunk-level list (for Pre-filter) - shows all classes/functions for selection
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, Any, List, Optional


def create_compact_index(index: Dict[str, Any]) -> str:
    """
    Creates a compact markdown representation of the project for Orchestrator.
    
    Shows project structure with file descriptions.
    Orchestrator uses this to:
    - Understand project structure
    - Find correct import paths
    - Navigate to other files if needed
    
    Args:
        index: Full semantic index (from semantic_index_builder)
        
    Returns:
        Markdown string with project map
    """
    files_data = index.get("files", {})
    total_files = len(files_data)
    total_tokens = index.get("total_tokens", 0)
    root_path = index.get("root_path", "N/A")
    
    lines = [
        f"# Project Map ({total_files} files, {total_tokens:,} tokens)",
        f"# Root: {root_path}",
        ""
    ]
    
    # Group files by directory
    by_dir: Dict[str, List[Dict]] = {}
    for file_path, file_data in files_data.items():
        dir_path = str(Path(file_path).parent)
        if dir_path == ".":
            dir_path = "(root)"
        if dir_path not in by_dir:
            by_dir[dir_path] = []
        by_dir[dir_path].append({
            "name": file_data.get("name", Path(file_path).name),
            "path": file_path,
            "tokens": file_data.get("tokens_total", 0),
            "description": file_data.get("description", "")
        })
    
    # Format output
    for dir_path in sorted(by_dir.keys()):
        files = by_dir[dir_path]
        lines.append(f"## {dir_path}/")
        
        for f in sorted(files, key=lambda x: x["name"]):
            desc = f["description"][:100] + "..." if len(f["description"]) > 100 else f["description"]
            lines.append(f"- `{f['name']}` ({f['tokens']} tok): {desc}")
        
        lines.append("")
    
    return "\n".join(lines)


def create_chunks_list_for_prefilter(index: Dict[str, Any]) -> str:
    """
    Creates a JSON list of all chunks (classes/functions) for Pre-filter.
    
    Pre-filter needs to select specific code chunks, not files.
    This function extracts all classes and functions with their metadata.
    
    Args:
        index: Full semantic index (from semantic_index_builder)
        
    Returns:
        JSON string with list of chunks
    """
    chunks = []
    
    files_data = index.get("files", {})
    
    for file_path, file_info in files_data.items():
        # Process classes
        for cls in file_info.get("classes", []):
            chunk_id = f"{file_path}:{cls['name']}"
            chunks.append({
                "chunk_id": chunk_id,
                "file": file_path,
                "type": "class",
                "name": cls["name"],
                "lines": cls.get("lines", ""),
                "tokens": cls.get("tokens", 0),
                "description": cls.get("description", ""),
                "methods": cls.get("methods", []),
                # Limit references to prevent huge output
                "references": cls.get("references", [])[:5]
            })
        
        # Process standalone functions
        for func in file_info.get("functions", []):
            chunk_id = f"{file_path}:{func['name']}"
            chunks.append({
                "chunk_id": chunk_id,
                "file": file_path,
                "type": "function",
                "name": func["name"],
                "lines": func.get("lines", ""),
                "tokens": func.get("tokens", 0),
                "description": func.get("description", ""),
                "references": func.get("references", [])[:5]
            })
    
    # Sort by file path and name for consistent output
    chunks.sort(key=lambda x: (x["file"], x["name"]))
    
    return json.dumps(chunks, indent=2, ensure_ascii=False)


def get_chunk_info_from_index(
    index: Dict[str, Any],
    chunk_id: str
) -> Optional[Dict[str, Any]]:
    """
    Retrieves chunk metadata from index by chunk_id.
    
    Args:
        index: Full semantic index
        chunk_id: Format "path/to/file.py:ClassName" or "path/to/file.py:function_name"
        
    Returns:
        Dict with chunk metadata or None if not found
    """
    if ":" not in chunk_id:
        return None
    
    file_path, element_name = chunk_id.rsplit(":", 1)
    
    # Handle nested names like "ClassName.method_name"
    name_parts = element_name.split(".")
    target_name = name_parts[0]  # Class or function name
    
    files_data = index.get("files", {})
    
    # Try exact match
    file_data = files_data.get(file_path)
    
    # Try variations if not found
    if not file_data:
        for fpath, fdata in files_data.items():
            if fpath.endswith(file_path) or file_path.endswith(fpath):
                file_data = fdata
                file_path = fpath
                break
    
    if not file_data:
        return None
    
    # Search in classes
    for cls in file_data.get("classes", []):
        if cls.get("name") == target_name:
            return {
                "file_path": file_path,
                "type": "class",
                "name": cls["name"],
                "lines": cls.get("lines", ""),
                "tokens": cls.get("tokens", 0),
                "description": cls.get("description", ""),
                "methods": cls.get("methods", []),
                "references": cls.get("references", []),
                "content_hash": cls.get("content_hash", ""),
            }
    
    # Search in functions
    for func in file_data.get("functions", []):
        if func.get("name") == target_name:
            return {
                "file_path": file_path,
                "type": "function",
                "name": func["name"],
                "lines": func.get("lines", ""),
                "tokens": func.get("tokens", 0),
                "description": func.get("description", ""),
                "references": func.get("references", []),
                "content_hash": func.get("content_hash", ""),
            }
    
    return None


def count_total_chunks(index: Dict[str, Any]) -> int:
    """
    Counts total number of chunks (classes + functions) in index.
    
    Args:
        index: Full semantic index
        
    Returns:
        Total chunk count
    """
    total = 0
    for file_data in index.get("files", {}).values():
        total += len(file_data.get("classes", []))
        total += len(file_data.get("functions", []))
    return total


def load_index_from_file(index_path: str) -> Optional[Dict[str, Any]]:
    """
    Loads semantic index from JSON file.
    
    Args:
        index_path: Path to semantic_index.json
        
    Returns:
        Parsed index dict or None if error
    """
    path = Path(index_path)
    if not path.exists():
        return None
    
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    
get_compact_index_for_orchestrator = create_compact_index